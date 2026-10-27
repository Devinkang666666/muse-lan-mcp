#!/usr/bin/env python3
"""Muse 專用居域網檢查 MCP 伺服器（重建版，不依賴 Hark 原檔）.

"version": "1.2.0"
放開版（使用者 2026-10-27 親定：Hark 太保守，Muse 可以放開做）：
    http_post 可跟隨跳轉、可選回 Cookie 值、可接受解析到內網的主機名；
    mqtt_probe 恢復訂閱抓主題；tcp_ports 上限放寬到 200。
1.2.0 新增 auth_fetch（使用者 2026-10-27 親定：登入後要能看所有資訊，
只登入不給看就失去意義）：
    在同一次呼叫內先 POST 登入，再用登入 Cookie 做一次唯讀 GET，
    Cookie 只在伺服器內部用，不回值、不存檔、不改設定。
唯一保留的硬線：只接內網。因為伺服器經 ngrok 對外，Bearer token
一旦外洩，放開到公網就等於把伺服器變成公開代理，這不是保守，
是會反傷使用者自己的線，所以不放。
來源：按 2026-10-27 實測的原伺服器 tools/list 與各工具行為重建，
原 14 個工具全保留，新增第 15 個工具 http_post（單次 POST 登入用）。
標準庫 only，不用 pip 裝任何東西，手機 Termux Python 3.12+ 可直接跑。

啟動（手機 Termux，一次一條）：
    MUSE_TOKEN="$(cat ~/.muse_token)" nohup python ~/muse_lan_mcp.py > ~/mcp.log 2>&1 &
    sleep 2; tail -n 20 ~/mcp.log
預設監聽 0.0.0.0:8765，路徑 /mcp。
認證：若環境變數 MUSE_TOKEN 有值，必須帶
    Authorization: Bearer <MUSE_TOKEN>
    否則回 401；若未設 MUSE_TOKEN 則不驗證（僅限本機測試用，
    經 ngrok 對外時務必設 token）。

安全線（http_post 專用，也是本檔的硬線）：
    - 一次只送一個 POST，不循環、不在伺服器裡猜密碼。
    - 只接受內網 IP 字面，公網 IP 與主機名一律拒絕。
    - 不跟隨跳轉，302 + Location 本身就是成功信號。
    - 不回 Cookie 值、不回送出的密碼，只回 Cookie 名稱與欄位名。
"""
import hashlib
import http.client
import http.server
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode, urlparse

VERSION = "1.4.0"
PORT = int(os.environ.get("PORT", "8765"))
TOKEN = os.environ.get("MUSE_TOKEN", "")
DEFAULT_PORTS = [21, 22, 23, 53, 80, 139, 443, 445, 554, 1883, 3389,
                 5000, 5353, 7000, 8000, 8008, 8009, 8080, 8443, 8888, 9100, 62078]


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


def strip_script_style(text):
    text = re.sub(r"(?is)<script.*?</script>", "", text)
    text = re.sub(r"(?is)<style.*?</style>", "", text)
    return text


def title_of(text):
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", text)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def mask_passwords(text):
    # 只遮「密碼值」，不遮 JS 函數名：值必須是引號包住、且不含 document/getElement
    def repl(m):
        val = m.group(2)
        if "document" in val or "getElement" in val or "." in val:
            return m.group(0)
        masked = val[0] + "*" * max(0, len(val) - 2) + val[-1] if len(val) >= 2 else "*"
        return m.group(1) + masked + m.group(3)
    return re.sub(r"(?i)(password[^=\n]{0,20}=[\"'])([^\"']+)([\"'])", repl, text)


def tcp_check(host, port, timeout=1.0):
    """回傳 open / closed / filtered。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        rc = s.connect_ex((host, port))
        s.close()
        if rc == 0:
            return "open"
        if rc in (111, 61):  # ECONNREFUSED
            return "closed"
        return "filtered"
    except socket.timeout:
        return "filtered"
    except Exception:
        return "filtered"


def http_get(host, port, path, timeout=5):
    try:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", path, headers={"User-Agent": "muse-lan-mcp/1.0"})
        r = conn.getresponse()
        raw = r.read(16384).decode("utf-8", "ignore")
        headers = {k: v for k, v in r.getheaders()}
        conn.close()
        return {"port": port, "path": path, "status": r.status,
                "server": r.getheader("Server", ""), "title": title_of(raw),
                "www_authenticate": r.getheader("WWW-Authenticate", ""),
                "location": r.getheader("Location", ""),
                "content_type": r.getheader("Content-Type", ""),
                "set_cookie": r.getheader("Set-Cookie", ""),
                "headers_all": headers,
                "body_snippet": mask_passwords(strip_script_style(raw))[:2000]}
    except Exception as e:
        return {"port": port, "path": path, "status": 0, "server": "",
                "title": "", "www_authenticate": "", "location": "",
                "content_type": "", "set_cookie": "", "headers_all": {},
                "body_snippet": "", "error": str(e)}


def exposed_secrets_from(body):
    """只認真正的密碼值，JS 函數名不算，避免原版 .1 誤報重演。"""
    out = []
    for m in re.finditer(r"(?i)password[^=\n]{0,30}=[\"']([^\"']+)[\"']", body):
        val = m.group(1)
        if "document" in val or "getElement" in val or val.startswith("function"):
            continue
        out.append({"field": "password", "len": len(val),
                    "value_masked": val[0] + "*" * max(0, len(val) - 2) + val[-1] if len(val) >= 2 else "*"})
    return out


# ---- 工具本體 ----
def device_info(_a):
    import platform
    return {"os": f"{platform.system()}-{platform.release()}-{platform.machine()}",
            "hostname": socket.gethostname(), "local_ip": local_ip(),
            "python": sys.version.split()[0], "muse_lan_mcp_version": VERSION}


def lan_scan(a):
    subnet = a.get("subnet") or ""
    ports = a.get("ports") or DEFAULT_PORTS
    if not subnet:
        base = ".".join(local_ip().split(".")[:3]) + ".0/24"
        subnet = base
    try:
        net = ipaddress.ip_network(subnet, strict=False)
    except Exception as e:
        return {"subnet": subnet, "error": str(e), "devices": []}
    hosts = [str(h) for h in net.hosts()]
    if len(hosts) > 256:
        hosts = hosts[:256]
    devices = []

    def check_host(host):
        opens, rst = [], False
        for p in ports:
            st = tcp_check(host, p, timeout=0.8)
            if st == "open":
                opens.append(p)
            elif st == "closed":
                rst = True
        if opens:
            guess = []
            if 554 in opens:
                guess.append("camera_or_nvr_suspect")
            if 8008 in opens or 8009 in opens:
                guess.append("cast_tv_suspect")
            return {"ip": host, "name": "", "open_ports": opens, "alive_by": "open_port", "mac": "",
                    "device_guess": guess}
        if rst:
            return {"ip": host, "name": "", "open_ports": [], "alive_by": "tcp_rst", "mac": "",
                    "device_guess": []}
        return None

    with ThreadPoolExecutor(max_workers=50) as ex:
        for dev in ex.map(check_host, hosts):
            if dev:
                devices.append(dev)
    devices.sort(key=lambda d: ipaddress.ip_address(d["ip"]))
    return {"subnet": str(net), "ports_checked": ports, "count": len(devices),
            "devices": devices,
            "note": "alive_by：open_port＝有開埠；tcp_rst＝在線但埠全關；arp＝只在 ARP 表看到"}


def ping(a):
    host, count = a["host"], int(a.get("count", 4))
    try:
        r = subprocess.run(["ping", "-c", str(count), "-W", "2", host],
                           capture_output=True, text=True, timeout=20)
        return {"host": host, "count": count, "alive": r.returncode == 0,
                "output": (r.stdout or r.stderr)[:1000]}
    except Exception as e:
        return {"host": host, "count": count, "alive": False, "error": str(e)}


def tcp_ports(a):
    host, ports = a["host"], (a.get("ports") or [])[:200]
    out = {}
    for p in ports:
        out[str(p)] = tcp_check(host, int(p), timeout=2.0)
    return {"host": host, "ports": out}


def banner(a):
    host, ports = a["host"], a.get("ports") or []
    out = {}
    for p in ports:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect((host, int(p)))
            try:
                data = s.recv(1024)
            except Exception:
                data = b""
            s.close()
            out[str(p)] = data.decode("utf-8", "ignore")
        except Exception as e:
            out[str(p)] = ""
    return out


def web_audit(a):
    host, ports, paths = a["host"], (a.get("ports") or [80]), (a.get("paths") or ["/"])
    web = []
    for p in ports:
        for path in paths:
            g = http_get(host, int(p), path)
            web.append({"port": int(p), "server": g["server"], "title": g["title"],
                        "exposed_secrets": [{"path": path, "status": g["status"],
                                             "field": s["field"], "len": s["len"],
                                             "value_masked": s["value_masked"]}
                                            for s in exposed_secrets_from(g["body_snippet"])]})
    return {"host": host, "web": web,
            "note": "exposed_secrets＝未登入即在頁面原始碼看到的密碼樣式（已遮罩）。請自行在瀏覽器核對；這通常是嚴重設定問題。"}


def audit_host(a):
    host = a["host"]
    ports = sorted(set((a.get("ports") or []) + DEFAULT_PORTS + [9000]))
    opens, banners = [], {}
    for p in ports:
        st = tcp_check(host, p, timeout=1.0)
        if st == "open":
            opens.append(p)
    banners = banner({"host": host, "ports": opens})
    web, flags = [], []
    for p in opens:
        if p in (80, 8080, 8443, 8000, 8888):
            g = http_get(host, p, "/")
            secs = exposed_secrets_from(g["body_snippet"])
            web.append({"port": p, "server": g["server"], "title": g["title"],
                        "exposed_secrets": [{"path": "/", "status": g["status"],
                                             "field": s["field"], "len": s["len"],
                                             "value_masked": s["value_masked"]} for s in secs]})
            if secs:
                flags.append("管理頁面原始碼外洩密碼（見 web.exposed_secrets）")
        else:
            web.append({"port": p, "server": "", "title": "", "exposed_secrets": []})
    guess = []
    joined = json.dumps(web, ensure_ascii=False)
    if "ZTE" in joined:
        guess.append("ZTE 路由器/光貓類")
    if "Tenda" in joined:
        guess.append("Tenda 路由器類")
    if "中国移动" in joined or "中國移動" in joined:
        guess.append("中國移動設備類")
    return {"host": host, "open_ports": opens, "banners": banners, "web": web,
            "device_guess": guess, "risk_flags": flags, "default_credential_hints": {},
            "note": "default_credential_hints 僅供你自行核對，工具不會自動嘗試登入或猜密碼。"}


def _mqtt_remaining(n):
    out = b""
    while True:
        b = n % 128
        n //= 128
        if n:
            out += bytes([b | 0x80])
        else:
            out += bytes([b])
            break
    return out


def mqtt_probe(a):
    host, port = a["host"], int(a.get("port", 1883))
    listen, max_topics = float(a.get("listen", 5)), int(a.get("max_topics", 20))
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((host, port))
        payload = b"\x00\x04MQTT\x04\x02\x00\x0a" + b"\x00\x0bmuse-probe"
        s.sendall(b"\x10" + bytes([len(payload)]) + payload)
        data = s.recv(4)
        if not (len(data) >= 4 and data[0] == 0x20 and data[3] == 0x00):
            s.close()
            if len(data) >= 4 and data[3] == 0x05:
                return {"host": host, "port": port, "anonymous_allowed": False,
                        "needs_auth": True, "topics": []}
            return {"host": host, "port": port, "anonymous_allowed": False,
                    "needs_auth": None, "topics": [], "raw_connack": data.hex()}
        # SUBSCRIBE to # : packet id 1, topic "#" QoS 0
        sub = b"\x00\x01\x00\x01\x23\x00"
        s.sendall(b"\x82" + _mqtt_remaining(len(sub)) + sub)
        topics, buf = [], b""
        s.settimeout(1)
        end = time.time() + listen
        while time.time() < end and len(topics) < max_topics:
            try:
                chunk = s.recv(4096)
            except socket.timeout:
                continue
            except Exception:
                break
            if not chunk:
                break
            buf += chunk
            while len(buf) >= 2:
                ptype, idx = buf[0], 1
                mult, rem = 1, 0
                while True:
                    if idx >= len(buf):
                        break
                    b = buf[idx]
                    rem += (b & 0x7F) * mult
                    mult *= 128
                    idx += 1
                    if not (b & 0x80):
                        break
                if idx + rem > len(buf):
                    break
                pkt_body = buf[idx:idx + rem]
                buf = buf[idx + rem:]
                if (ptype & 0xF0) == 0x30 and len(pkt_body) >= 2:
                    tlen = (pkt_body[0] << 8) | pkt_body[1]
                    topic = pkt_body[2:2 + tlen].decode("utf-8", "ignore")
                    if topic and topic not in topics:
                        topics.append(topic)
        s.close()
        return {"host": host, "port": port, "anonymous_allowed": True,
                "needs_auth": False, "topics": topics,
                "note": "只訂閱、不發布；主題只回名稱，不回訊息內容。"}
    except Exception as e:
        return {"host": host, "port": port, "error": str(e)}


def web_cred_context(a):
    host, port, paths = a["host"], int(a.get("port", 80)), (a.get("paths") or ["/"])
    ctx = int(a.get("context", 200))
    matches = []
    for path in paths:
        g = http_get(host, port, path)
        body = g["body_snippet"]
        for m in re.finditer(r"(?i)password", body):
            start, end = max(0, m.start() - ctx), min(len(body), m.end() + ctx)
            snippet = body[start:end]
            val_m = re.search(r"(?i)password[^=\n]{0,30}=[\"']([^\"']+)[\"']", snippet)
            val = val_m.group(1) if val_m else ""
            if val and ("document" in val or "getElement" in val):
                masked, likely = val[0] + "*" * max(0, len(val) - 2) + val[-1], True
            elif val:
                masked, likely = val[0] + "*" * max(0, len(val) - 2) + val[-1], False
            else:
                masked, likely = "", ("function" in snippet or "document" in snippet)
            matches.append({"path": path, "field": "password", "len": len(val),
                            "value_masked": masked, "snippet": snippet,
                            "likely_js_or_template": likely})
    return {"host": host, "port": port, "matches": matches,
            "note": "snippet 為命中前後文(值已遮罩)。likely_js_or_template=true 代表周圍像程式碼/模板而非真實憑證。僅供人工判讀。"}


def http_probe(a):
    host, ports, paths = a["host"], (a.get("ports") or [80]), (a.get("paths") or ["/"])
    probes = []
    for p in ports:
        for path in paths:
            probes.append(http_get(host, int(p), path))
    return {"host": host, "probes": probes,
            "note": "body_snippet 已移除 script/style 並遮罩疑似密碼；唯讀，不登入。"}


def raw_probe(a):
    host, port = a["host"], int(a["port"])
    results = []
    probes = [("idle", b""), ("crlf", b"\r\n"),
              ("http", b"GET / HTTP/1.0\r\n\r\n"),
              ("rtsp", b"OPTIONS * RTSP/1.0\r\nCSeq: 1\r\n\r\n")]
    for name, data in probes:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect((host, port))
            if data:
                s.sendall(data)
            try:
                chunk = s.recv(160)
            except Exception:
                chunk = b""
            s.close()
            results.append({"probe": name, "bytes": len(chunk), "hex": chunk.hex(),
                            "text": chunk.decode("utf-8", "ignore"), "error": ""})
        except Exception as e:
            results.append({"probe": name, "bytes": 0, "hex": "", "text": "", "error": str(e)})
    return {"host": host, "port": port, "results": results}


def ssdp_probe(a):
    import xml.etree.ElementTree as ET
    st = a.get("st") or "ssdp:all"
    timeout = float(a.get("timeout", 3))
    host_filter = a.get("host") or ""
    msg = ("\r\n".join(["M-SEARCH * HTTP/1.1", "HOST: 239.255.255.250:1900",
                        'MAN: "ns=01"', f"MX: 2", f"ST: {st}", "", ""])).encode()
    out = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.settimeout(timeout)
        s.sendto(msg, ("239.255.255.250", 1900))
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, addr = s.recvfrom(65535)
            except socket.timeout:
                break
            if host_filter and addr[0] != host_filter:
                continue
            txt = data.decode("utf-8", "ignore")
            headers = {}
            for line in txt.splitlines()[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            item = {"host": addr[0], "st": headers.get("st", ""), "usn": headers.get("usn", ""),
                    "server": headers.get("server", ""), "location": headers.get("location", ""),
                    "friendlyName": "", "manufacturer": "", "modelName": ""}
            loc = headers.get("location", "")
            if loc:
                try:
                    u = urlparse(loc)
                    g = http_get(u.hostname, u.port or 80, u.path or "/")
                    if "<" in g["body_snippet"]:
                        root = ET.fromstring(re.sub(r"<\?xml[^?]*\?>", "", g["body_snippet"])[:4000])
                        # 命名空間不確定，暴力找標籤尾名
                        for el in root.iter():
                            tag = el.tag.split("}")[-1]
                            if tag in item and el.text:
                                item[tag] = el.text.strip()
                except Exception:
                    pass
            out.append(item)
        s.close()
    except Exception as e:
        return {"devices": [], "error": str(e)}
    return {"devices": out}


def mdns_probe(a):
    # 最小實作：發 _services 查詢，收集回包中的可讀服務名；空結果不代表無設備。
    name = a.get("name") or "_services._dns-sd._udp.local"
    timeout = float(a.get("timeout", 3))
    host_filter = a.get("host") or ""
    try:
        # DNS 查詢封包：ID 0, flags 0, QDCOUNT 1
        def enc(n):
            out = b""
            for part in n.split("."):
                out += bytes([len(part)]) + part.encode()
            return out + b"\x00"
        q = b"\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00" + enc(name) + b"\x00\x0c\x00\x01"
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.settimeout(timeout)
        s.sendto(q, ("224.0.0.251", 5353))
        services, seen = [], set()
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, addr = s.recvfrom(65535)
            except socket.timeout:
                break
            if host_filter and addr[0] != host_filter:
                continue
            txt = "".join(chr(b) if 32 <= b < 127 else " " for b in data)
            for m in re.finditer(r"_[A-Za-z0-9\-]+\._(tcp|udp)\.local", txt):
                if m.group(0) not in seen:
                    seen.add(m.group(0))
                    services.append({"host": addr[0], "service": m.group(0)})
        s.close()
        return {"services": services}
    except Exception as e:
        return {"services": [], "error": str(e)}


def mac_of(host):
    try:
        if os.path.exists("/proc/net/arp"):
            for line in open("/proc/net/arp"):
                parts = line.split()
                if len(parts) >= 4 and parts[0] == host:
                    return parts[3]
    except Exception:
        pass
    try:
        r = subprocess.run(["ip", "neigh", "show", host], capture_output=True, text=True, timeout=5)
        m = re.search(r"lladdr\s+([0-9a-fA-F:]{17})", r.stdout)
        if m:
            return m.group(1)
    except Exception:
        pass
    return ""


def guess_device(host, opens, http_list, ssdp, mdns):
    """Port + title + service based guess, like the screenshot app.

    A guess is only a guess: 554 alone is not proof of a camera,
    8008/8009 alone is not proof of a Cast TV. The guess is returned
    with its evidence so the caller can judge, never as a confirmed
    identity. Read-only.
    """
    guesses = []
    titles = " ".join((h.get("title") or "") for h in http_list).lower()
    services = " ".join(
        str(s.get("name", "")) + " " + str(s.get("type", "")) for s in (ssdp + mdns)).lower()
    if "wmc180" in titles or "wireless router" in titles:
        guesses.append({"guess": "router", "evidence": "http title"})
    if 554 in opens:
        guesses.append({"guess": "camera_or_nvr_suspect", "evidence": "port 554 open (RTSP)"})
    if 8008 in opens or 8009 in opens or "googlecast" in services or "chromecast" in services:
        guesses.append({"guess": "cast_tv_suspect", "evidence": "port 8008/8009 or googlecast service"})
    if "dlna" in services or "mediaserver" in services:
        guesses.append({"guess": "media_server_suspect", "evidence": "ssdp/dlna service"})
    if 80 in opens and not guesses:
        guesses.append({"guess": "web_device", "evidence": "port 80 open"})
    return guesses


def identify_host(a):
    host = a["host"]
    # Cast ports were missing from the old scan list, so a Cast TV
    # could be missed entirely. Add them here for identification.
    ports = (a.get("ports") or []) + DEFAULT_PORTS + [9000, 8009, 8443]
    opens = [p for p in sorted(set(ports)) if tcp_check(host, p, timeout=1.0) == "open"]
    http_list = []
    for p in opens:
        if p in (80, 8080, 8443, 8000, 8008, 8888, 9000):
            for path in (a.get("paths") or ["/"]):
                http_list.append(http_get(host, p, path))
    # mDNS/SSDP with a longer timeout and several service types:
    # the old single short query returned empty even for a Cast TV.
    ssdp = ssdp_probe({"host": host, "timeout": 3}).get("devices", [])
    mdns = mdns_probe({"host": host, "timeout": 3}).get("services", [])
    try:
        hostname, _, _ = socket.gethostbyaddr(host)
    except Exception:
        hostname = ""
    mac = mac_of(host)
    return {"host": host, "hostname": hostname, "mac": mac, "mac_vendor": "",
            "open_ports": opens, "http": http_list, "ssdp": ssdp, "mdns": mdns,
            "device_guess": guess_device(host, opens, http_list, ssdp, mdns),
            "note": "綜合裝置自報資訊與 HTTP 指紋；唯讀。device_guess 只是猜測，需以服務名/標題為證據判斷。"}


def http_post(a):
    host_in, path = a["host"], a.get("path", "/")
    port, form = int(a.get("port", 80)), (a.get("form") or {})
    use_https = bool(a.get("use_https", False))
    follow = bool(a.get("follow_redirects", False))
    ret_cookies = bool(a.get("return_cookies", False))
    # 放開點：主機名可以，但解析後必須是內網 IP，否則拒絕。
    try:
        ip = ipaddress.ip_address(host_in)
        connect_host, resolved = host_in, str(ip)
    except ValueError:
        try:
            resolved = socket.gethostbyname(host_in)
            ip = ipaddress.ip_address(resolved)
            connect_host = resolved
        except Exception as e:
            return {"host": host_in, "error": f"主機名解析失敗：{e}"}
    if not (ip.is_private or ip.is_loopback):
        return {"host": host_in, "resolved_ip": resolved, "error": "只允許內網 IP，拒絕公網位址"}
    if not path.startswith("/"):
        path = "/" + path
    body = urlencode(form) if form else ""
    headers = {}
    if form:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if connect_host != host_in:
        headers["Host"] = host_in
    try:
        cls = http.client.HTTPSConnection if use_https else http.client.HTTPConnection
        conn = cls(connect_host, port, timeout=10)
        conn.request("POST", path, body=body, headers=headers)
        r = conn.getresponse()
        raw = r.read(16384).decode("utf-8", "ignore")
        cookies = [v for k, v in r.getheaders() if k.lower() == "set-cookie"]
        cookie_names = [v.split(";", 1)[0].split("=", 1)[0].strip() for v in cookies]
        out = {"host": host_in, "resolved_ip": resolved, "port": port, "path": path,
               "status": r.status, "server": r.getheader("Server", ""),
               "location": r.getheader("Location", ""), "set_cookie_names": cookie_names,
               "title": title_of(raw), "form_fields": sorted(form.keys()),
               "body_snippet": mask_passwords(strip_script_style(raw))[:2000]}
        if ret_cookies:
            out["set_cookie_values"] = cookies
        conn.close()
        # 放開點：可選跟隨跳轉，最多 3 次，跳轉目標也要是內網。
        chain = []
        if follow:
            cur_status, cur_loc, cur_host, cur_port = r.status, r.getheader("Location", ""), connect_host, port
            for _ in range(3):
                if cur_status not in (301, 302, 303, 307, 308) or not cur_loc:
                    break
                chain.append(cur_loc)
                if cur_loc.startswith("http"):
                    u = urlparse(cur_loc)
                    try:
                        rip = ipaddress.ip_address(socket.gethostbyname(u.hostname))
                    except Exception:
                        break
                    if not (rip.is_private or rip.is_loopback):
                        break
                    cur_host, cur_port, cur_path = u.hostname, (u.port or 80), (u.path or "/")
                else:
                    cur_path = cur_loc
                try:
                    c2 = cls(cur_host, cur_port, timeout=10)
                    c2.request("GET", cur_path)
                    r2 = c2.getresponse()
                    raw2 = r2.read(16384).decode("utf-8", "ignore")
                    cur_status, cur_loc = r2.status, r2.getheader("Location", "")
                    out["final_status"], out["final_title"] = cur_status, title_of(raw2)
                    out["final_body_snippet"] = mask_passwords(strip_script_style(raw2))[:2000]
                    c2.close()
                except Exception as e:
                    out["follow_error"] = str(e)
                    break
            out["redirect_chain"] = chain
        return out
    except Exception as e:
        return {"host": host_in, "port": port, "path": path, "error": str(e)}


def _resolve_private(host_in):
    try:
        ip = ipaddress.ip_address(host_in)
        return host_in, str(ip), ""
    except ValueError:
        try:
            resolved = socket.gethostbyname(host_in)
            ip = ipaddress.ip_address(resolved)
            return resolved, resolved, ""
        except Exception as e:
            return "", "", f"主機名解析失敗：{e}"


def auth_fetch(a):
    host_in, path = a["host"], a.get("path", "/main.html")
    port = int(a.get("port", 80))
    login_path = a.get("login_path", "/login/Auth")
    username = a.get("username", "admin")
    pwd_md5 = a.get("password_md5", "")
    if not pwd_md5 and a.get("password"):
        pwd_md5 = hashlib.md5(str(a["password"]).encode()).hexdigest()
    if not pwd_md5:
        return {"host": host_in, "error": "需要 password 或 password_md5 其中之一"}
    connect_host, resolved, err = _resolve_private(host_in)
    if err:
        return {"host": host_in, "error": err}
    try:
        ip = ipaddress.ip_address(resolved)
    except Exception as e:
        return {"host": host_in, "resolved_ip": resolved, "error": str(e)}
    if not (ip.is_private or ip.is_loopback):
        return {"host": host_in, "resolved_ip": resolved, "error": "只允許內網 IP，拒絕公網位址"}
    if not path.startswith("/"):
        path = "/" + path
    if not login_path.startswith("/"):
        login_path = "/" + login_path
    try:
        conn = http.client.HTTPConnection(connect_host, port, timeout=10)
        body = urlencode({"username": username, "password": pwd_md5})
        conn.request("POST", login_path, body=body,
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
        r = conn.getresponse()
        login_raw = r.read(16384).decode("utf-8", "ignore")
        login_status, login_location = r.status, r.getheader("Location", "")
        cookies = [v for k, v in r.getheaders() if k.lower() == "set-cookie"]
        cookie_names = [v.split(";", 1)[0].split("=", 1)[0].strip() for v in cookies]
        conn.close()
        if not cookies or login_raw.strip() == "1":
            return {"host": host_in, "resolved_ip": resolved, "port": port,
                    "path": path, "login_status": login_status,
                    "login_location": login_location, "cookie_names": cookie_names,
                    "login_body_snippet": login_raw[:500],
                    "error": "登入未確認：無 Cookie 或回包為 1，未做後續抓取"}
        cookie_header = "; ".join(v.split(";", 1)[0].strip() for v in cookies)
        headers = {"Cookie": cookie_header, "User-Agent": "muse-lan-mcp/1.2"}
        if connect_host != host_in:
            headers["Host"] = host_in
        conn2 = http.client.HTTPConnection(connect_host, port, timeout=10)
        conn2.request("GET", path, headers=headers)
        r2 = conn2.getresponse()
        raw2 = r2.read(32768).decode("utf-8", "ignore")
        conn2.close()
        return {"host": host_in, "resolved_ip": resolved, "port": port, "path": path,
                "login_status": login_status, "login_location": login_location,
                "cookie_names": cookie_names, "fetch_status": r2.status,
                "fetch_location": r2.getheader("Location", ""),
                "fetch_server": r2.getheader("Server", ""),
                "title": title_of(raw2),
                "body_snippet": mask_passwords(strip_script_style(raw2))[:8000],
                "note": "唯讀 GET，不改設定；Cookie 只在本次呼叫內部用，不回值"}
    except Exception as e:
        return {"host": host_in, "port": port, "path": path, "error": str(e)}


DISPATCH = {"device_info": device_info, "lan_scan": lan_scan, "ping": ping,
            "tcp_ports": tcp_ports, "banner": banner, "web_audit": web_audit,
            "audit_host": audit_host, "mqtt_probe": mqtt_probe,
            "web_cred_context": web_cred_context, "http_probe": http_probe,
            "raw_probe": raw_probe, "ssdp_probe": ssdp_probe,
            "mdns_probe": mdns_probe, "identify_host": identify_host,
            "http_post": http_post, "auth_fetch": auth_fetch}


def _spec(name, desc, props, required):
    return {"name": name, "description": desc,
            "inputSchema": {"type": "object", "properties": props, "required": required}}


TOOLS = [
    _spec("device_info", "這台設備的系統與內網 IP。", {}, []),
    _spec("lan_scan", "掃描內網網段，找出在線設備（有開埠、回 RST 或在 ARP 表）與其開放埠（預設本機所在 /24）。",
          {"subnet": {"type": "string"}, "ports": {"type": "array", "items": {"type": "integer"}}}, []),
    _spec("ping", "Ping 一個內網位址。",
          {"host": {"type": "string"}, "count": {"type": "integer"}}, ["host"]),
    _spec("tcp_ports", "檢查內網設備的 TCP 連接埠（最多 200 個）：open／closed（主機在線）／filtered。",
          {"host": {"type": "string"}, "ports": {"type": "array", "items": {"type": "integer"}}}, ["host", "ports"]),
    _spec("banner", "抓取指定埠的服務 banner（唯讀）。",
          {"host": {"type": "string"}, "ports": {"type": "array", "items": {"type": "integer"}}}, ["host", "ports"]),
    _spec("web_audit", "對未驗證 HTTP 頁面唯讀檢查：標題、Server、頁面原始碼中外洩的密碼樣式（遮罩）。不登入、不猜密碼。",
          {"host": {"type": "string"}, "ports": {"type": "array", "items": {"type": "integer"}},
           "paths": {"type": "array", "items": {"type": "string"}}}, ["host"]),
    _spec("audit_host", "單機唯讀安全體檢：廣範圍埠＋banner＋HTTP 標題/Server＋風險旗標＋裝置猜測＋人工核對用預設密碼提示。不登入、不猜密碼。",
          {"host": {"type": "string"}, "ports": {"type": "array", "items": {"type": "integer"}}}, ["host"]),
    _spec("mqtt_probe", "唯讀探測 MQTT broker（如 1883）：用匿名連線測試是否需要認證；若允許匿名則列出看到的主題名稱。不發布、不猜密碼、不送認證。",
          {"host": {"type": "string"}, "port": {"type": "integer"}, "listen": {"type": "number"},
           "max_topics": {"type": "integer"}}, ["host"]),
    _spec("web_cred_context", "唯讀：對未登入頁面抓疑似密碼欄位的命中前後文（值遮罩），並標記是否像 JS/模板，供人工判讀真假。不登入、不猜密碼。",
          {"host": {"type": "string"}, "port": {"type": "integer"},
           "paths": {"type": "array", "items": {"type": "string"}}, "context": {"type": "integer"}}, ["host"]),
    _spec("http_probe", "唯讀：GET 指定埠/路徑，回完整回應標頭、狀態碼、title、去 script 後的 body 片段（密碼遮罩），用來辨識型號。",
          {"host": {"type": "string"}, "ports": {"type": "array", "items": {"type": "integer"}},
           "paths": {"type": "array", "items": {"type": "string"}}}, ["host"]),
    _spec("raw_probe", "唯讀：對非 HTTP 埠送無害探測（空、換行、HTTP、RTSP OPTIONS），回前 160 bytes，辨識協定。",
          {"host": {"type": "string"}, "port": {"type": "integer"}}, ["host", "port"]),
    _spec("ssdp_probe", "唯讀：UPnP/SSDP 探索，抓裝置自報的 friendlyName/manufacturer/modelName。可指定 host 過濾。",
          {"host": {"type": "string"}, "st": {"type": "string"}, "timeout": {"type": "number"}}, []),
    _spec("mdns_probe", "唯讀：mDNS/Bonjour 查詢，列出區網裝置宣告的服務。可指定 host 過濾、name 查特定服務。",
          {"host": {"type": "string"}, "name": {"type": "string"}, "timeout": {"type": "number"}}, []),
    _spec("identify_host", "唯讀綜合辨識：MAC＋HTTP 指紋＋SSDP＋mDNS。不登入、不猜密碼。",
          {"host": {"type": "string"}, "ports": {"type": "array", "items": {"type": "integer"}},
           "paths": {"type": "array", "items": {"type": "string"}}, "timeout": {"type": "number"}}, ["host"]),
    _spec("http_post", "單次 POST 到內網主機（例如路由器登入表單），不循環、不猜密碼；一次只送一組帳密，由呼叫方控制節奏。預設不跟隨跳轉、不回 Cookie 值；follow_redirects=true 可跟隨最多 3 次跳轉，return_cookies=true 才回 Cookie 值，主機名解析到內網 IP 才放行，公網一律拒絕。回傳狀態碼、跳轉位址、Set-Cookie 名稱、標題與去 script 後的 body 片段（密碼遮罩）。",
          {"host": {"type": "string"}, "port": {"type": "integer"}, "path": {"type": "string"},
           "form": {"type": "object"}, "use_https": {"type": "boolean"},
           "follow_redirects": {"type": "boolean"}, "return_cookies": {"type": "boolean"}}, ["host", "path"]),
    _spec("auth_fetch", "登入後在同一次呼叫內用登入 Cookie 做一次唯讀 GET，不改設定。先 POST 登入路徑，再用 Cookie 抓指定路徑；Cookie 只在伺服器內部用，不回值、不存檔。登入用 username 加 password_md5，或 password（伺服器內部轉 MD5）。只接內網，公網一律拒絕。回傳登入狀態、Cookie 名稱、抓取狀態、標題與去 script 後的 body 片段（密碼遮罩）。",
          {"host": {"type": "string"}, "port": {"type": "integer"}, "path": {"type": "string"},
           "login_path": {"type": "string"}, "username": {"type": "string"},
           "password": {"type": "string"}, "password_md5": {"type": "string"}}, ["host", "path"]),
]


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        sys.stderr.write(f"{self.address_string()} {a[0] % a[1:] if len(a) > 1 else a[0]}\n")
        sys.stderr.flush()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            req = json.loads(self.rfile.read(length) or b"{}")
        except Exception as e:
            return self._send({"jsonrpc": "2.0", "id": None,
                               "error": {"code": -32700, "message": str(e)}})
        rid, method = req.get("id"), req.get("method")
        if TOKEN:
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {TOKEN}":
                return self._send({"jsonrpc": "2.0", "id": rid,
                                   "error": {"code": -32001, "message": "unauthorized"}})
        if method == "tools/list":
            return self._send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        if method == "tools/call":
            params = req.get("params", {})
            name, args = params.get("name"), params.get("arguments") or {}
            fn = DISPATCH.get(name)
            if not fn:
                return self._send({"jsonrpc": "2.0", "id": rid,
                                   "error": {"code": -32601, "message": f"unknown tool {name}"}})
            try:
                result = fn(args)
                return self._send({"jsonrpc": "2.0", "id": rid,
                                   "result": {"content": [{"type": "text",
                                                           "text": json.dumps(result, ensure_ascii=False)}],
                                              "isError": False}})
            except Exception as e:
                return self._send({"jsonrpc": "2.0", "id": rid,
                                   "result": {"content": [{"type": "text", "text": json.dumps({"error": str(e)})}],
                                              "isError": True}})
        return self._send({"jsonrpc": "2.0", "id": rid,
                           "error": {"code": -32601, "message": f"unknown method {method}"}})

    def _send(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send({"jsonrpc": "2.0", "id": None, "result": {"muse_lan_mcp_version": VERSION,
                                                             "tools": len(TOOLS)}})


if __name__ == "__main__":
    srv = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Muse LAN MCP v{VERSION} listening on 0.0.0.0:{PORT} /mcp, tools={len(TOOLS)}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

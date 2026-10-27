#!/usr/bin/env python3
"""SentinelMind LAN audit MCP server (rebuilt, stdlib only).

"version": "1.2.0"
Open version (owner decision 2026-10-27):
    http_post can follow redirects, optionally return cookie values, and accept hostnames resolving to a LAN IP;
    mqtt_probe subscribes for topic names again; tcp_ports cap raised to 200.
1.2.0 added auth_fetch (owner decision 2026-10-27: login must allow viewing all information,
login without viewing is pointless):
    In one call, POST login first, then do one read-only GET with the login cookie,
    Cookie is used only inside the server, never returned, never stored, no settings changed.
Hard line kept: LAN only by default. Because the server is exposed via ngrok, a leaked Bearer token
token opened to the public internet would turn the server into a public proxy. This is not conservatism,
it would harm the owner, so it is not opened by default.
Source: rebuilt from the original server tools/list and tool behavior tested 2026-10-27,
All original 14 tools kept, plus a 15th tool http_post (single POST for login).
Standard library only, no pip packages needed, runs directly on phone Termux Python 3.12+.

Start (phone Termux, one command at a time):
    MUSE_TOKEN="$(cat ~/.muse_token)" nohup python ~/muse_lan_mcp.py > ~/mcp.log 2>&1 &
    sleep 2; tail -n 20 ~/mcp.log
Default listen 0.0.0.0:8765, path /mcp.
Auth: if env MUSE_TOKEN is set, requests must carry
    Authorization: Bearer <MUSE_TOKEN>
    otherwise 401; if MUSE_TOKEN is unset, no auth (local testing only,
    always set a token when exposed via ngrok).

Safety line (http_post only, also this file's hard line):
    - One POST per call, no loop, no password guessing inside the server.
    - LAN IP literals only by default; public IPs rejected unless the allow_public switch is on.
    - No redirect following by default; 302 + Location itself is a success signal.
    - Never return cookie values or submitted passwords, only cookie names and field names.
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

VERSION = "1.7.2"
PORT = int(os.environ.get("PORT", "8765"))
TOKEN = os.environ.get("MUSE_TOKEN", "")
ALLOW_PUBLIC = os.environ.get("MUSE_ALLOW_PUBLIC", "") == "1" or os.path.exists(os.path.join(os.path.expanduser("~"), ".lan_mcp_allow_public"))


def _ip_allowed(ip):
    return ALLOW_PUBLIC or ip.is_private or ip.is_loopback
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
    # Mask only password values, not JS function names: value must be quoted and must not contain document/getElement
    def repl(m):
        val = m.group(2)
        if "document" in val or "getElement" in val or "." in val:
            return m.group(0)
        masked = val[0] + "*" * max(0, len(val) - 2) + val[-1] if len(val) >= 2 else "*"
        return m.group(1) + masked + m.group(3)
    return re.sub(r"(?i)(password[^=\n]{0,20}=[\"'])([^\"']+)([\"'])", repl, text)


def tcp_check(host, port, timeout=1.0):
    """Return open / closed / filtered."""
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
    """Only real password values count; JS function names do not, to avoid the original .1 false positive."""
    out = []
    for m in re.finditer(r"(?i)password[^=\n]{0,30}=[\"']([^\"']+)[\"']", body):
        val = m.group(1)
        if "document" in val or "getElement" in val or val.startswith("function"):
            continue
        out.append({"field": "password", "len": len(val),
                    "value_masked": val[0] + "*" * max(0, len(val) - 2) + val[-1] if len(val) >= 2 else "*"})
    return out


# ---- Tool implementations ----
def network_mode():
    ip = local_ip()
    try:
        addr = ipaddress.ip_address(ip)
        if addr.is_private or addr.is_loopback:
            return "lan"
    except Exception:
        pass
    return "non_lan"


def device_info(_a):
    import platform
    return {"os": f"{platform.system()}-{platform.release()}-{platform.machine()}",
            "hostname": socket.gethostname(), "local_ip": local_ip(),
            "network_mode": network_mode(),
            "allow_public": ALLOW_PUBLIC,
            "python": sys.version.split()[0], "muse_lan_mcp_version": VERSION}


def lan_scan(a):
    subnet = a.get("subnet") or ""
    ports = a.get("ports") or DEFAULT_PORTS
    if not subnet:
        if network_mode() != "lan":
            return {"subnet": "", "error": "phone is not on a private LAN (network_mode=non_lan, local_ip=" + local_ip() + "); LAN scan needs a WiFi LAN, local tools (device_info, shell) still work", "devices": []}
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
            "note": "alive_by: open_port = port open; tcp_rst = live but all ports closed; arp = seen only in ARP table"}


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
            "note": "exposed_secrets = password patterns seen in page source without login (masked). Verify in a browser yourself; this is usually a serious misconfiguration."}


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
                flags.append("Admin page source leaks a password (see web.exposed_secrets)")
        else:
            web.append({"port": p, "server": "", "title": "", "exposed_secrets": []})
    guess = []
    joined = json.dumps(web, ensure_ascii=False)
    if "ZTE" in joined:
        guess.append("ZTE router / optical modem family")
    if "Tenda" in joined:
        guess.append("Tenda router family")
    if "\u4e2d\u56fd\u79fb\u52a8" in joined or "\u4e2d\u570b\u79fb\u52d5" in joined:
        guess.append("China Mobile device family")
    return {"host": host, "open_ports": opens, "banners": banners, "web": web,
            "device_guess": guess, "risk_flags": flags, "default_credential_hints": {},
            "note": "default_credential_hints are for manual check only; the tool never tries login or password guessing automatically."}


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
                "note": "Subscribe only, never publish; only topic names returned, never message content."}
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
            "note": "snippet is context around the hit (values masked). likely_js_or_template=true means the surroundings look like code/template, not a real credential. For manual judgment only."}


def http_probe(a):
    host, ports, paths = a["host"], (a.get("ports") or ([int(a["port"])] if a.get("port") else [80])), (a.get("paths") or ["/"])
    probes = []
    for p in ports:
        for path in paths:
            probes.append(http_get(host, int(p), path))
    return {"host": host, "probes": probes,
            "note": "body_snippet has scripts/styles removed and suspected passwords masked; read-only, no login."}


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
                        # Namespace uncertain, brute-force search by tag local name
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
    # Minimal implementation: send a _services query, collect readable service names from replies; empty does not mean no devices.
    name = a.get("name") or "_services._dns-sd._udp.local"
    timeout = float(a.get("timeout", 3))
    host_filter = a.get("host") or ""
    try:
        # DNS query packet: ID 0, flags 0, QDCOUNT 1
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



def fingerprint_device(host, opens, http_list, extra_probes):
    """Multi-path / multi-port fingerprint, v1.5.0.

    Why: title-only identification fails on ISP gateways whose login
    page is a JS shell (empty title, no Server header). The fingerprint
    is the *combination* of: 80-page JS shell, 8080 JSON error string,
    fixed-path-only 400 behaviour, and the open-port set. One item
    alone is not proof; the combination is returned as a suspect
    guess with its evidence, never as a confirmed model.
    """
    guesses, evidence = [], []
    all_probes = list(http_list) + list(extra_probes)
    bodies = " ".join((x.get("body_snippet") or "") for x in all_probes)
    ctypes = " ".join((x.get("content_type") or "") for x in all_probes)
    by_port_path = {(x.get("port"), x.get("path")): x for x in all_probes}
    has_frmload = "frmLoad" in bodies
    has_no_cmd = "no_cmd_match" in bodies
    if has_frmload:
        evidence.append("80 page is a frmLoad() JS shell (Broadcom-based telecom gateway firmware pattern)")
    if has_no_cmd:
        evidence.append("8080 returns application/json 'no_cmd_match' (Tianyi TEWA optical-modem pattern)")
    login_400 = any(by_port_path.get((80, path), {}).get("status") == 400 for path in ("/login.html", "/main.html", "/robots.txt"))
    root_200 = by_port_path.get((80, "/"), {}).get("status") == 200
    if root_200 and login_400:
        evidence.append("80 serves only fixed paths, other paths return 400 (not 404)")
    if has_frmload and has_no_cmd:
        guesses.append({"guess": "telecom_optical_modem_suspect", "evidence": "frmLoad JS shell on 80 + no_cmd_match JSON on 8080"})
    elif has_no_cmd:
        guesses.append({"guess": "telecom_optical_modem_suspect", "evidence": "8080 returns no_cmd_match JSON"})
    elif has_frmload:
        guesses.append({"guess": "telecom_gateway_firmware_suspect", "evidence": "80 page uses frmLoad() JS shell"})
    if sorted(opens) == [53, 80, 8080]:
        guesses.append({"guess": "isp_gateway_suspect", "evidence": "only DNS(53)+web(80)+8080 JSON API open, no SSH/Telnet/HTTPS management"})
    return guesses, evidence

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
    # v1.5.0 fingerprint probes: 80 + 8080, several paths, because a
    # single "/" title is empty on JS-shell gateways.
    extra_probes = []
    for p in (80, 8080):
        if p in opens:
            for path in ("/", "/login.html", "/favicon.ico"):
                try:
                    extra_probes.append(http_get(host, p, path))
                except Exception:
                    pass
    ssdp = ssdp_probe({"host": host, "timeout": 3}).get("devices", [])
    mdns = mdns_probe({"host": host, "timeout": 3}).get("services", [])
    try:
        hostname, _, _ = socket.gethostbyaddr(host)
    except Exception:
        hostname = ""
    mac = mac_of(host)
    guesses = guess_device(host, opens, http_list, ssdp, mdns)
    fp_guesses, fp_evidence = fingerprint_device(host, opens, http_list, extra_probes)
    for g in fp_guesses:
        if g not in guesses:
            guesses.append(g)
    mac_status = "available" if mac else "unavailable_on_this_phone"
    return {"host": host, "hostname": hostname, "mac": mac, "mac_vendor": "",
            "mac_status": mac_status,
            "mac_note": "When the phone ARP table is empty, MAC cannot be found; this is a phone sandbox/VPN limit, not missing code. Accurate MACs are in the router client list, via router_clients (read-only after login).",
            "open_ports": opens, "http": http_list, "fingerprint_probes": extra_probes,
            "fingerprint_evidence": fp_evidence, "ssdp": ssdp, "mdns": mdns,
            "device_guess": guesses,
            "note": "Combined device-reported info and multi-path HTTP fingerprints; read-only. device_guess is only a guess, judge by fingerprint evidence; exact model needs a logged-in version page or device label."}




def shell(a):
    """Run a shell command on the phone itself, return output.

    Owner-only, unrestricted by user order 2026-10-27 ("open mode").
    Runs as the Termux user, not root. Timeout 120s, output capped
    at 20000 chars. This tool is why the Bearer token must never leak:
    anyone with the token gets a shell on the phone, not just a LAN scan.
    No Gist, no second token needed: it rides the existing MCP tunnel.
    """
    cmd = a.get("command", "")
    if not cmd:
        return {"error": "command is required"}
    timeout = float(a.get("timeout", 120))
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or "") + (r.stderr or "")
        return {"command": cmd, "exit_code": r.returncode, "output": out[:20000]}
    except subprocess.TimeoutExpired:
        return {"command": cmd, "exit_code": 124, "output": "[TIMEOUT]", "error": f"exceeded {timeout}s"}
    except Exception as e:
        return {"command": cmd, "exit_code": 1, "output": "", "error": str(e)}

def router_clients(a):
    """Login once, read-only GET the router client list, return it.

    This is the MAC source: the router's own DHCP/client list holds
    IP+MAC+hostname together, which the phone ARP table cannot give
    on this phone. Same safety as auth_fetch: one login, one read-only
    GET, no settings changed, cookie kept inside this call only.
    """
    args = dict(a)
    args.setdefault("path", a.get("client_path") or "/goform/GetIpMacBind")
    out = auth_fetch(args)
    out["tool"] = "router_clients"
    out["note"] = "MAC comes from the router client list, not the phone ARP table; when not logged in or the path is wrong, the router returns a login page or empty data. That does not mean the password is wrong, only that this path is not this router's client-list path."
    return out

def http_post(a):
    host_in, path = a["host"], a.get("path", "/")
    port, form = int(a.get("port", 80)), (a.get("form") or {})
    use_https = bool(a.get("use_https", False))
    follow = bool(a.get("follow_redirects", False))
    ret_cookies = bool(a.get("return_cookies", False))
    # Open point: hostnames allowed, but must resolve to a LAN IP, otherwise rejected.
    try:
        ip = ipaddress.ip_address(host_in)
        connect_host, resolved = host_in, str(ip)
    except ValueError:
        try:
            resolved = socket.gethostbyname(host_in)
            ip = ipaddress.ip_address(resolved)
            connect_host = resolved
        except Exception as e:
            return {"host": host_in, "error": f"hostname resolution failed: {e}"}
    if not _ip_allowed(ip):
        return {"host": host_in, "resolved_ip": resolved, "error": "Private LAN IP only, public IP rejected (switch off: set MUSE_ALLOW_PUBLIC=1 or create ~/.lan_mcp_allow_public, then restart)"}
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
        # Open point: optional redirect following, max 3, redirect targets must also be LAN.
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
                    if not _ip_allowed(rip):
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
            return "", "", f"hostname resolution failed: {e}"


def auth_fetch(a):
    host_in, path = a["host"], a.get("path", "/main.html")
    port = int(a.get("port", 80))
    login_path = a.get("login_path", "/login/Auth")
    username = a.get("username", "admin")
    pwd_md5 = a.get("password_md5", "")
    if not pwd_md5 and a.get("password"):
        pwd_md5 = hashlib.md5(str(a["password"]).encode()).hexdigest()
    if not pwd_md5:
        return {"host": host_in, "error": "password or password_md5 is required"}
    connect_host, resolved, err = _resolve_private(host_in)
    if err:
        return {"host": host_in, "error": err}
    try:
        ip = ipaddress.ip_address(resolved)
    except Exception as e:
        return {"host": host_in, "resolved_ip": resolved, "error": str(e)}
    if not _ip_allowed(ip):
        return {"host": host_in, "resolved_ip": resolved, "error": "Private LAN IP only, public IP rejected (switch off: set MUSE_ALLOW_PUBLIC=1 or create ~/.lan_mcp_allow_public, then restart)"}
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
                    "error": "Login not confirmed: no cookie or response was 1, no follow-up fetch done"}
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
                "note": "Read-only GET, no settings changed; cookie used only inside this call, never returned"}
    except Exception as e:
        return {"host": host_in, "port": port, "path": path, "error": str(e)}


DISPATCH = {"device_info": device_info, "lan_scan": lan_scan, "ping": ping,
            "tcp_ports": tcp_ports, "banner": banner, "web_audit": web_audit,
            "audit_host": audit_host, "mqtt_probe": mqtt_probe,
            "web_cred_context": web_cred_context, "http_probe": http_probe,
            "raw_probe": raw_probe, "ssdp_probe": ssdp_probe,
            "mdns_probe": mdns_probe, "identify_host": identify_host,
            "http_post": http_post, "auth_fetch": auth_fetch, "router_clients": router_clients, "shell": shell}


def _spec(name, desc, props, required):
    return {"name": name, "description": desc,
            "inputSchema": {"type": "object", "properties": props, "required": required}}


TOOLS = [
    _spec("device_info", "This device system and LAN IP.", {}, []),
    _spec("lan_scan", "Scan a LAN subnet for live hosts (open port, TCP RST, or ARP entry) and their open ports (default: local /24).",
          {"subnet": {"type": "string"}, "ports": {"type": "array", "items": {"type": "integer"}}}, []),
    _spec("ping", "Ping a LAN address.",
          {"host": {"type": "string"}, "count": {"type": "integer"}}, ["host"]),
    _spec("tcp_ports", "Check TCP ports on a LAN host (max 200): open / closed (host live) / filtered.",
          {"host": {"type": "string"}, "ports": {"type": "array", "items": {"type": "integer"}}}, ["host", "ports"]),
    _spec("banner", "Fetch a service banner from a port (read-only).",
          {"host": {"type": "string"}, "ports": {"type": "array", "items": {"type": "integer"}}}, ["host", "ports"]),
    _spec("web_audit", "Read-only check of an unauthenticated HTTP page: title, Server, and leaked password patterns in page source (masked). No login, no password guessing.",
          {"host": {"type": "string"}, "ports": {"type": "array", "items": {"type": "integer"}},
           "paths": {"type": "array", "items": {"type": "string"}}}, ["host"]),
    _spec("audit_host", "Single-host read-only security audit: wide ports + banner + HTTP title/Server + risk flags + device guess + default-credential hints for manual check. No login, no password guessing.",
          {"host": {"type": "string"}, "ports": {"type": "array", "items": {"type": "integer"}}}, ["host"]),
    _spec("mqtt_probe", "Read-only MQTT broker probe (e.g. 1883): test with an anonymous connection whether auth is required; if anonymous is allowed, list seen topic names. No publish, no password guessing, no auth sent.",
          {"host": {"type": "string"}, "port": {"type": "integer"}, "listen": {"type": "number"},
           "max_topics": {"type": "integer"}}, ["host"]),
    _spec("web_cred_context", "Read-only: fetch context around suspected password fields on an unauthenticated page (values masked), and mark whether it looks like JS/template, for manual judgment. No login, no password guessing.",
          {"host": {"type": "string"}, "port": {"type": "integer"},
           "paths": {"type": "array", "items": {"type": "string"}}, "context": {"type": "integer"}}, ["host"]),
    _spec("http_probe", "Read-only: GET a port/path, return full response headers, status, title, and body snippet with scripts removed (passwords masked), for model identification.",
          {"host": {"type": "string"}, "ports": {"type": "array", "items": {"type": "integer"}},
           "paths": {"type": "array", "items": {"type": "string"}}}, ["host"]),
    _spec("raw_probe", "Read-only: send harmless probes (empty, newline, HTTP, RTSP OPTIONS) to a non-HTTP port, return first 160 bytes, for protocol identification.",
          {"host": {"type": "string"}, "port": {"type": "integer"}}, ["host", "port"]),
    _spec("ssdp_probe", "Read-only: UPnP/SSDP discovery, fetch device-reported friendlyName/manufacturer/modelName. Optional host filter.",
          {"host": {"type": "string"}, "st": {"type": "string"}, "timeout": {"type": "number"}}, []),
    _spec("mdns_probe", "Read-only: mDNS/Bonjour query, list services announced by LAN devices. Optional host filter, name for a specific service.",
          {"host": {"type": "string"}, "name": {"type": "string"}, "timeout": {"type": "number"}}, []),
    _spec("identify_host", "Read-only combined identification: MAC + HTTP fingerprint + SSDP + mDNS. No login, no password guessing.",
          {"host": {"type": "string"}, "ports": {"type": "array", "items": {"type": "integer"}},
           "paths": {"type": "array", "items": {"type": "string"}}, "timeout": {"type": "number"}}, ["host"]),
    _spec("http_post", "Single POST to a LAN host (e.g. a router login form), no loop, no password guessing; one credential set per call, caller controls pacing. Default: no redirect following, no cookie values returned; follow_redirects=true follows up to 3 redirects, return_cookies=true returns cookie values, hostnames allowed only when resolved to a LAN IP, public IPs always rejected. Returns status, redirect address, Set-Cookie names, title, and body snippet with scripts removed (passwords masked).",
          {"host": {"type": "string"}, "port": {"type": "integer"}, "path": {"type": "string"},
           "form": {"type": "object"}, "use_https": {"type": "boolean"},
           "follow_redirects": {"type": "boolean"}, "return_cookies": {"type": "boolean"}}, ["host", "path"]),
    _spec("auth_fetch", "After login, one read-only GET with the login cookie in the same call, no settings changed. POST the login path first, then fetch the target path with the cookie; the cookie is used only inside the server, never returned, never stored. Login uses username plus password_md5, or password (converted to MD5 inside the server). LAN only, public IPs always rejected. Returns login status, cookie names, fetch status, title, and body snippet with scripts removed (passwords masked).",
          {"host": {"type": "string"}, "port": {"type": "integer"}, "path": {"type": "string"},
           "login_path": {"type": "string"}, "username": {"type": "string"},
           "password": {"type": "string"}, "password_md5": {"type": "string"}}, ["host", "path"]),
    _spec("router_clients", "After router login, read-only fetch of the client list (IP+MAC+hostname), the accurate MAC source. Login works like auth_fetch: one login, one read-only GET, no settings changed, cookie values never returned. client_path defaults to /goform/GetIpMacBind; paths differ by brand, a wrong path returns a login page or empty data.",
          {"host": {"type": "string"}, "port": {"type": "integer"}, "path": {"type": "string"},
           "client_path": {"type": "string"}, "login_path": {"type": "string"}, "username": {"type": "string"},
           "password": {"type": "string"}, "password_md5": {"type": "string"}}, ["host"]),
    _spec("shell", "Run one shell command on the phone itself, return output and exit code. Owner-only via Bearer token; a leaked token equals a leaked phone shell, so close the tunnel when done. Timeout 120s, output truncated to 20000 chars.",
          {"command": {"type": "string"}, "timeout": {"type": "number"}}, ["command"]),
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
    print(f"SentinelMind v{VERSION} listening on 0.0.0.0:{PORT} /mcp, tools={len(TOOLS)}, allow_public={ALLOW_PUBLIC}", flush=True)
    if ALLOW_PUBLIC:
        print("WARNING: allow_public is ON - public IP targets are allowed, close the tunnel when done.", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass

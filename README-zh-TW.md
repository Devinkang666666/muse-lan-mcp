# LAN MCP：用 AI 幫你掃內網

[![Python](https://img.shields.io/badge/Python-3.x-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-18_tools-green.svg)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Termux%20%2F%20Android-orange.svg)](https://termux.dev/)

[English version](README.md)

## 這是什麼

LAN MCP 是一個跑在 Android 手機上的 MCP（模型上下文協議）伺服器。它不再只是小小的內網掃描，而是一套網路審計工具。它讓 AI 助手掃描、識別家裡內網的設備，審計主機的開放服務和外洩密碼，登入路由器後唯讀查看資訊，檢查附近 WiFi 的加密方式，還能做手機本地檢查，而不用把內網設備直接暴露給外網。

簡單說：手機本來就連著家裡 Wi-Fi，手機跑伺服器，遠端的 AI 助手連到手機，手機再幫它做內網的工作。

這個做法適合 AI 助手本身不在家裡、打不到 `192.168.x.x` 這類內網地址的情況。手機就是遠端 AI 和家裡內網之間的一座橋。

## 適合誰

新手也可以用。不需要伺服器、不需要 Docker 主機、不需要廠商控制器、不需要另外買硬體。一台 Android 手機、一個家裡 Wi-Fi、一個免費 ngrok 帳號就夠了。

會在終端機裡打幾個命令就夠了。下面每一步都按順序寫出來，從零開始。

## 能做什麼

- 掃一個內網網段，列出活著的主機和開放的端口。
- 用 banner、HTTP 標題、SSDP、mDNS 識別主機。
- ping 主機、探測 TCP 端口。
- 對內網地址發一次 HTTP POST（例如路由器登入頁）。
- 登入路由器網頁後唯讀取回一頁，同一次伺服器內呼叫完成（`auth_fetch`）。會話 Cookie 只在伺服器內部用，不回值、不存檔。
- 共 18 個工具（v1.7.1 重大更新）：`device_info, lan_scan, ping, tcp_ports, banner, web_audit, audit_host, mqtt_probe, web_cred_context, http_probe, raw_probe, ssdp_probe, mdns_probe, identify_host, http_post, auth_fetch, router_clients, shell`。`identify_host` 會多路徑探測 80 和 8080 並比對指紋（例如 80 的 frmLoad JS 殼加上 8080 的 `no_cmd_match` JSON 回應，指向電信光貓家族），`router_clients` 是登入後唯讀抓路由器客戶端列表，手機 ARP 表為空時，這才是 MAC 的準確來源，`shell` 則是在手機本機跑一條 shell 命令。`device_info` 現在會回 `network_mode`（內網是 `lan`，移動網絡是 `non_lan`）；移動網絡下內網掃描不能用，但本地工具（`device_info`、`shell`）還能用。公網地址預設一律拒絕，只有機主明確開開關才能用（啟動時設 `MUSE_ALLOW_PUBLIC=1`，或在手機上建一個空的 `~/.lan_mcp_allow_public` 文件），用完必須再關掉。

## 操作圖片

下面三張是示意圖，不是真實截圖，圖裡的命令不用照抄，真正要跑的命令以本文為準。

![架構](images/operation-architecture.svg)

![一鍵啟動](images/operation-start.svg)

![認設備](images/operation-scan.svg)

## 第 1 步：安裝 Termux

Termux 是 Android 上的終端機 App，不需要 root，就能在手機上跑一個 Linux 環境。

1. 從 F-Droid 安裝 Termux（建議用 F-Droid，Play 商店版比較舊）。打開 Termux 一次，讓它完成第一次設定。
2. 更新套件清單：
   ```
   pkg update && pkg upgrade -y
   ```

## 第 2 步：安裝 Python

在 Termux 裡跑：
```
pkg install -y python
python3 --version
```
需要 Python 3。其他 Python 套件都不需要：伺服器只用 Python 標準庫。

## 第 3 步：取得這些檔案

在 Termux 裡下載這個倉庫，兩種方法都可以：

方法 A，用 git：
```
pkg install -y git
git clone https://github.com/Devinkang666666/muse-lan-mcp.git
cp muse-lan-mcp/muse_lan_mcp.py ~/
cp muse-lan-mcp/start-lan.sh ~/
```

方法 B，不用 git：從 GitHub 頁面下載倉庫 ZIP，解壓後把 `muse_lan_mcp.py` 和 `start-lan.sh` 複製到 Termux 家目錄（`~`）。

做完這一步，`~/muse_lan_mcp.py` 和 `~/start-lan.sh` 都要存在。

## 第 4 步：建立一個令牌

伺服器經隧道對外時需要一個 Bearer 令牌。令牌就是手機主人自己定的一個長密碼，絕對不要分享、不要寫進公開倉庫、不要發給別人。

在 Termux 裡建立令牌檔（把範例值換成自己定的長隨機值）：
```
printf 'your-long-random-token-here' > ~/.lan_mcp_token
chmod 600 ~/.lan_mcp_token
```
`start-lan.sh` 啟動時會讀這個檔。為了相容舊設定，如果 `~/.lan_mcp_token` 不存在，它也會接受舊檔名 `~/.hark_token`。

## 第 5 步：安裝 ngrok

ngrok 會給一個公開隧道地址，轉到本地 8765 端口的 MCP 伺服器。免費 ngrok 帳號一次只能佔一個隧道，夠用了。

1. 從 ngrok 官網下載頁選 Android ARM64 版。Termux 套件倉庫裡沒有 ngrok，不要用 `pkg install ngrok` 找。
2. 解壓後把 `ngrok` 放到 Termux 能找到的路徑，例如 `~/../usr/bin/ngrok`，再跑：
   ```
   chmod +x ngrok
   ```
3. 註冊免費 ngrok 帳號，登入後到 Dashboard 的 Your Authtoken 頁，複製自己的 authtoken。這個碼是自己的，不要分享、不要寫進公開倉庫。
4. 在 Termux 裡配 authtoken（把佔位換成真的 authtoken）：
   ```
   ngrok config add-authtoken <你自己的 authtoken>
   ```
   配好後碼只存在手機上 `~/.config/ngrok/ngrok.yml` 裡。

## 第 6 步：一鍵啟動

在 Termux 裡跑：
```
chmod +x ~/start-lan.sh
~/start-lan.sh
```
這個腳本會：
- 先停掉舊隧道（免費帳號一次只能佔一個域名，起第二條會報 `ERR_NGROK_334`），
- 後台啟動 MCP 伺服器，已經在跑就沿用，不重複起，
- 後台啟動 ngrok 隧道到 8765 端口，
- 印出 `started tunnel` 行，裡面有一個像 `https://....ngrok-free.dev` 的公開地址。

那個公開地址，加上第 4 步的 Bearer 令牌，就是 AI 助手要連的東西。隧道地址後面加上 `/mcp`，例如 `https://....ngrok-free.dev/mcp`，就是 MCP 端點。

用完要停，跑：
```
~/start-lan.sh stop
```
隧道和 MCP 都不要長期開著。

## 第 7 步：接上 AI 助手

在任何支援 MCP 的 AI 助手裡，加一個遠端 MCP 伺服器：
- URL： 第 6 步的 ngrok 隧道地址，後面加上 `/mcp`，
- Authorization 標頭： `Authorization: Bearer <第 4 步的令牌>`。

然後就可以叫助手掃內網了。掃的時候一定要明確指定家裡網段，例如：
```json
{"subnet": "192.168.1.0/24"}
```
不要靠預設網段：如果手機走了 VPN / Warp 等虛擬路徑，預設網段可能變成虛擬網段，掃出全開假象。

## 檔案

- `muse_lan_mcp.py`：MCP 伺服器本體。純 Python 標準庫。監聽 `0.0.0.0:8765`，路徑 `/mcp`，JSON-RPC 2.0。
- `start-lan.sh`：MCP 伺服器＋ngrok 隧道的一鍵啟動和停止。裡面不含任何令牌、密碼、隧道域名。令牌只在啟動時從檔案讀。
- `images/`：說明文件的 SVG 示意圖。
- `LICENSE`：MIT 授權。
- `SECURITY.md`：安全政策。

## 安全硬線

- 預設只接內網：公網 IP 一律拒絕，主機名只在解析到私有／回環地址時才放行；只有機主明確開開關（啟動時設 `MUSE_ALLOW_PUBLIC=1`，或建 `~/.lan_mcp_allow_public` 空文件）才能連公網，用完必須再關掉。
- 認證：環境變數 `MUSE_TOKEN` 有值時，必須帶 `Authorization: Bearer <MUSE_TOKEN>`，否則回 401。經 ngrok 對外時務必設令牌，不要空跑。
- 登入後唯讀：`auth_fetch` 登入後只做 GET，不改任何設備設定。不要用 `Set*` 類接口。
- 密碼處理：登入密碼只在呼叫時傳，伺服器內部轉 MD5（如果接口需要），不寫進檔案、不寫進日誌，頁面片段裡的密碼會被遮罩。
- 機密：令牌、密碼、Cookie 值、authtoken、隧道域名，一律不要寫進公開倉庫、不要發給別人。每個人掃的是自己的內網，不是別人的。

## 常見問題

- `ERR_NGROK_334`（域名已被佔用）：先 `pkill ngrok` 停掉舊隧道，再跑 `~/start-lan.sh`。腳本啟動時已經是這樣做的。
- `lookup connect.ngrok-agent.com on [::1]:53: connection refused`（ngrok 查不到自己的伺服器）：手機 DNS 壞了。寫一個 `~/resolv.conf`，裡面兩行分別是家裡路由器地址（例如 `nameserver 192.168.1.1`）和一個公共 DNS（例如 `nameserver 223.5.5.5`），然後用 `proot -b ~/resolv.conf:/etc/resolv.conf ngrok ...` 的方式起。`start-lan.sh` 已經是這樣起的。
- 一直不出 `started tunnel`：看 `~/ngrok-lan.log` 裡的 ngrok 錯誤，檢查第 5 步的 authtoken 有沒有配好。
- MCP 起不來：看 `~/muse-lan.log`，檢查 `~/muse_lan_mcp.py` 在不在，檢查第 4 步的令牌檔在不在。
- 掃出來每台主機每個端口都開：網段很可能是 VPN / Warp 虛擬網段，不是家裡內網。按第 7 步明確指定家裡網段。

## 授權

MIT 授權。見 `LICENSE`。

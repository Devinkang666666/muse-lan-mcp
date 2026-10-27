# LAN MCP：用 AI 幫你掃內網

[![Python](https://img.shields.io/badge/Python-3.x-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-16_tools-green.svg)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Termux%20%2F%20Android-orange.svg)](https://termux.dev/)

[English version](README.md)

## 概覽

手機本地跑一個 MCP 伺服器，讓你的 AI 助手（例如 Muse）能從手機所在的內網出發，掃描、識別家裡的設備，登入路由器後唯讀查看資訊。不用把內網設備直接暴露給外網 AI，AI 只透過這台手機上的伺服器發請求。

## 操作圖片

下面三張是示意圖，不是真實截圖，圖裡的命令不用照抄，真正要跑的命令以本文為準。圖片用 SVG 向量格式，方便直接推到倉庫。

![架構](images/operation-architecture.svg)

![一鍵啟動](images/operation-start.svg)

![認設備](images/operation-scan.svg)

## 檔案

- `muse_lan_mcp.py`：MCP 伺服器本體，純 Python 標準庫，監聽 `0.0.0.0:8765`，路徑 `/mcp`，JSON-RPC 2.0。
  - 工具（共 16 個）：`device_info, lan_scan, ping, tcp_ports, banner, web_audit, audit_host, mqtt_probe, web_cred_context, http_probe, raw_probe, ssdp_probe, mdns_probe, identify_host, http_post, auth_fetch`。
  - `auth_fetch`：同一次呼叫內 POST 登入、拿 Cookie、再唯讀 GET 一頁，Cookie 只在伺服器內部用，不回值、不存檔。
- `start-lan.sh`：一鍵啟動。起 MCP + ngrok 隧道（轉 8765），後台跑，日誌留檔。停掉用 `~/start-lan.sh stop`。

## 安全硬線（不要拿掉）

- 只接內網：公網 IP 一律拒絕，主機名只在解析到私有 / 回環地址時才放行。
- 認證：環境變數 `MUSE_TOKEN` 有值時，必須帶 `Authorization: Bearer <MUSE_TOKEN>`，否則回 401。經 ngrok 對外時務必設 token，不要空跑。
- 唯讀為主：`auth_fetch` 登入後只做 GET，不改任何設備設定。不要用 `Set*` 類接口。
- 密碼處理：登入密碼只在呼叫時傳，伺服器內部轉 MD5（如果接口需要），不寫進檔案、不寫進日誌，頁面片段裡的密碼會被遮罩。
- 令牌、密碼、Cookie 值、隧道域名，一律不要寫進公開倉庫。`start-lan.sh` 只讀你手機上現成的 `~/.hark_token`，不含任何真值。

## 前置條件

- 手機 Termux，Python 3，`~/muse_lan_mcp.py` 已放好。
- 一個令牌檔 `~/.hark_token`，內容就是你自己定的 Bearer token，別人用時自己建一個，不要共用別人的。
- ngrok（見下面「建立隧道」一節，沒配好 authtoken 就起不了隧道）。

## 建立隧道（第一次才要做）

`start-lan.sh` 只會「起」隧道，不會幫你「裝」ngrok 和「配」authtoken，所以第一次要先自己做下面這幾步，做完一次以後就不用再做了：
1. 裝 ngrok：到 ngrok 官網下載頁選 Android ARM64 版，下載、解壓，把 `ngrok` 放到 Termux 能找到的路徑（例如 `~/../usr/bin/ngrok`），再 `chmod +x ngrok`。Termux 軟體倉庫裡沒有 ngrok，不要用 `pkg install ngrok` 找。
2. 註冊 ngrok 帳號（免費版就可以），登入後到 Dashboard（儀表板）的 Your Authtoken（你的授權碼）頁，複製你自己的 authtoken。這個碼是你自己的，不要發給別人、不要寫進公開倉庫。
3. 在手機 Termux 裡配 authtoken，跑下面這條（把碼換成你自己的，不要貼到公開地方）：
   ```
   ngrok config add-authtoken <你自己的 authtoken>
   ```
   配好後碼只存在手機上 `~/.config/ngrok/ngrok.yml` 裡。
4. 測一次隧道通不通（不用等一鍵腳本），跑：
   ```
   proot -b ~/resolv.conf:/etc/resolv.conf ngrok http 8765 --log=stdout --inspect=false
   ```
   出 `started tunnel` 和一個 `https://....ngrok-free.dev` 地址就是通了，那個地址就是你的 AI 助手要連的地址。測完按 Ctrl+C 停掉，等一鍵腳本再起。
5. 如果 ngrok 報 `lookup connect.ngrok-agent.com on [::1]:53: connection refused`（查不到 ngrok 伺服器），就是手機 DNS 壞了：先寫一個 `~/resolv.conf`，裡面兩行分別是你家路由器地址（例如 `nameserver 192.168.1.1`）和一個公共 DNS（例如 `nameserver 223.5.5.5`），然後一律用上面第 4 步那種 `proot -b ~/resolv.conf:/etc/resolv.conf ngrok ...` 的方式起，`start-lan.sh` 已經是這樣起的，不用你再手動。
6. 免費 ngrok 一次只能佔一個域名，起第二條隧道會報 `ERR_NGROK_334`（域名已被佔用）。先 `pkill ngrok` 停掉舊的，再起新的，`start-lan.sh` 已經是這樣做的。
7. 隧道用完就停，長期開著等於把手機上的 MCP 伺服器公開在外網，雖然有 Bearer token 擋著，也不要長期開。

## 一鍵啟動

```
cp start-lan.sh ~/start-lan.sh
chmod +x ~/start-lan.sh
~/start-lan.sh
```

跑完會印出 MCP 的 listening 行和 ngrok 的 `started tunnel` 行，隧道地址就是你的 AI 助手要連的地址。把那個地址交給你的 MCP 客戶端（例如 Muse 的 Lan Mcp 連接器），客戶端要帶 Bearer token 才能呼叫。

掃內網時要明確指定網段，例如：
```json
{"subnet": "192.168.1.0/24"}
```
不要靠預設網段：如果手機走了 VPN / Warp 等虛擬路徑，預設網段可能變成虛擬網段，掃出全開假象。

## 別人怎麼用

1. 下載這幾個檔（本說明文件、`muse_lan_mcp.py`、`start-lan.sh`）。
2. 在自己手機 Termux 裡放好 `muse_lan_mcp.py`，自己建一個 `~/.hark_token`。
3. 跑 `~/start-lan.sh`，拿到隧道地址。
4. 在自己的 AI 助手裡接上這個 MCP 地址，帶上自己的 Bearer token，就能掃自己的內網、認自己的設備。
5. 別人掃的是別人的內網，不是你的。不要把你的隧道地址、令牌、密碼發給別人。

## 停掉

```
~/start-lan.sh stop
```

用完就停，隧道和 MCP 都不要長期開著。

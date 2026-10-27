# LAN MCP: Scan Your LAN with AI

[![Python](https://img.shields.io/badge/Python-3.x-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-18_tools-green.svg)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Termux%20%2F%20Android-orange.svg)](https://termux.dev/)

[繁體中文版](README-zh-TW.md)

## What Is This

LAN MCP is a Model Context Protocol (MCP) server that runs on an Android phone. It is no longer just a small LAN scanner: it is a network audit tool. It lets an AI assistant scan and identify devices on a home local area network (LAN), audit hosts for exposed services and leaked credentials, read router information after login, check nearby Wi-Fi networks and encryption, and run local phone checks, without exposing LAN devices directly to the outside internet.

In short: the phone is already on the home Wi-Fi. The phone runs the server. A remote AI assistant connects to the phone, and the phone does the network audit work on its behalf.

This is useful when the AI assistant is running somewhere else (for example in the cloud) and cannot reach `192.168.x.x` addresses directly. The phone acts as a bridge between the remote AI and the home network.

## Who Is This For

Beginners are welcome. No server, no Docker host, no vendor controller, and no extra hardware are required. An Android phone, a home Wi-Fi network, and a free ngrok account are enough.

Basic familiarity with typing commands in a terminal is helpful. Every step below is written out in order, from zero.

## What It Can Do

- Scan a LAN subnet and list live hosts and open ports.
- Identify hosts by banner, HTTP title, SSDP, and mDNS.
- Ping hosts and probe TCP ports.
- Send one HTTP POST to a private-LAN address (for example a router login page).
- Log in to a router web page and fetch one page read-only, in a single server-side call (`auth_fetch`). The session cookie is used only inside the server, never returned and never stored.
- 18 tools in total (v1.7.1 major update): `device_info, lan_scan, ping, tcp_ports, banner, web_audit, audit_host, mqtt_probe, web_cred_context, http_probe, raw_probe, ssdp_probe, mdns_probe, identify_host, http_post, auth_fetch, router_clients, shell`. `identify_host` probes 80 and 8080 on several paths and matches fingerprints (for example a frmLoad JS shell on 80 plus a `no_cmd_match` JSON reply on 8080 points to a telecom optical modem family), `router_clients` reads a router client list after login, which is the accurate source for MAC addresses when the phone ARP table is empty, and `shell` runs one shell command on the phone itself. `device_info` now reports `network_mode` (`lan` on a private LAN, `non_lan` on mobile data); on a non-LAN network, LAN scanning is unavailable, but local tools (`device_info`, `shell`) still work. Public IP targets are rejected by default; they can be enabled only by an explicit owner switch (`MUSE_ALLOW_PUBLIC=1` at start, or an empty `~/.lan_mcp_allow_public` file on the phone), and the switch must be turned off again when idle.

## Operation Images

The three images below are illustrations, not real screenshots. Follow the commands in this README, not the commands drawn in the images.

![Architecture](images/operation-architecture.svg)

![One-Click Start](images/operation-start.svg)

![Identify Devices](images/operation-scan.svg)

## Step 1: Install Termux

Termux is a terminal app for Android. It provides a Linux environment on the phone, without root.

1. Install Termux from F-Droid (recommended; the Play Store version is outdated). Open Termux once so it finishes its first-time setup.
2. Update the package lists:
   ```
   pkg update && pkg upgrade -y
   ```

## Step 2: Install Python

In Termux, run:
```
pkg install -y python
python3 --version
```
Python 3 is required. No other Python packages are required: the server uses only the Python standard library.

## Step 3: Get These Files

In Termux, download this repository. Either method works:

Option A, with git:
```
pkg install -y git
git clone https://github.com/Devinkang666666/muse-lan-mcp.git
cp muse-lan-mcp/muse_lan_mcp.py ~/
cp muse-lan-mcp/start-lan.sh ~/
```

Option B, without git: download the repository ZIP from the GitHub page, extract it in Termux or in a file manager, then copy `muse_lan_mcp.py` and `start-lan.sh` to the Termux home directory (`~`).

After this step, `~/muse_lan_mcp.py` and `~/start-lan.sh` must exist.

## Step 4: Create a Token

The server requires a Bearer token when it is exposed through a tunnel. The token is a password chosen by the owner of the phone. It is never shared, never committed to a repository, and never sent to anyone else.

In Termux, create a token file (replace the example value with a long random value of your own):
```
printf 'your-long-random-token-here' > ~/.lan_mcp_token
chmod 600 ~/.lan_mcp_token
```
`start-lan.sh` reads this file at start time. For backward compatibility it also accepts the older file name `~/.hark_token` if `~/.lan_mcp_token` does not exist.

## Step 5: Install ngrok

ngrok creates a public tunnel address that forwards to the local MCP server on port 8765. A free ngrok account is enough for one tunnel at a time.

1. Download ngrok for Android ARM64 from the official ngrok download page. There is no ngrok package in the Termux repository, so do not use `pkg install ngrok`.
2. Extract it and place the `ngrok` binary where Termux can find it, for example `~/../usr/bin/ngrok`, then run:
   ```
   chmod +x ngrok
   ```
3. Register a free ngrok account, sign in, and copy the authtoken from Dashboard, Your Authtoken. The authtoken is private to its owner. Do not share it and do not write it into a public repository.
4. Configure it in Termux (replace the placeholder with the real authtoken):
   ```
   ngrok config add-authtoken <your own authtoken>
   ```
   After this, the authtoken lives only on the phone, in `~/.config/ngrok/ngrok.yml`.

## Step 6: Start With One Command

In Termux, run:
```
chmod +x ~/start-lan.sh
~/start-lan.sh
```
The script:
- stops any old ngrok tunnel first (a free account can hold only one domain at a time; a second tunnel reports `ERR_NGROK_334`),
- starts the MCP server in the background, or reuses it if it is already running,
- starts an ngrok tunnel to port 8765 in the background,
- prints a `started tunnel` line with a public address like `https://....ngrok-free.dev`.

That public address, plus the Bearer token from Step 4, is what an AI assistant needs to connect. The tunnel address is the MCP endpoint, with path `/mcp`, for example `https://....ngrok-free.dev/mcp`.

To stop everything later, run:
```
~/start-lan.sh stop
```
Do not leave the tunnel or the MCP server running long-term when it is not needed.

## Step 7: Connect an AI Assistant

In any MCP-compatible AI assistant, add a remote MCP server with:
- URL: the ngrok tunnel address from Step 6, with `/mcp` at the end,
- Authorization header: `Authorization: Bearer <the token from Step 4>`.

Then ask the assistant to scan the LAN. Always specify the home subnet explicitly, for example:
```json
{"subnet": "192.168.1.0/24"}
```
Do not rely on the default subnet: if the phone is on a VPN or Warp virtual path, the default subnet may become a virtual subnet and produce a false all-open result.

## Files

- `muse_lan_mcp.py`: the MCP server. Python standard library only. Listens on `0.0.0.0:8765`, path `/mcp`, JSON-RPC 2.0.
- `start-lan.sh`: one-click start and stop for the MCP server plus the ngrok tunnel. It contains no token, password, or tunnel domain. Tokens are read from a file at run time only.
- `images/`: SVG illustrations for the README.
- `LICENSE`: MIT License.
- `SECURITY.md`: security policy.

## Safety Rules

- Private LAN only by default. Public IP addresses are rejected unless the owner explicitly enables the public switch (`MUSE_ALLOW_PUBLIC=1` at start, or an empty `~/.lan_mcp_allow_public` file on the phone). Turn the switch off again when idle. Hostnames are allowed only when they resolve to a private or loopback address, unless the switch is on.
- Authentication. When `MUSE_TOKEN` is set, requests must carry `Authorization: Bearer <MUSE_TOKEN>`, otherwise they return 401. When exposed through ngrok, always set a token. Never run the server open.
- Read-only after login. `auth_fetch` only does GET after login and changes no device settings. Do not use `Set*` endpoints.
- Password handling. A login password is passed only at call time, converted to MD5 inside the server if the endpoint needs it, never written to a file or a log, and passwords in page snippets are masked.
- Secrets. Tokens, passwords, Cookie values, authtokens, and tunnel domains must never be written into a public repository or shared with other people. Each user scans their own LAN, not someone else's.

## Troubleshooting

- `ERR_NGROK_334` (domain already in use): stop the old tunnel first with `pkill ngrok`, then run `~/start-lan.sh` again. The script already does this on start.
- `lookup connect.ngrok-agent.com on [::1]:53: connection refused` (ngrok cannot resolve its own server): the phone DNS is broken. Create `~/resolv.conf` with two lines, the home router address (for example `nameserver 192.168.1.1`) and a public DNS (for example `nameserver 223.5.5.5`), then start ngrok with `proot -b ~/resolv.conf:/etc/resolv.conf ngrok ...`. `start-lan.sh` already starts ngrok this way.
- `started tunnel` never appears: check `~/ngrok-lan.log` for the ngrok error, and check that the authtoken from Step 5 was configured.
- MCP does not start: check `~/muse-lan.log`, check that `~/muse_lan_mcp.py` exists, and check that the token file from Step 4 exists.
- A scan returns every host with every port open: the subnet is probably a VPN or Warp virtual subnet, not the home LAN. Specify the home subnet explicitly, as in Step 7.

## License

MIT License. See `LICENSE`.

# SentinelMind: The Phone Sentinel with a Mind

> SentinelMind is an AI-powered phone network patrol tool, not just an MCP server. Sentinel is the guard, Mind is the brain: a sentinel that thinks, identifies, judges, and explains, not a scanner that only lists ports. MCP is only the underlying protocol that connects the AI to the phone. What the user gets is a phone that patrols the network, with audit-grade inspection.
>
> Zero Hardware, Phone as the Patrol Device: no extra hardware to buy, no appliance to deploy. The phone you already carry is the patrol device.
>
> Zero Hardware, Phone as the Patrol Device: no extra hardware to buy, no appliance to deploy. The phone you already carry is the patrol device.

[![Python](https://img.shields.io/badge/Python-3.x-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-18_tools-green.svg)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Termux%20%2F%20Android-orange.svg)](https://termux.dev/)

[繁體中文版](README-zh-TW.md)

## What Is This

This is SentinelMind, an AI-powered phone network patrol tool. The phone itself is the patrol device: it runs on an Android phone, and an AI assistant patrols the network through the phone, with audit-grade inspection. The AI is the core: it identifies devices, judges risks, and explains findings, not just lists ports. It is no longer positioned as an MCP server, and no longer just a small LAN scanner.

It lets an AI assistant scan and identify devices on a home local area network (LAN), audit hosts for exposed services and leaked credentials, read router information after login, check nearby Wi-Fi networks and encryption, and run local phone checks, without exposing LAN devices directly to the outside internet.

In short: the phone is already on the home Wi-Fi. The phone runs the audit service. A remote AI assistant connects to the phone, and the phone does the network audit work on its behalf.

This is useful when the AI assistant is running somewhere else (for example in the cloud) and cannot reach `192.168.x.x` addresses directly. The phone acts as a bridge between the remote AI and the home network. Under the hood the connection uses the Model Context Protocol (MCP), but that is an implementation detail, not the product.

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

## Two Modes: MCP Mode and Shell Mode

Note: SentinelMind has two modes. Do not confuse them.

- **MCP Mode (for other AI assistants):** the 18 tools above, over the MCP protocol. This is the mode for AI assistants that only speak MCP, such as Claude Desktop. It packages field-tested experience (fingerprinting, router login read-only, password masking, private-LAN default) into ready tools, with safety boundaries.
- **Shell Mode (for direct personal use):** direct Termux control. The `shell` tool runs one shell command on the phone itself, and a direct Termux connection runs any Termux command. For the phone owner, Shell Mode is faster and freer than MCP Mode, and MCP tools are redundant once direct control works. Shell Mode is full access: it can change and delete things, so use it only on your own phone, and stop the tunnel when done.

In short: Shell Mode for yourself, MCP Mode for other people's AI.

## Usage Examples

Audit in this order, and record the evidence for each step instead of guessing an identity from one signal:

1. Confirm the phone is on the LAN: `device_info` must show `network_mode: "lan"` and a private local IP. On mobile data it shows `non_lan`; LAN tools are then unavailable, but `device_info` and `shell` still work.
2. Scan the subnet explicitly: `lan_scan` with `{"subnet": "192.168.1.0/24"}`. List live hosts and open ports first, before naming anything.
3. Identify a host: `identify_host` probes several paths on ports 80 and 8080 and matches fingerprints. A family-level result (for example a telecom optical modem family) is not an exact model; an exact model requires a logged-in version page or a device label.
4. Audit a host: `audit_host` checks for exposed services and leaked credentials in unauthenticated page content. An empty result means nothing is visible without login, not that the firmware or configuration is secure.
5. Read router clients after login: `router_clients` logs in and reads the router client list read-only. This is the authoritative IP, MAC, and hostname source, because Android Termux cannot read the phone ARP table (see Known Issues).
6. Check nearby Wi-Fi: on the phone, `termux-wifi-scaninfo` (requires the Termux:API app and location permission) lists nearby SSIDs, signal, and encryption. A connected phone can still report a saved SSID even when the access point hides it, so a hidden-network claim must be checked on the router settings page, not from the phone scan alone.
7. Turn off when done: `~/start-lan.sh stop`, and remove the `~/.lan_mcp_allow_public` file if the public switch was enabled.

## Known Issues and How They Were Solved

These are real problems met during field testing on Android/Termux, written here so the next user does not repeat them:

- **Tunnel domain already in use (`ERR_NGROK_334`).** Cause: a free ngrok account holds only one tunnel at a time; a second tunnel on the same domain collides. Fix: stop the old tunnel first (`~/start-lan.sh stop`, or `pkill ngrok`), then start again. Do not enable tunnel pooling on a free account.
- **ngrok cannot resolve its own server (`lookup ... on [::1]:53: connection refused`).** Cause: the Termux dead resolver `[::1]:53` does not answer. Fix: create `~/resolv.conf` with the home router address and a public DNS, and start ngrok through `proot -b ~/resolv.conf:/etc/resolv.conf`. `start-lan.sh` already does this.
- **Tunnel heartbeat timeouts and HTTP 404 from the public address.** Cause: the tunnel session dropped (network switch, Wi-Fi disconnect, or unstable mobile data), while the local MCP process kept running. A `start-lan.sh` completion line alone does not prove the tunnel is up. Fix: confirm a fresh `started tunnel` line in `~/ngrok-lan.log` and a successful remote `device_info` call; if the tunnel is down, run `~/start-lan.sh stop` then `~/start-lan.sh` on a stable Wi-Fi connection.
- **A scan returns every host with every port open.** Cause: the phone is on a VPN or Warp virtual path, so the default subnet is a virtual subnet, not the home LAN. Fix: always specify the home subnet explicitly, as in Step 7, and check `device_info` first.
- **MAC addresses are empty in `lan_scan`.** Cause: Android sandbox/SELinux blocks Termux from reading `/proc/net/arp` (`Permission denied`) and from `ip neigh` netlink. This is a platform limit, not a code omission. Fix: use `router_clients` after router login for authoritative MAC data.
- **Router login succeeds once, then fails with the same password.** Cause: some routers (observed on Tenda) allow only one online session, or lock after repeated failures. A single failure does not prove the password is wrong. Fix: check the login-info endpoint for lock state and remaining attempts, wait for the lock to clear, and retry once; never brute-force.
- **A router settings page shows only an overview, not the detailed dialog.** Cause: the settings UI is an icon grid; the detail dialog opens only from its specific tile, and a click on the surrounding text container returns the same overview. Fix: click the exact tile (for example "Wireless Name and Password"), and verify from the dialog fields themselves, not from the overview summary.
- **Termux:API commands time out with no output.** Cause: the `termux-api` package is installed, but the companion Termux:API Android app is missing (Termux:X11 is a different app, a graphical display, not the API app). Fix: install the matching Termux:API app (F-Droid build for an F-Droid Termux install), then grant location permission for Wi-Fi SSID results; without it, SSIDs report as `<unknown ssid>` and MACs as `02:00:00:00:00:00`.
- **Update does not take effect after copying a new file.** Cause: `start-lan.sh` reuses a running MCP process instead of restarting it. Fix: always run `~/start-lan.sh stop` before `~/start-lan.sh` after an update, and use a versioned file name (for example `muse_lan_mcp_v171.py`) so the correct copy is installed.
- **Identification stops at family level.** Cause: internet search and unauthenticated probing can classify a device family, but cannot establish an exact model. Fix: treat family-level results as suspects, and confirm the exact model only from a logged-in version page or a device label. Do not present a family guess as a confirmed model, and do not use a vendor "super password" to bypass login.

## Troubleshooting

- `ERR_NGROK_334` (domain already in use): stop the old tunnel first with `pkill ngrok`, then run `~/start-lan.sh` again. The script already does this on start.
- `lookup connect.ngrok-agent.com on [::1]:53: connection refused` (ngrok cannot resolve its own server): the phone DNS is broken. Create `~/resolv.conf` with two lines, the home router address (for example `nameserver 192.168.1.1`) and a public DNS (for example `nameserver 223.5.5.5`), then start ngrok with `proot -b ~/resolv.conf:/etc/resolv.conf ngrok ...`. `start-lan.sh` already starts ngrok this way.
- `started tunnel` never appears: check `~/ngrok-lan.log` for the ngrok error, and check that the authtoken from Step 5 was configured.
- MCP does not start: check `~/muse-lan.log`, check that `~/muse_lan_mcp.py` exists, and check that the token file from Step 4 exists.
- A scan returns every host with every port open: the subnet is probably a VPN or Warp virtual subnet, not the home LAN. Specify the home subnet explicitly, as in Step 7.

## License

MIT License. See `LICENSE`.

# LAN MCP: Scan Your LAN with AI

[繁體中文版](README-zh-TW.md)

## Overview

Run an MCP server locally on your phone, so your AI assistant (for example Muse) can scan and identify devices on your home LAN from the phone, and view router information read-only after login. Your LAN devices are not exposed directly to an external AI; the AI only sends requests through this phone-local server.

## Operation Images

The three images below are illustrations, not real screenshots. Do not copy commands from the images; follow the commands in this README. Images are in SVG vector format, so they can be pushed directly to the repository.

![Architecture](images/operation-architecture.svg)

![One-Click Start](images/operation-start.svg)

![Identify Devices](images/operation-scan.svg)

## Files

- `muse_lan_mcp.py`: the MCP server itself, Python standard library only, listening on `0.0.0.0:8765`, path `/mcp`, JSON-RPC 2.0.
  - Tools (16 in total): `device_info, lan_scan, ping, tcp_ports, banner, web_audit, audit_host, mqtt_probe, web_cred_context, http_probe, raw_probe, ssdp_probe, mdns_probe, identify_host, http_post, auth_fetch`.
  - `auth_fetch`: in a single call, POST to log in, take the Cookie, then do one read-only GET. The Cookie is used only inside the server, never returned and never stored.
- `start-lan.sh`: one-click start. It starts MCP + an ngrok tunnel (forwarding to 8765), in the background, with logs kept. Stop with `~/start-lan.sh stop`.

## Safety Rules (Do Not Remove)

- Private LAN only: public IPs are always rejected; hostnames are allowed only when they resolve to a private or loopback address.
- Authentication: when the environment variable `MUSE_TOKEN` is set, requests must carry `Authorization: Bearer <MUSE_TOKEN>`, otherwise they return 401. When exposed via ngrok, always set a token; never run it open.
- Read-only first: after login, `auth_fetch` only does GET and changes no device settings. Do not use `Set*` endpoints.
- Password handling: a login password is passed only at call time, converted to MD5 inside the server if the endpoint needs it, never written to a file or a log, and passwords in page snippets are masked.
- Tokens, passwords, Cookie values and tunnel domains must never be written into a public repository. `start-lan.sh` only reads the existing `~/.hark_token` on your phone and contains no real secret.

## Prerequisites

- Termux on your phone, Python 3, and `~/muse_lan_mcp.py` already in place.
- A token file `~/.hark_token`, containing a Bearer token you chose yourself. Other users must create their own; do not share yours.
- ngrok (see the tunnel section below; without a configured authtoken, no tunnel will start).

## Set Up the Tunnel (First Time Only)

`start-lan.sh` only starts a tunnel; it does not install ngrok or configure an authtoken for you. Do the steps below once, the first time only:
1. Install ngrok: on the ngrok download page, choose the Android ARM64 build, download and extract it, put `ngrok` somewhere Termux can find (for example `~/../usr/bin/ngrok`), then `chmod +x ngrok`. There is no ngrok package in the Termux repository; do not look for it with `pkg install ngrok`.
2. Register a free ngrok account, sign in, and on the Dashboard, Your Authtoken page, copy your own authtoken. It is yours alone; do not share it and do not write it into a public repository.
3. Configure the authtoken in Termux on your phone (replace the placeholder with your own token; do not paste it anywhere public):
   ```
   ngrok config add-authtoken <your own authtoken>
   ```
   After that, the token lives only on your phone, in `~/.config/ngrok/ngrok.yml`.
4. Test the tunnel once (without waiting for the one-click script):
   ```
   proot -b ~/resolv.conf:/etc/resolv.conf ngrok http 8765 --log=stdout --inspect=false
   ```
   If you see `started tunnel` and an address like `https://....ngrok-free.dev`, the tunnel works, and that address is the address your AI assistant should connect to. Stop it with Ctrl+C and let the one-click script start it later.
5. If ngrok reports `lookup connect.ngrok-agent.com on [::1]:53: connection refused` (cannot resolve the ngrok server), your phone DNS is broken: write a `~/resolv.conf` with two lines, your home router address (for example `nameserver 192.168.1.1`) and a public DNS (for example `nameserver 223.5.5.5`), then always start ngrok the way step 4 does, with `proot -b ~/resolv.conf:/etc/resolv.conf ngrok ...`. `start-lan.sh` already starts it this way.
6. A free ngrok account can hold only one domain at a time. Starting a second tunnel reports `ERR_NGROK_334` (domain already in use). Run `pkill ngrok` to stop the old one first, then start the new one. `start-lan.sh` already does this.
7. Stop the tunnel when you are done. Leaving it open publishes your phone MCP server on the public internet; even with a Bearer token, do not leave it open long-term.

## One-Click Start

```
cp start-lan.sh ~/start-lan.sh
chmod +x ~/start-lan.sh
~/start-lan.sh
```

After it runs, you will see the MCP listening line and the ngrok `started tunnel` line. The tunnel address is the address your AI assistant should connect to. Give that address to your MCP client (for example Muse's Lan Mcp connector); the client must carry your Bearer token to call it.

When scanning the LAN, always specify the subnet explicitly, for example:
```json
{"subnet": "192.168.1.0/24"}
```
Do not rely on the default subnet: if the phone is on a VPN / Warp virtual path, the default subnet may become a virtual subnet and produce a false all-open result.

## How Others Use It

1. Download these files (this README, `muse_lan_mcp.py`, `start-lan.sh`).
2. Put `muse_lan_mcp.py` in Termux on your own phone, and create your own `~/.hark_token`.
3. Run `~/start-lan.sh` and get your tunnel address.
4. Connect that MCP address in your own AI assistant, with your own Bearer token, and you can scan your own LAN and identify your own devices.
5. Other people scan their own LAN, not yours. Do not send them your tunnel address, token or password.

## Stop

```
~/start-lan.sh stop
```

Stop when you are done; do not leave the tunnel or MCP running long-term.

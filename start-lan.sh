#!/usr/bin/env bash
# One-click start: phone-local MCP + ngrok tunnel (to 8765)
# Usage: ~/start-lan.sh        start
#        ~/start-lan.sh stop   stop MCP + ngrok
# Contains no token or password. The token is read at run time only,
# from ~/.lan_mcp_token if it exists, otherwise from ~/.hark_token
# (older name, kept for backward compatibility). It is never written
# into this file or into a log.
set -u
cd "$HOME" || exit 1

MCP_FILE="$HOME/muse_lan_mcp.py"
MCP_LOG="$HOME/muse-lan.log"
MCP_PID="$HOME/muse-lan.pid"
NGROK_LOG="$HOME/ngrok-lan.log"
NGROK_PID="$HOME/ngrok-lan.pid"
RESOLV="$HOME/resolv.conf"

stop_all() {
  echo "Stopping old ngrok / MCP..."
  pkill ngrok 2>/dev/null || true
  if [ -f "$MCP_PID" ]; then
    kill "$(cat "$MCP_PID")" 2>/dev/null || true
    rm -f "$MCP_PID"
  fi
  # This script runs as a file, so its own command line does not contain muse_lan_mcp.py, so -f is safe here.
  # (Pasting pkill -f directly via bash -c in a dialog can kill itself; that is a different case.)
  pkill -f "muse_lan_mcp.py" 2>/dev/null || true
  sleep 2
  echo "Stopped."
}

if [ "${1:-}" = "stop" ]; then
  stop_all
  exit 0
fi

if [ ! -f "$MCP_FILE" ]; then
  echo "Not found: $MCP_FILE. Copy muse_lan_mcp.py to your home directory first, then run this script."
  exit 1
fi
if [ -f "$HOME/.lan_mcp_token" ]; then
  TOKEN_FILE="$HOME/.lan_mcp_token"
elif [ -f "$HOME/.hark_token" ]; then
  TOKEN_FILE="$HOME/.hark_token"
else
  echo "No token file found. Create ~/.lan_mcp_token first (see README Step 4)."
  exit 1
fi

# DNS workaround file: create if missing, reuse if present, never overwrite.
if [ ! -f "$RESOLV" ]; then
  printf 'nameserver 192.168.1.1\nnameserver 223.5.5.5\n' > "$RESOLV"
  echo "Created $RESOLV"
fi

# Stop the old tunnel first, so a free domain is not held by it (ERR_NGROK_334).
pkill ngrok 2>/dev/null || true
sleep 2

# MCP: reuse if already running, do not start twice.
if pgrep -f "muse_lan_mcp.py" >/dev/null 2>&1; then
  echo "MCP is already running, reusing it, not restarting."
else
  echo "Starting MCP..."
  nohup env MUSE_TOKEN="$(cat "$TOKEN_FILE")" python3 "$MCP_FILE" > "$MCP_LOG" 2>&1 &
  echo $! > "$MCP_PID"
  sleep 3
  if grep -q "listening on 0.0.0.0:8765" "$MCP_LOG" 2>/dev/null; then
    echo "MCP started: $(grep 'listening' "$MCP_LOG" | tail -1)"
  else
    echo "MCP log (first 20 lines):"
    head -20 "$MCP_LOG" 2>/dev/null || true
  fi
fi

# ngrok tunnel: forward local 8765, run in background, keep log.
echo "Starting ngrok tunnel (to 8765)..."
nohup proot -b "$RESOLV:/etc/resolv.conf" ngrok http 8765 --log=stdout --inspect=false > "$NGROK_LOG" 2>&1 &
echo $! > "$NGROK_PID"
for i in 1 2 3 4 5 6 7 8 9 10; do
  if grep -q "started tunnel" "$NGROK_LOG" 2>/dev/null; then
    break
  fi
  sleep 1
done
grep "started tunnel" "$NGROK_LOG" 2>/dev/null | tail -1 || echo "No started tunnel line yet, check $NGROK_LOG to confirm."
echo "Done. MCP log: $MCP_LOG, ngrok log: $NGROK_LOG"
echo "To stop: ~/start-lan.sh stop"

#!/usr/bin/env bash
# 一鍵啟動：手機本地 MCP + ngrok 隧道（轉 8765）
# 用法：~/start-lan.sh        啟動
#       ~/start-lan.sh stop   停掉 MCP + ngrok
# 不含任何令牌、密碼，令牌一律即時讀 ~/.hark_token，不寫進本檔、不寫進日誌。
set -u
cd "$HOME" || exit 1

MCP_FILE="$HOME/muse_lan_mcp.py"
MCP_LOG="$HOME/muse-lan.log"
MCP_PID="$HOME/muse-lan.pid"
NGROK_LOG="$HOME/ngrok-lan.log"
NGROK_PID="$HOME/ngrok-lan.pid"
RESOLV="$HOME/resolv.conf"

stop_all() {
  echo "正在停掉舊的 ngrok / MCP..."
  pkill ngrok 2>/dev/null || true
  if [ -f "$MCP_PID" ]; then
    kill "$(cat "$MCP_PID")" 2>/dev/null || true
    rm -f "$MCP_PID"
  fi
  # 本腳本是檔案執行，自己的命令列不含 muse_lan_mcp.py，所以 -f 在這裡是安全的
  # （直接在對話框用 bash -c 貼 pkill -f 才會自殺，那是另一回事）
  pkill -f "muse_lan_mcp.py" 2>/dev/null || true
  sleep 2
  echo "已停。"
}

if [ "${1:-}" = "stop" ]; then
  stop_all
  exit 0
fi

if [ ! -f "$MCP_FILE" ]; then
  echo "找不到 $MCP_FILE，先把 muse_lan_mcp.py 放到家目錄再跑本腳本。"
  exit 1
fi
if [ ! -f "$HOME/.hark_token" ]; then
  echo "找不到 ~/.hark_token，令牌檔缺了就起不了 MCP。"
  exit 1
fi

# DNS  workaround 檔：沒有就建一個，有就沿用，不覆蓋
if [ ! -f "$RESOLV" ]; then
  printf 'nameserver 192.168.1.1\nnameserver 223.5.5.5\n' > "$RESOLV"
  echo "已建 $RESOLV"
fi

# 先停舊隧道，避免免費域名被舊隧道佔住報 ERR_NGROK_334
pkill ngrok 2>/dev/null || true
sleep 2

# MCP：已在跑就沿用，不重複起
if pgrep -f "muse_lan_mcp.py" >/dev/null 2>&1; then
  echo "MCP 已在跑，沿用，不重起。"
else
  echo "正在起 MCP..."
  nohup env MUSE_TOKEN="$(cat "$HOME/.hark_token")" python3 "$MCP_FILE" > "$MCP_LOG" 2>&1 &
  echo $! > "$MCP_PID"
  sleep 3
  if grep -q "listening on 0.0.0.0:8765" "$MCP_LOG" 2>/dev/null; then
    echo "MCP 已起：$(grep 'listening' "$MCP_LOG" | tail -1)"
  else
    echo "MCP 日誌（前 20 行）："
    head -20 "$MCP_LOG" 2>/dev/null || true
  fi
fi

# ngrok 隧道：轉本地 8765，後台跑，日誌留檔
echo "正在起 ngrok 隧道（轉 8765）..."
nohup proot -b "$RESOLV:/etc/resolv.conf" ngrok http 8765 --log=stdout --inspect=false > "$NGROK_LOG" 2>&1 &
echo $! > "$NGROK_PID"
for i in 1 2 3 4 5 6 7 8 9 10; do
  if grep -q "started tunnel" "$NGROK_LOG" 2>/dev/null; then
    break
  fi
  sleep 1
done
grep "started tunnel" "$NGROK_LOG" 2>/dev/null | tail -1 || echo "隧道還沒出 started tunnel，看 $NGROK_LOG 再確認。"
echo "完成。MCP 日誌：$MCP_LOG，ngrok 日誌：$NGROK_LOG"
echo "停掉用：~/start-lan.sh stop"

#!/bin/bash
# 停止并取消 lecture-live 本机常驻 (含 Ollama / 应用 / 隧道)
# 同时也会清理可能残留的独立进程（包括沙箱后台任务启动的）
# 用法: 在自己 Mac 的「终端」里执行  bash /Users/asaki/WorkBuddy/2nd/lecture-live/uninstall-service.sh
set -e
AGENTS="$HOME/Library/LaunchAgents"
unload_one() {
  local p="$1"
  local name
  name="$(basename "$p" .plist)"
  launchctl unload "$p" 2>/dev/null || launchctl bootout "gui/$(id -u)/$name" 2>/dev/null || true
  echo "  ✔ 已卸载 $name"
}
echo "==> 停止 lecture-live 常驻服务"
unload_one "$AGENTS/com.lecturelive.tunnel.plist"
unload_one "$AGENTS/com.lecturelive.app.plist"
unload_one "$AGENTS/com.lecturelive.ollama.plist"

echo "==> 清理可能残留的独立进程（包括沙箱后台任务、手动起的进程）"
pkill -f "lecture-live/app.py" 2>/dev/null || true
pkill -f "ollama serve" 2>/dev/null || true
pkill -f "cloudflared tunnel" 2>/dev/null || true
pkill -f "lecture-live/bin/ngrok" 2>/dev/null || true
pkill -x "ngrok" 2>/dev/null || true
sleep 2
echo "🛑 已停止并取消常驻。端口 5000 / 11434 与公网隧道均已关闭。"

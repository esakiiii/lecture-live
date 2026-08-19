#!/bin/bash
# lecture-live 本机常驻: 用 macOS launchd 注册为「登录自启 + 崩溃自动重启」
# 包含三项: Ollama(生成笔记所需的本地模型服务) / lecture-live 应用 / Cloudflare 公网隧道
# 用法: 在自己 Mac 的「终端」里执行  bash /Users/asaki/WorkBuddy/2nd/lecture-live/install-service.sh
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENTS="$HOME/Library/LaunchAgents"

echo "==> 第一步：先彻底清理旧注册和残留进程"
bash "$SCRIPT_DIR/uninstall-service.sh" || true

echo
load_one() {
  local p="$1"
  local name="$(basename "$p")"
  echo "  · 加载 $name ..."
  if launchctl load "$p" 2>/dev/null; then
    echo "    ✔ 已加载 $name (launchctl load)"
  elif launchctl bootstrap "gui/$(id -u)" "$p" 2>/dev/null; then
    echo "    ✔ 已加载 $name (launchctl bootstrap)"
  else
    echo "    ✗ 加载 $name 失败，请检查日志或手动运行: launchctl bootstrap gui/$(id -u) $p" >&2
    exit 1
  fi
}

echo "==> 第二步：注册常驻服务 (Ollama → 应用 → 隧道)"
load_one "$AGENTS/com.lecturelive.ollama.plist"
sleep 2
load_one "$AGENTS/com.lecturelive.app.plist"
sleep 2
load_one "$AGENTS/com.lecturelive.tunnel.plist"

echo
echo "✅ 完成。三项服务现在开机自启、崩溃自动重启。"
echo
launchctl list | grep lecturelive || echo "(launchctl 列表未立即显示也属正常，可等 5 秒后重查)"
echo
echo "常用维护命令:"
echo "  状态:      launchctl list | grep lecturelive"
echo "  本地验证:  curl -s localhost:5000/health"
echo "  域名(等隧道建立后):  grep -oE 'https://[a-zA-Z0-9.-]+\\.trycloudflare\\.com' $SCRIPT_DIR/logs/tunnel.err | tail -1"
echo "  停止常驻:  bash $SCRIPT_DIR/uninstall-service.sh"

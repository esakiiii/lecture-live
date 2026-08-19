#!/usr/bin/env bash
#
# install_ollama.sh — 一键安装 Ollama 并拉取本地整理模型
# 适用：macOS（Intel / Apple Silicon），面向国内网络 + Verge 代理用户
#
set -euo pipefail

# ---------- 颜色输出 ----------
if [ -t 1 ]; then
  C_R=$'\033[31m'; C_G=$'\033[32m'; C_Y=$'\033[33m'; C_B=$'\033[34m'; C_N=$'\033[0m'
else
  C_R=""; C_G=""; C_Y=""; C_B=""; C_N=""
fi
info(){ printf "${C_B}[info]${C_N} %s\n" "$*"; }
ok(){   printf "${C_G}[ ok ]${C_N} %s\n" "$*"; }
warn(){ printf "${C_Y}[warn]${C_N} %s\n" "$*"; }
err(){  printf "${C_R}[fail]${C_N} %s\n" "$*"; }

# ---------- 平台检查 ----------
if [ "$(uname)" != "Darwin" ]; then
  err "本脚本仅支持 macOS（Windows/Linux 请手动按 README 安装）。"
  exit 1
fi

# ---------- 模型选择（第一个参数，默认 3b）----------
MODEL="${1:-qwen2.5:3b}"
info "将安装 Ollama 并拉取模型: $MODEL"
info "（如需更大/更准的模型，运行: ./install_ollama.sh qwen2.5:7b）"

# ---------- 0. Homebrew ----------
if command -v brew >/dev/null 2>&1; then
  ok "已检测到 Homebrew: $(brew --version 2>/dev/null | head -1)"
else
  warn "未检测到 Homebrew，使用清华镜像安装……"
  /bin/bash -c "$(curl -fsSL https://mirrors.tuna.tsinghua.edu.cn/macos/brew/install.sh)"
  if [ -x /opt/homebrew/bin/brew ]; then
    eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [ -x /usr/local/bin/brew ]; then
    eval "$(/usr/local/bin/brew shellenv)"
  else
    err "Homebrew 安装后仍找不到 brew，请重启终端后重试。"
    exit 1
  fi
  ok "Homebrew 安装完成"
fi

# ---------- 1. 安装 Ollama ----------
if command -v ollama >/dev/null 2>&1; then
  ok "Ollama 已安装: $(ollama --version 2>/dev/null || echo unknown)"
else
  info "通过 brew 安装 Ollama……"
  brew install ollama
  ok "Ollama 安装完成"
fi

# ---------- 2. 启动 Ollama 服务 ----------
is_up(){ curl -s -o /dev/null -w "%{http_code}" --max-time 3 http://localhost:11434 >/dev/null 2>&1; }
if is_up; then
  ok "Ollama 服务已在运行 (localhost:11434)"
else
  info "启动 Ollama 服务……"
  if brew services start ollama >/dev/null 2>&1; then
    ok "已通过 brew services 后台启动"
  else
    warn "brew services 不可用，改用 nohup 后台启动"
    nohup ollama serve >/tmp/ollama.log 2>&1 &
  fi
  for i in $(seq 1 30); do
    if is_up; then break; fi
    sleep 1
  done
  if is_up; then ok "Ollama 服务就绪 (localhost:11434)"; else err "Ollama 未就绪，查看 /tmp/ollama.log"; fi
fi

# ---------- 3. 代理自检（国内网络关键）----------
if [ -z "${HTTPS_PROXY:-}" ] && [ -z "${https_proxy:-}" ]; then
  if ! curl -sI --max-time 8 https://registry.ollama.ai >/dev/null 2>&1; then
    warn "当前未设置 HTTPS_PROXY，且无法直连 ollama 模型服务器。"
    warn "请确认 Verge 处于 TUN / 系统代理模式（会接管全系统流量，无需手动设代理）。"
    warn "若只用『仅终端代理』模式，请先执行: export HTTPS_PROXY=http://127.0.0.1:7890（端口以 Verge 实际设置为准）"
    if [ -t 0 ]; then
      printf "${C_Y}仍要继续拉取模型？${C_N} (y/N) "
      read -r ans
      case "$ans" in y|Y|yes|YES) ;; *) err "已取消，请配置代理后重试。"; exit 1;; esac
    fi
  fi
fi

# ---------- 4. 拉取模型 ----------
info "拉取模型 $MODEL（首次约需下载数 GB，请保持 Verge 开启，耐心等待）……"
ollama pull "$MODEL"
ok "模型 $MODEL 拉取完成"

# ---------- 5. 完成 ----------
info "已安装模型列表："
ollama list
echo
ok "Ollama 已就绪！重启 lecture-live 服务即可使用「生成笔记」："
echo "    cd lecture-live && ./run.sh"
echo "然后 Chrome 打开 http://localhost:5000"

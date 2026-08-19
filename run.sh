#!/usr/bin/env bash
# 课堂实时转写笔记 —— 一键启动脚本（macOS）
set -e
cd "$(dirname "$0")"

# 使用 WorkBuddy 管理的 Python，避免污染系统环境
PY="/Users/asaki/.workbuddy/binaries/python/versions/3.13.12/bin/python3"
VENV="venv"

# 国内镜像：让 faster-whisper 从 hf-mirror.com 下载模型（无需翻墙）
export HF_ENDPOINT="https://hf-mirror.com"
# 禁用 HuggingFace 新版 Xet 存储，避免国内网络出现 401 下载失败
export HF_HUB_DISABLE_XET=1
# 代理（可选）：若你的 Verge 正在本机 7890 端口，可取消下一行注释加速下载
# export HTTPS_PROXY="http://127.0.0.1:7890" HTTP_PROXY="http://127.0.0.1:7890"

if [ ! -d "$VENV" ]; then
  echo "[setup] 创建虚拟环境..."
  "$PY" -m venv "$VENV"
fi
source "$VENV/bin/activate"

echo "[setup] 安装 Python 依赖..."
pip install -q -r requirements.txt

# 可在此处覆盖默认参数（模型越大越准但越慢）：
#   base  最小最快、识别率最低；small 兼顾速度与准确率（默认）；medium 更准但明显更慢；large-v3 最准但 CPU 上很慢
export WHISPER_MODEL="${WHISPER_MODEL:-small}"
export WHISPER_LANG="${WHISPER_LANG:-zh}"
export OLLAMA_MODEL="${OLLAMA_MODEL:-qwen2.5:7b}"

echo "[run] 启动服务： http://localhost:5000"
python app.py

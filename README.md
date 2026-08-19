# 课堂实时转写笔记（本地版）

像 Typeless 一样，把老师上课说的话实时转成文字，并**像会议总结一样自动整理成结构化笔记**。
支持**人声降噪**、**说话人分离**、**一键推送到本机「备忘录」**。
全部在本机运行，**离线、免费、数据不出电脑**——契合国内网络与个人隐私需求。

---

## 功能

- 🎙️ **实时流式转写**：浏览器采集麦克风 → 本地 Whisper 逐段识别，边说边出字
- 🔇 **人声降噪**：喂给 Whisper 前先用轻量频谱门限提纯人声（numpy/scipy，无模型下载，离线）
- 🗣️ **说话人分离**：VAD 切出说话轮次 + MFCC 声纹嵌入 + 聚类，标注「说话人A / B …」（轻量本地，无需 HF token）
- 🧠 **智能整理**：把整段原文交给本机 Ollama（qwen2.5），像会议总结一样提炼重点
- 🗂️ **三套模板**：结构化课堂笔记（要点/概念/疑问/待办）、大纲+关键词、问答对
- 🍎 **推送到备忘录**：一键把原文推到本机 macOS「备忘录」(Notes.app)，彻底本地
- 🗄 **归档本节课**：一键把「原文 + 三套笔记 + 本场录音」落盘到 `archive/` 目录（Markdown + WAV），并自动维护 `INDEX.md` 索引，方便长期留存与回溯
- 🌐 **公网分享**：用 ngrok v3 dev domain 拿到**固定 HTTPS 地址**，朋友在 Windows Chrome 直接打开就能用（HTTPS 下浏览器自动允许麦克风）。配套 `SHARE_MODE=1` 自动禁掉「推送到备忘录」和「归档」两个写入口，保护你本机
- 🚀 **本机常驻**：`./install-service.sh` 一键注册 macOS launchd 三件套（Ollama / 应用 / 公网隧道），开机自启、崩溃自动重启
- ⚡ **低延迟**：说话人分离用「最近 N 段滑动窗口」聚类，前端每 3 秒一段实时刷，彻底告别 2 分钟滞后

## 架构

```
浏览器 (Chrome)
  ├─ AudioWorklet @16k 采集麦克风 → 封装 WAV 分块
  ├─ POST /transcribe ──────────► Flask 后端
  │     ├─ [可选] 频谱降噪
  │     ├─ faster-whisper (本地) 逐段识别（词级时间戳）
  │     └─ [可选] 说话人分离（VAD + MFCC 嵌入 + 聚类）→ 返回带 speaker 的 segments
  ├─ 点击「生成笔记」POST /summarize ─► Ollama (本机 qwen2.5) 整理成 JSON 模板
  └─ 点击「推送到备忘录」POST /push-notes ─► AppleScript 在 Notes.app 新建笔记（仅 macOS）
```

## 前置条件

1. **Chrome 浏览器**（Safari 对 `AudioContext({sampleRate:16000})` 支持不稳定）
2. **Python 3.10+**（已用 WorkBuddy 管理的 3.13）
3. **Ollama**（仅「智能整理」需要）：详见下方「安装 Ollama（智能整理用）」一节，或用一键脚本 `./install_ollama.sh`
4. **首次运行**会下载 Whisper 模型（走 `hf-mirror.com` 国内镜像）与安装 scipy/sklearn 等依赖
5. **推送到备忘录仅支持 macOS**；首次使用需在「系统设置 → 隐私与安全性 → 自动化」中允许终端/Terminal「控制 备忘录」（否则会报 `-10004 权限违例`，点允许即可）

## 安装 Ollama（智能整理用）

「生成笔记」依赖本机运行的 Ollama（qwen2.5 等本地模型）。纯转写不需要它，**安装一次即可永久离线使用**。

### 一键脚本（推荐）

```bash
chmod +x install_ollama.sh
./install_ollama.sh            # 默认拉 qwen2.5:3b
./install_ollama.sh qwen2.5:7b # 想要更准的模型，传参即可
```

脚本会自动：检测/安装 Homebrew（清华镜像）→ 安装并启动 Ollama → 做代理自检 → 拉取模型。

### 手动步骤

```bash
brew install ollama            # 或去 https://ollama.com/download 下载 .dmg
ollama serve                   # 若用 .app 双击即后台运行；brew 版用 brew services start ollama
ollama pull qwen2.5:3b         # 内存紧/求快用 3b；质量优先用 7b；14b 在 Intel Mac 上慎重
ollama list                    # 能看到模型即成功
```

### 接入本项目

Ollama 默认监听本机 `11434`，本项目 `/summarize` 已自动连接，**无需改代码**。装好后重启服务：

```bash
cd lecture-live && ./run.sh
```

Chrome 打开 `localhost:5000` → 录完音点「生成笔记」即走本地模型出三套模板。

### 国内网络 / 代理排错

| 现象 | 原因 | 解决 |
|------|------|------|
| `ollama pull` 卡住 / 极慢 | 终端流量未走代理 | 开启 Verge **TUN / 系统代理模式**（接管全系统），或 `export HTTPS_PROXY=http://127.0.0.1:7890` 后重试 |
| `registry.ollama.ai` 连接超时 | 未连外网 | 确认 Verge 已连上、能正常访问其他海外站点 |
| 拉到一半中断 | 网络抖动 | 重新执行 `ollama pull <模型>` 会断点续传 |
| Intel Mac 上生成笔记很慢 | 纯 CPU 推理 7b | 换 `qwen2.5:3b`，生成时耐心等待（约 10~30 秒） |

> 端口说明：Verge 默认 HTTP 代理端口常见为 `7890`，若你改过，以实际设置为准。

## 运行

```bash
cd lecture-live
chmod +x run.sh
./run.sh
```

Chrome 打开 **http://localhost:5000**：

1. 点「开始录音」并授权麦克风（可勾选「降噪」「说话人分离」开关）
2. 老师讲话时，左侧实时出现文字，开启说话人分离时会标注「说话人A：…」
3. 下课后点「生成笔记」→ 右侧三套模板自动填充
4. 点「推送到备忘录」→ 本机「备忘录」App 中生成一条新笔记

### 🗄 归档本节课（保存笔记 + 录音）

「推送到备忘录」只是临时存放。要**永久留存一节课**，点 **🗄 归档本节课**：

- 后端会把当前会话的 **原文 + 三套笔记模板 + 本场录音** 写入 `lecture-live/archive/<时间戳>_<标题>/`：
  - `lecture.md` —— 结构化归档笔记（含原文、要点、大纲、问答、关键词）
  - `recording.wav` —— 本节课的完整录音（由本会话累积的 PCM 直接写出，**无需重新上传**）
  - `archive/INDEX.md` —— 全部归档的索引，按时间倒序，点开即看
- 归档结果面板会给出本地目录路径，并提供「下载笔记 Markdown / 下载录音 WAV」链接。
- 标题可在归档前用「标题（可选）」输入框填写，留空则自动用时间命名。
- 纯转写（还没点「生成笔记」）也能归档：只有原文 + 录音，没有 AI 笔记部分会自动留空。

> 说明：录音取自本会话在后端累积的音频缓冲（转写时已在内存中），所以**务必在同一页面会话内、停止录音后、刷新页面前**点归档，才能带上 `recording.wav`；刷新页面后再归档则只含文字与笔记。

## 🚀 本机常驻（开机自启 + 崩溃自愈）

不想每次手动 `./run.sh`？用 `launchd` 把三项注册成 macOS 系统服务：

```bash
chmod +x install-service.sh uninstall-service.sh
bash install-service.sh         # 注册 Ollama + 应用 + 公网隧道
```

会创建三个 `~/Library/LaunchAgents/com.lecturelive.*.plist`，行为：
- **开机自启**：用户登录后自动启动
- **崩溃自愈**：服务意外退出后 10 秒内自动重启
- **依赖顺序**：Ollama 先起 → 应用 → 公网隧道（隧道 plist 内 `sleep 30` 等应用先就绪）
- **防冲突**：如果本机 11434 端口已被手动 `ollama serve` 或 Ollama.app 占用，跳过 Ollama 注册，避免抢端口

```bash
launchctl list | grep lecturelive    # 看三个服务状态
bash uninstall-service.sh            # 全部停掉并取消
```

## 🌐 公网分享（让朋友的电脑直接访问）

局域网外的朋友想用？用 **ngrok v3 dev domain** 拿到一个**永久不变的固定 HTTPS 地址**（绑定 ngrok 账号，重启 ngrok 仍是同一 URL，不会像 quick tunnel 那样随机变域名）：

```bash
brew install ngrok   # 或直接到 https://ngrok.com/download 下载 darwin 二进制
ngrok config add-authtoken <你的 PAT>   # 一次性写入 ~/Library/Application Support/ngrok/ngrok.yml
# 在 https://dashboard.ngrok.com/cloud-edge/domains 看你的 dev domain（免费 1 个）
ngrok http 5000 --url=https://<你的>.ngrok-free.dev
```

> ⚠️ **ngrok 免费 plan 每月 1GB 流量**（endpoint hours 计量），日常课堂转写够用，密集使用一个月会触发限速。

### 🛡 分享模式（保护你本机）

公网分享时**禁止**让陌生人点「推送到备忘录」/「归档」写你的 Mac。开启 `SHARE_MODE=1` 后：

- `/push-notes`、`/archive` 后端直接 **403**
- 前端 `GET /config` 返回 `{"shareMode": true}`，按钮与归档面板自动隐藏
- `run.sh`/`install-service.sh` 的 launchd plist 默认就是 `SHARE_MODE=1`

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `WHISPER_MODEL` | `small` | `tiny`(最快)/`base`/`small`(**推荐**)/`medium`(更准但更慢) |
| `WHISPER_LANG` | `zh` | 识别语言 |
| `WHISPER_PROMPT` | `""` | 留空最稳。**不要写指令式**（Whisper 会直接复读）；如要风格引导，给一段真实转写样例如 `"同学们好,今天我们继续上课。"` |
| `OLLAMA_MODEL` | `qwen2.5:3b` | 3b 更快/更省；想更准用 `qwen2.5:7b` |
| `DENOISE` | `1` | `0` 关闭频谱降噪（怀疑降噪削掉辅音时可关闭对比） |
| `DIARIZE` | `1` | `0` 关闭说话人分离 |
| `DIARIZE_WINDOW` | `200` | 说话人聚类滑动窗口大小（最近 N 个语音段），越小越快但说话人标签可能漂移 |
| `SHARE_MODE` | `1` | `1` 启用分享模式（禁 push-notes/archive，公网必备） |
| `HF_HUB_DISABLE_XET` | `1` | 必设。绕开 HuggingFace 新 Xet 存储在国内 `hf-mirror.com` 镜像下的 401 |
| `PORT` | `5000` | 服务端口 |

前端开关会覆盖对应的环境变量（按请求传参）。

## 已验证 / 已知限制

- ✅ **本机直跑**：`./run.sh` 起 Flask + Whisper + Ollama，`/health` OK
- ✅ **launchd 三件套**：`install-service.sh` 注册 `com.lecturelive.{ollama,app,tunnel}` 三个 LaunchAgent，开机自启、崩溃自愈；Ollama 防冲突保护（端口 11434 已被占则跳过）
- ✅ **公网分享**：ngrok v3 dev domain 固定 HTTPS 地址，公网实测 `/health` 200、`/config` 返 `shareMode:true`、push-notes/archive 均 403、`/summarize` 端到端出笔记
- ✅ **实时性**：前端 3 秒分块 + 后端「最近 200 段」聚类，秒级延迟，不再 2 分钟滞后
- ✅ **Whisper small**：base→small 后中文识别率明显提升；仍觉不够可换 `WHISPER_MODEL=medium`
- ⚠️ **说话人分离准确度**：当前为轻量方案（MFCC 嵌入 + 聚类 + 滑动窗口），老师/同学两三类够用，逊于 pyannote
- ⚠️ **Whisper prompt 复读坑**：`initial_prompt` **不要写指令式**（如"请使用标准书面中文..."），Whisper 会直接复读当转写结果。设成真实转写样例或留空
- ⚠️ **HuggingFace 401**：必须设 `HF_HUB_DISABLE_XET=1`（已写进 `run.sh`），否则 faster-whisper 模型下载会 401
- 准确率（真实人声识别、说话人区分效果）建议你用一段真实课堂录音实测确认。

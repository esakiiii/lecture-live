#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
课堂实时转写笔记 —— 后端服务（含降噪 / 说话人分离 / 推送备忘录）
- /                单页前端
- /health          健康检查（不加载模型）
- /transcribe      接收浏览器上传的 16k 单声道 WAV 分块：
                     → 可选频谱降噪 → 本地 Whisper 逐段识别（带词级时间戳）
                     → 可选说话人分离（VAD 切轮 + MFCC 嵌入 + 聚类）→ 返回带 speaker 的 segments
- /summarize       把整段原文交给本机 Ollama，按"会议总结"风格整理成三套模板
- /push-notes      把内容推送到本机 macOS「备忘录」(Notes.app)，通过 AppleScript
- /archive         把「原文 + 三套笔记模板 + 本会话录音」落盘到 archive/ 目录（Markdown + WAV），并维护 INDEX.md
- /archive-file    下载某个归档里的文件（lecture.md / recording.wav）

设计要点：转写与整理全程在本机，离线、免费、数据不出电脑。
"""
import os
import io
import re
import json
import datetime
import platform
import threading
import subprocess
import tempfile
import urllib.request
import urllib.error

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import numpy as np
import soundfile as sf

app = Flask(__name__, static_folder="static")
CORS(app)

MODEL_SIZE = os.environ.get("WHISPER_MODEL", "small")
LANG = os.environ.get("WHISPER_LANG", "zh")
COMPUTE = os.environ.get("WHISPER_COMPUTE", "int8")
# 中文课堂场景的转写上文(给 Whisper 当风格引导)。
# 警告:不要写"指令式"prompt(如"请使用标准书面中文..."),Whisper 会直接复读当转写内容。
# 想启用风格引导时,把它设成一段真实课堂转写样例,例如:
#   export WHISPER_PROMPT="同学们好,今天我们继续上课。"
# 默认留空,纯转写,稳定不会被复读。
WHISPER_PROMPT = os.environ.get("WHISPER_PROMPT", "").strip()
# 说话人聚类滑动窗口大小：限制每次聚类的音频片段数，避免课时变长后延迟爆炸
DIARIZE_WINDOW = int(os.environ.get("DIARIZE_WINDOW", "200"))
DENOISE = os.environ.get("DENOISE", "1") == "1"          # 人声降噪（轻量频谱门限）
DIARIZE = os.environ.get("DIARIZE", "1") == "1"          # 说话人分离（轻量本地）
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
# 分享模式：公开暴露时禁用「推送到备忘录」「归档」等写入口，避免陌生人写你本机
SHARE_MODE = os.environ.get("SHARE_MODE", "0") == "1"

model = None
model_lock = threading.Lock()

# 会话音频： sid -> {...}
sessions = {}
sessions_lock = threading.Lock()

# 归档目录：每节课的笔记(markdown) + 录音(wav) 落盘到此，便于永久保存
ARCHIVE_DIR = os.environ.get("ARCHIVE_DIR",
                             os.path.join(os.path.dirname(os.path.abspath(__file__)), "archive"))
os.makedirs(ARCHIVE_DIR, exist_ok=True)


def get_model():
    global model
    if model is None:
        with model_lock:
            if model is None:
                from faster_whisper import WhisperModel
                print(f"[init] 加载 faster-whisper 模型 model={MODEL_SIZE} device=cpu compute={COMPUTE} ...", flush=True)
                model = WhisperModel(MODEL_SIZE, device="cpu", compute_type=COMPUTE)
                print("[init] 模型就绪", flush=True)
    return model


# ----------------------- 人声降噪（轻量频谱门限，离线，无模型） -----------------------
def denoise(audio, sr=16000):
    try:
        from scipy.signal import stft, istft
    except Exception:
        return audio
    if len(audio) < sr * 0.5:
        return audio
    f, t, Z = stft(audio, fs=sr, nperseg=512, noverlap=256)
    mag = np.abs(Z)
    frame_energy = mag.mean(axis=0)
    thr = np.percentile(frame_energy, 10)
    noise_frames = mag[:, frame_energy <= thr]
    noise_profile = noise_frames.mean(axis=1, keepdims=True) if noise_frames.shape[1] else mag.mean(axis=1, keepdims=True)
    alpha, min_mask = 2.0, 0.1
    mask = np.clip((mag - alpha * noise_profile) / (mag + 1e-6), min_mask, 1.0)
    _, y = istft(mask * Z, fs=sr, nperseg=512, noverlap=256)
    if len(y) > len(audio):
        y = y[:len(audio)]
    elif len(y) < len(audio):
        y = np.pad(y, (0, len(audio) - len(y)))
    return y.astype(np.float32)


# ----------------------- 说话人分离（VAD 切轮 + MFCC 嵌入 + 聚类） -----------------------
def vad_turns(audio, sr=16000):
    try:
        import webrtcvad
    except Exception:
        return []
    v = webrtcvad.Vad(2)
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    frame = int(sr * 0.02)
    turns, speech, start = [], False, 0.0
    for i in range(0, len(pcm) - frame, frame):
        seg = pcm[i:i + frame].tobytes()
        try:
            is_speech = v.is_speech(seg, sr)
        except Exception:
            is_speech = False
        if is_speech and not speech:
            speech, start = True, i / sr
        elif not is_speech and speech:
            speech = False
            turns.append((start, i / sr))
    if speech:
        turns.append((start, len(pcm) / sr))
    merged = []
    for s, e in turns:
        if merged and s - merged[-1][1] < 0.5:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return merged


def turn_embedding(audio, sr=16000):
    try:
        from python_speech_features import mfcc
    except Exception:
        return None
    if len(audio) < sr * 0.3:
        return None
    try:
        feats = mfcc(audio.astype(np.float32), samplerate=sr, numcep=13, nfilt=26, nfft=512)
        return feats.mean(axis=0)
    except Exception:
        return None


def cluster_speakers(turns):
    if not turns:
        return {}
    embs = np.array([t[2] for t in turns], dtype=float)
    if len(embs) == 1:
        return {0: 0}
    try:
        from sklearn.cluster import AgglomerativeClustering
        n = 2 if len(embs) >= 4 else len(embs)
        labels = AgglomerativeClustering(n_clusters=n, metric="euclidean", linkage="average").fit_predict(embs)
    except Exception:
        labels = np.zeros(len(embs), dtype=int)
    uniq = {}
    for lab in labels:
        uniq.setdefault(lab, len(uniq))
    return {i: uniq[lab] for i, lab in enumerate(labels)}


def speaker_for(mid, turns, cmap):
    best, best_d = None, 1e9
    for idx, (s, e, _emb) in enumerate(turns):
        if s <= mid <= e:
            return cmap.get(idx)
        d = min(abs(mid - s), abs(mid - e))
        if d < best_d:
            best_d, best = d, cmap.get(idx)
    return best


def render_full(segments):
    lines = []
    for seg in segments:
        spk = seg.get("speaker")
        prefix = (spk + "：") if spk else ""
        lines.append(prefix + seg["text"])
    return "\n".join(lines).strip() + ("\n" if lines else "")


# ----------------------- 归档：把转写 + 笔记落盘为结构化 Markdown -----------------------
def build_markdown(title, meta, transcript, notes):
    """把一节课堂的原文 + 三套笔记模板整理成一份结构化 Markdown。"""
    meta = meta or {}
    notes = notes if isinstance(notes, dict) else {}
    dt = meta.get("datetime") or datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    dur = meta.get("duration_sec")
    dur_txt = ""
    if dur:
        try:
            m_, s_ = int(dur // 60), int(dur % 60)
            dur_txt = f"{m_}分{s_}秒"
        except Exception:
            dur_txt = ""
    L = []
    L.append(f"# 课堂笔记 · {title}")
    L.append("")
    L.append(f"> 归档时间：{dt}")
    if dur_txt:
        L.append(f"> 时长：{dur_txt}")
    L.append("> 来源：课堂实时转写笔记（本地 Whisper + 本机 Ollama 整理）")
    L.append("> 配套录音：同目录 `recording.wav`（若已归档）")
    L.append("")

    n = notes.get("notes") or {}
    if isinstance(n, dict) and any(n.get(k) for k in ("要点", "核心概念", "疑问", "待办")):
        L.append("## 一、结构化笔记")
        L.append("")
        for k in ("要点", "核心概念", "疑问", "待办"):
            arr = n.get(k) or []
            L.append(f"### {k}")
            L += [f"- {x}" for x in arr] if arr else ["（无）"]
            L.append("")

    ol = notes.get("outline") or {}
    kw = notes.get("keywords") or []
    if (isinstance(ol, dict) and ol) or kw:
        L.append("## 二、大纲 + 关键词")
        L.append("")
        if isinstance(ol, dict):
            for k, v in ol.items():
                L.append(f"### {k}")
                L += [f"- {x}" for x in v] if isinstance(v, list) and v else ["（无）"]
                L.append("")
        if kw:
            L.append("### 关键词")
            L.append(" ".join(f"`{x}`" for x in kw))
            L.append("")

    qa = notes.get("qa") or []
    if isinstance(qa, list) and qa:
        L.append("## 三、问答对")
        L.append("")
        for p in qa:
            p = p or {}
            L.append(f"**Q：** {p.get('q', '')}")
            L.append("")
            L.append(f"**A：** {p.get('a', '')}")
            L.append("")

    L.append("## 四、转写原文")
    L.append("")
    L.append(transcript if transcript else "（无）")
    L.append("")
    return "\n".join(L)


def rebuild_index():
    """扫描 archive/ 目录，重建 INDEX.md（按时间倒序）。"""
    rows = []
    if os.path.isdir(ARCHIVE_DIR):
        for name in sorted(os.listdir(ARCHIVE_DIR), reverse=True):
            d = os.path.join(ARCHIVE_DIR, name)
            if not os.path.isdir(d):
                continue
            md_path = os.path.join(d, "lecture.md")
            if not os.path.exists(md_path):
                continue
            title = name
            has_audio = os.path.exists(os.path.join(d, "recording.wav"))
            try:
                with open(md_path, encoding="utf-8") as f:
                    first = f.readline().strip()
                if first.startswith("#"):
                    title = first.lstrip("#").strip()
            except Exception:
                pass
            rows.append(f"- [{name}]({name}/lecture.md) — {title}" + ("  🎙️" if has_audio else ""))
    idx = ("# 课堂笔记归档索引\n\n"
           "> 自动生成，按时间倒序。每个子目录含 `lecture.md` 与（若有）`recording.wav`。\n\n")
    idx += "\n".join(rows) if rows else "（暂无归档）"
    try:
        with open(os.path.join(ARCHIVE_DIR, "INDEX.md"), "w", encoding="utf-8") as f:
            f.write(idx)
    except Exception:
        pass


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/health")
def health():
    return jsonify({"ok": True, "model": MODEL_SIZE, "lang": LANG,
                    "denoise": DENOISE, "diarize": DIARIZE, "ollama": OLLAMA_MODEL})


@app.route("/transcribe", methods=["POST"])
def transcribe():
    sid = request.args.get("sid") or request.form.get("sid") or "default"
    denoise_on = request.args.get("denoise", "1" if DENOISE else "0") == "1"
    diarize_on = request.args.get("diarize", "1" if DIARIZE else "0") == "1"
    wav_bytes = request.get_data()
    if not wav_bytes:
        return jsonify({"error": "音频为空"}), 400
    try:
        audio, sr = sf.read(io.BytesIO(wav_bytes), dtype="float32", always_2d=False)
    except Exception as e:
        return jsonify({"error": f"音频解码失败: {e}"}), 400
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != 16000:
        try:
            import librosa
            audio = librosa.resample(audio, orig_sr=sr, target_sr=16000)
        except Exception:
            if len(audio) > 1:
                audio = np.interp(np.arange(0, len(audio) * 16000, sr),
                                  np.arange(0, len(audio)), audio).astype(np.float32)
    audio = audio.astype(np.float32)

    with sessions_lock:
        s = sessions.setdefault(sid, {"pcm": np.zeros(0, dtype=np.float32), "segments": [], "turns": []})
        dur_before = len(s["pcm"]) / 16000
        if denoise_on:
            audio = denoise(audio, 16000)
        s["pcm"] = np.concatenate([s["pcm"], audio])
        cap = 16000 * 3600
        if len(s["pcm"]) > cap:
            s["pcm"] = s["pcm"][-cap // 2:]

    m = get_model()
    try:
        # 串行化模型推理，避免多线程并发访问 CTranslate2 模型导致错乱
        with model_lock:
            _kw = dict(language=LANG, beam_size=5, word_timestamps=True, vad_filter=True)
            if WHISPER_PROMPT:  # 只有非空才传 initial_prompt，避免 Whisper 复读空 prompt
                _kw["initial_prompt"] = WHISPER_PROMPT
            segments, _ = m.transcribe(audio, **_kw)
    except Exception as e:
        return jsonify({"error": f"转写失败: {e}"}), 500

    new_segs = []
    for seg in segments:
        txt = (seg.text or "").strip()
        if txt:
            new_segs.append({"start": round(dur_before + seg.start, 2),
                             "end": round(dur_before + seg.end, 2),
                             "text": txt})

    if diarize_on:
        try:
            chunk_turns = []
            for (ts, te) in vad_turns(audio, 16000):
                a = audio[int(ts * 16000):int(te * 16000)]
                emb = turn_embedding(a, 16000)
                if emb is not None:
                    chunk_turns.append((dur_before + ts, dur_before + te, emb))
            if chunk_turns:
                s["turns"].extend(chunk_turns)
                # 仅在最近窗口内聚类，避免随课时增长出现 O(n^2) 延迟爆炸
                window = s["turns"][-DIARIZE_WINDOW:]
                cmap = cluster_speakers(window)
                # 只给本批新片段标注说话人，历史片段保持原标注（不再全量重算）
                for seg in new_segs:
                    spk = speaker_for((seg["start"] + seg["end"]) / 2, window, cmap)
                    seg["speaker"] = ("说话人" + chr(65 + spk)) if spk is not None else None
        except Exception as e:
            app.logger.warning("diarize skipped: %s", e)

    with sessions_lock:
        s["segments"].extend(new_segs)
        full = render_full(s["segments"])
        s["text"] = full

    return jsonify({"segments": s["segments"], "full": full})


@app.route("/summarize", methods=["POST"])
def summarize():
    data = request.get_json(silent=True) or {}
    transcript = (data.get("transcript") or "").strip()
    if not transcript:
        return jsonify({"error": "transcript 为空"}), 400
    return jsonify(summarize_with_ollama(transcript))


SUMMARY_PROMPT = """你是一名严谨的课堂笔记整理助手。下面是一段大学课程的实时转写原文（可能包含口语词、重复、断句错误、少量识别错误，部分句子以"说话人A：""说话人B："开头表示不同说话人）。
请像"会议总结"一样，自行判断并提取老师讲课中真正重要的内容，剔除废话与口头禅，整理为结构化笔记。

要求：
1. 只基于原文内容推理，不要编造原文中没有的知识点。
2. 若原文带有"说话人X："前缀，请在整理时保留说话人归属（例如要点可标注是谁提出的）。
3. 输出严格的 JSON，字段如下（全部使用中文）：
{{
  "notes": {{
    "要点": ["本节课最重要的 5-10 条结论性要点"],
    "核心概念": ["关键术语 / 定义 / 公式 / 人名"],
    "疑问": ["原文中存疑、待查证、或老师留下的思考题"],
    "待办": ["作业、预习、复习等任务"]
  }},
  "outline": {{
    "一级标题": ["二级要点", "二级要点"]
  }},
  "keywords": ["5-15 个高亮关键词"],
  "qa": [
    {{"q": "老师的提问 / 重点设问", "a": "对应解答"}}
  ]
}}
4. 若某字段在原文中确实没有对应内容，给空数组 / 空对象，不要硬凑。
5. 一级标题应反映本节课的知识结构，不要只用"要点1/要点2"。

转写原文如下：
---
{transcript}
---
只输出 JSON，不要任何解释性文字。"""


def summarize_with_ollama(transcript):
    prompt = SUMMARY_PROMPT.format(transcript=transcript)
    payload = {"model": OLLAMA_MODEL, "prompt": prompt, "format": "json", "stream": False}
    req = urllib.request.Request(OLLAMA_URL, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            resp = json.loads(r.read().decode("utf-8"))
        raw = resp.get("response", "")
    except urllib.error.URLError as e:
        return {"error": f"无法连接 Ollama（{OLLAMA_URL}）：{e}。\n请先安装并运行 Ollama，再执行 `ollama pull {OLLAMA_MODEL}`。"}
    except Exception as e:
        return {"error": f"Ollama 调用异常：{e}"}
    try:
        data = json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0)) if (m and m.group(0)) else {"raw": raw}
    return data


@app.route("/config")
def config():
    return jsonify({"shareMode": SHARE_MODE, "model": MODEL_SIZE, "lang": LANG,
                    "ollama": OLLAMA_MODEL, "diarize": DIARIZE, "denoise": DENOISE})


@app.route("/push-notes", methods=["POST"])
def push_notes():
    if SHARE_MODE:
        return jsonify({"error": "分享模式下已禁用「推送到备忘录」"}), 403
    if platform.system() != "Darwin":
        return jsonify({"error": "推送备忘录仅支持 macOS 本机的「备忘录」(Notes.app)。"}), 400
    data = request.get_json(silent=True) or {}
    content = (data.get("content") or "").strip()
    title = (data.get("title") or "课堂笔记").strip()
    if not content:
        return jsonify({"error": "内容为空"}), 400
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
    script = ('tell application "Notes"\n'
              f'  make new note with properties {{name:"{esc(title)}", body:"{esc(content)}"}}\n'
              'end tell')
    fd, path = tempfile.mkstemp(suffix=".applescript")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(script)
        r = subprocess.run(["osascript", path], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return jsonify({"error": "备忘录推送失败：" + (r.stderr or r.stdout).strip()})
        return jsonify({"ok": True, "msg": "已推送到本机备忘录"})
    except Exception as e:
        return jsonify({"error": "备忘录推送异常：" + str(e)})
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


@app.route("/archive", methods=["POST"])
def archive():
    if SHARE_MODE:
        return jsonify({"error": "分享模式下已禁用「归档本节课」"}), 403
    data = request.get_json(silent=True) or {}
    sid = data.get("sid") or "default"
    transcript = (data.get("transcript") or "").strip()
    notes = data.get("notes") or {}
    if not isinstance(notes, dict):
        notes = {}
    title = (data.get("title") or "课堂笔记").strip() or "课堂笔记"
    meta = data.get("meta") or {}
    if not transcript and not notes:
        return jsonify({"error": "没有可归档的内容（原文和笔记均为空）"}), 400

    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r'[\\/:*?"<>|\r\n]+', "_", title)[:40].strip() or "lecture"
    folder = f"{ts}_{safe}"
    out_dir = os.path.join(ARCHIVE_DIR, folder)
    os.makedirs(out_dir, exist_ok=True)

    md = build_markdown(title, meta, transcript, notes)
    md_path = os.path.join(out_dir, "lecture.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)

    has_audio = False
    with sessions_lock:
        s = sessions.get(sid)
        pcm = s.get("pcm") if s else None
    if pcm is not None and len(pcm) > 0:
        try:
            wav_path = os.path.join(out_dir, "recording.wav")
            sf.write(wav_path, np.asarray(pcm, dtype=np.float32), 16000)
            has_audio = True
        except Exception as e:
            app.logger.warning("archive audio save failed: %s", e)

    rebuild_index()
    return jsonify({"ok": True, "folder": folder, "dir": out_dir,
                    "md": md, "has_audio": has_audio})


@app.route("/archive-file/<folder>/<filename>")
def archive_file(folder, filename):
    folder = os.path.basename(folder)
    filename = os.path.basename(filename)
    base = os.path.join(ARCHIVE_DIR, folder)
    if not os.path.isdir(base):
        return jsonify({"error": "归档不存在"}), 404
    return send_from_directory(base, filename)


if __name__ == "__main__":
    rebuild_index()
    port = int(os.environ.get("PORT", "5000"))
    print(f"[server] http://localhost:{port}  (model={MODEL_SIZE}, lang={LANG}, "
          f"denoise={DENOISE}, diarize={DIARIZE}, ollama={OLLAMA_MODEL})", flush=True)
    app.run(host="0.0.0.0", port=port, threaded=True)

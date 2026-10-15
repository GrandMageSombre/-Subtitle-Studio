"""faster-whisper 语音识别封装。

要点：
- 国内网络无法直连 huggingface.co 时，在导入 faster_whisper 之前自动把
  ``HF_ENDPOINT`` 切换到 hf-mirror 镜像（用户已显式设置时尊重用户设置）；
- 模型按 (尺寸, 计算精度) 缓存，重复识别不重复加载；
- 全局转录锁保证同一时刻只有一个识别任务（CTranslate2 非并发安全）；
- 基于词级时间戳做「按标点断句」，把 Whisper 的长segment切分成适合字幕的短条。
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
import time
import urllib.request
from typing import Callable

# ---------------------------------------------------------------------------
# 镜像探测（必须在 import faster_whisper 之前设置 HF_ENDPOINT，
# 因为 huggingface_hub 在 import 时读取该环境变量）
# ---------------------------------------------------------------------------


#: 探测缓存：文件 + 有效期（避免每次启动都阻塞 4 秒网络探测）
_PROBE_CACHE_FILE = os.path.join(tempfile.gettempdir(), "subtitle_studio_hf_probe")
_PROBE_TTL_SECONDS = 24 * 3600


def _read_probe_cache() -> str | None:
    """读取探测缓存："" 表示直连可用，非空为应使用的镜像地址；无有效缓存返回 None。"""
    try:
        with open(_PROBE_CACHE_FILE, encoding="utf-8") as f:
            ts, endpoint = f.read().strip().split("\n", 1)
        if time.time() - float(ts) < _PROBE_TTL_SECONDS:
            return endpoint
    except Exception:  # noqa: BLE001  缓存缺失或损坏则忽略
        pass
    return None


def _write_probe_cache(endpoint: str) -> None:
    try:
        with open(_PROBE_CACHE_FILE, "w", encoding="utf-8") as f:
            f.write(f"{time.time():.0f}\n{endpoint}")
    except OSError:
        pass


def _ensure_hf_endpoint() -> None:
    """国内网络无法直连 huggingface.co 时切换到 hf-mirror 镜像。

    镜像站不支持 Xet 存储后端（cas-server.xethub.hf.co 会 401），
    因此回退镜像时同步禁用 Xet。用户已显式设置 HF_ENDPOINT 时尊重用户设置。
    探测结果缓存 24 小时（``%TEMP%/subtitle_studio_hf_probe``），
    避免每次启动都阻塞 4 秒——打包版冷启动尤其明显。
    """
    if os.environ.get("HF_ENDPOINT"):
        return
    endpoint = _read_probe_cache()
    if endpoint is None:
        try:
            urllib.request.urlopen("https://huggingface.co", timeout=4)
            endpoint = ""
        except Exception:  # noqa: BLE001  直连失败即判定需要镜像
            endpoint = "https://hf-mirror.com"
        _write_probe_cache(endpoint)
    if endpoint:
        os.environ["HF_ENDPOINT"] = endpoint
        # 镜像不支持 Xet 存储后端（cas-server.xethub.hf.co 会 401）
        os.environ["HF_HUB_DISABLE_XET"] = "1"


_ensure_hf_endpoint()

from faster_whisper import WhisperModel  # noqa: E402

from .srt import round3  # noqa: E402  half-up 舍入，与前端保持一致

logger = logging.getLogger(__name__)


def _patch_av_open_for_pyav19() -> None:
    """PyAV 19 起 av.open 不再接受 metadata_errors 等参数（faster-whisper 1.2.x 仍会传入）。

    探测支持性；不支持时给 av.open 包一层剥掉这些参数的兼容壳，
    使用户环境中意外存在 PyAV 19 时识别仍可工作。
    """
    try:
        import av  # noqa: PLC0415
        import io  # noqa: PLC0415
    except ImportError:
        return

    def _supported() -> bool:
        try:
            av.open(io.BytesIO(b""), mode="r", metadata_errors="ignore")
        except TypeError as exc:
            # 仅当报错确实源于该参数时才判定为不支持
            return "metadata_errors" not in str(exc)
        except Exception:  # noqa: BLE001
            return True  # 其它错误说明参数已被接受
        return True

    if _supported():
        return

    _original_open = av.open

    def _compat_open(*args, **kwargs):
        for key in ("metadata_errors", "metadata_encoding", "stream_options"):
            kwargs.pop(key, None)
        return _original_open(*args, **kwargs)

    av.open = _compat_open


_patch_av_open_for_pyav19()

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

#: 可用模型（id -> 说明）
MODELS = {
    "tiny": "tiny · 75MB · 速度最快，精度一般",
    "base": "base · 145MB · 较快，精度尚可",
    "small": "small · 460MB · 速度与精度均衡（推荐）",
    "medium": "medium · 1.5GB · 精度较高，速度较慢",
    "large-v3": "large-v3 · 3.1GB · 精度最高，速度最慢",
}

#: 界面语言选择（code -> 显示名），空串表示自动检测
LANGUAGES = {
    "": "自动检测",
    "zh": "中文",
    "yue": "粤语",
    "en": "英语",
    "ja": "日语",
    "ko": "韩语",
    "fr": "法语",
    "de": "德语",
    "es": "西班牙语",
    "ru": "俄语",
    "it": "意大利语",
    "pt": "葡萄牙语",
    "ar": "阿拉伯语",
    "hi": "印地语",
    "th": "泰语",
    "vi": "越南语",
    "id": "印尼语",
    "tr": "土耳其语",
}

TASKS = {
    "transcribe": "识别为原语言字幕",
    "translate": "翻译成英语",
}

#: 中文字形选项（识别后处理，OpenCC 转换；auto = 不转换，保持识别原样）
SCRIPTS = {
    "auto": "自动（保持识别原样）",
    "simplified": "简体中文",
    "traditional": "繁体中文",
}

#: OpenCC 转换器实例缓存（词典加载耗时，按需构造并复用）
_SCRIPT_CONVERTERS: dict = {}


def convert_script(text: str, script: str) -> str:
    """按选项转换中文字形（simplified/traditional）；auto 或 OpenCC
    不可用（未安装/加载失败）时原样返回。

    采用词汇级配置：简体走 ``tw2sp``（繁→简且转换台湾用词，如 軟體→软件）、
    繁体走 ``s2twp``（简→繁台湾正体，如 软件→軟體），比纯字级 t2s/s2t 更自然。
    """
    if script not in ("simplified", "traditional"):
        return text
    try:
        if script not in _SCRIPT_CONVERTERS:
            try:
                from opencc import OpenCC

                _SCRIPT_CONVERTERS[script] = OpenCC("tw2sp" if script == "simplified" else "s2twp")
            except Exception:  # noqa: BLE001
                # 负缓存：构造失败后不再每条 cue 重试刷屏，降级返回原文
                logger.warning("OpenCC 加载失败，字形转换降级为原文（script=%s）", script,
                               exc_info=True)
                _SCRIPT_CONVERTERS[script] = None
        cc = _SCRIPT_CONVERTERS[script]
        return cc.convert(text) if cc else text
    except Exception:  # noqa: BLE001  转换失败不应阻断识别主流程
        logger.warning("字形转换失败（script=%s），返回原文", script, exc_info=True)
        return text

#: 句末标点（中英文，含英文句点；与前端 splitTextAtPunct 正则保持一致，
#: 改动需同步 static/app.js 并由 tests/test_consistency.py 校验）
SENT_PUNCT = "。！？；!?;…."

#: 非句点类句末标点（出现即断句）
_PUNCT_NON_DOT = "。！？；!?;…"

#: 英文句点的“伪句尾”模式：小数（3.5）、常见缩写（Mr./Dr./U.S.A.）
_NOT_SENT_END_RE = re.compile(r"^(?:\d+\.\d+|[A-Za-z]{1,3}\.|(?:[A-Za-z]\.){2,})$")


def _is_sentence_end(token: str) -> bool:
    """判断词元是否表示句子结束（排除小数与缩写里的句点）。"""
    if not token:
        return False
    if any(p in token for p in _PUNCT_NON_DOT):
        return True
    if token.endswith(".") and not _NOT_SENT_END_RE.match(token):
        return True
    return False

#: 短于此时长且少于此字数的句子碎片，并入相邻条目（避免出现 "好。" 这类单字条）
MIN_CUE_DURATION = 0.9
MIN_CUE_CHARS = 6
#: 单句超过该时长时按词边界硬切（防止超长条目）
HARD_DURATION = 14.0

# ---------------------------------------------------------------------------
# 模型缓存
# ---------------------------------------------------------------------------

_MODELS: dict[tuple[str, str], WhisperModel] = {}
_MODEL_LOCK = threading.Lock()
_TRANSCRIBE_LOCK = threading.Lock()

ProgressCB = Callable[[float], None] | Callable[[float, str], None] | None


def get_model(model_size: str, compute_type: str = "int8") -> WhisperModel:
    """获取（必要时加载）Whisper 模型实例。"""
    key = (model_size, compute_type)
    with _MODEL_LOCK:
        model = _MODELS.get(key)
        if model is None:
            model = WhisperModel(model_size, device="cpu", compute_type=compute_type)
            _MODELS[key] = model
        return model


# ---------------------------------------------------------------------------
# 断句
# ---------------------------------------------------------------------------


def _mk_cue(words) -> dict:
    text = re.sub(r"\s+", " ", "".join(w.word or "" for w in words)).strip()
    return {
        "start": round3(words[0].start),
        "end": round3(words[-1].end),
        "text": text,
    }


def refine_segments(segments) -> list[dict]:
    """把 Whisper 原始 segment 列表细化成适合字幕的短条。

    策略（三级）：
    1. 按句末标点（。！？；!?;…）切分为句子；
    2. 时长/字数过短的碎片并入相邻句子，避免单字条；
    3. 单句超过 :data:`HARD_DURATION` 秒时按词边界硬切。

    无词级时间戳的 segment 直接采用其自身边界。
    """
    cues: list[dict] = []
    for seg in segments:
        words = list(getattr(seg, "words", None) or [])
        if not words:
            text = re.sub(r"\s+", " ", seg.text or "").strip()
            if text:
                cues.append({
                    "start": round3(seg.start),
                    "end": round3(seg.end),
                    "text": text,
                })
            continue

        # 1. 按句末标点切句
        sentences: list[list] = []
        current: list = []
        for word in words:
            current.append(word)
            if _is_sentence_end(word.word or ""):
                sentences.append(current)
                current = []
        if current:
            sentences.append(current)

        # 2. 合并过短碎片
        merged: list[list] = []
        for sent in sentences:
            duration = sent[-1].end - sent[0].start
            chars = len("".join(w.word or "" for w in sent))
            if merged and duration < MIN_CUE_DURATION and chars < MIN_CUE_CHARS:
                merged[-1].extend(sent)
            else:
                merged.append(list(sent))

        # 3. 超长硬切
        for group in merged:
            cues.extend(_hard_cut(group))

    cues.sort(key=lambda c: c["start"])
    return cues


def _hard_cut(words: list) -> list[dict]:
    """把超长词列表按词边界切成不超过 HARD_DURATION 的条目。"""
    if words[-1].end - words[0].start <= HARD_DURATION:
        return [_mk_cue(words)]
    out: list[dict] = []
    current = [words[0]]
    for word in words[1:]:
        if word.end - current[0].start >= HARD_DURATION:
            out.append(_mk_cue(current))
            current = [word]
        else:
            current.append(word)
    if current:
        out.append(_mk_cue(current))
    return out


# ---------------------------------------------------------------------------
# 识别入口
# ---------------------------------------------------------------------------


def transcribe(
    audio_path: str,
    *,
    model_size: str = "small",
    language: str | None = None,
    task: str = "transcribe",
    script: str = "auto",
    compute_type: str = "int8",
    progress_cb: ProgressCB = None,
) -> list[dict]:
    """识别音频/视频文件，返回 cue 列表。

    :param audio_path: 音视频文件路径（PyAV 可解码即可）
    :param model_size: 模型尺寸，见 :data:`MODELS`
    :param language: 语言代码，``None`` 表示自动检测
    :param task: ``transcribe``（原语言）或 ``translate``（翻译成英语）
    :param script: 中文字形，见 :data:`SCRIPTS`；``auto`` 表示不转换
    :param progress_cb: 进度回调，接收 0~1 的浮点数，可选第二个参数为状态文本
    """
    if model_size not in MODELS:
        raise ValueError(f"未知模型: {model_size}")

    def report(value: float, message: str | None = None) -> None:
        if progress_cb is None:
            return
        try:
            if message is None:
                progress_cb(value)
            else:
                progress_cb(value, message)
        except Exception:
            pass

    # 模型加载/下载在转录锁之外进行：避免首次下载（可达数分钟）时
    # 其他任务被锁挡住且进度假死（加载锁在 get_model 内部）
    report(0.01, "正在加载模型…")
    model = get_model(model_size, compute_type)

    # 推理串行化（CTranslate2 非并发安全）；排队时给出明确状态
    if not _TRANSCRIBE_LOCK.acquire(blocking=False):
        report(0.0, "排队中，等待前序识别完成…")
        _TRANSCRIBE_LOCK.acquire()
    try:
        report(0.05, "正在解码音频…")
        segments_gen, info = model.transcribe(
            audio_path,
            language=language or None,
            task=task if task in TASKS else "transcribe",
            vad_filter=True,
            beam_size=5,
            word_timestamps=True,
        )

        duration = float(info.duration) if info.duration else 0.0
        raw_segments = []
        for seg in segments_gen:
            raw_segments.append(seg)
            if duration > 0:
                report(min(0.95, 0.05 + 0.90 * float(seg.end) / duration),
                       f"识别中 {fmt_hms(seg.end)} / {fmt_hms(duration)}")

        report(0.97, "正在按标点优化断句…")
        cues = refine_segments(raw_segments)
    finally:
        _TRANSCRIBE_LOCK.release()
    # 字形转换不依赖 CTranslate2，移出转录锁执行，
    # 避免词典加载与逐条转换的耗时延长锁占用、阻塞后续任务
    if script != "auto":
        report(0.98, "正在转换中文字形…")
        for cue in cues:
            cue["text"] = convert_script(cue["text"], script)
    report(1.0, f"识别完成，共 {len(cues)} 条")
    return cues

def fmt_hms(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

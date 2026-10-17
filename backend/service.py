"""识别任务与字幕文件的共用业务逻辑。

浏览器模式（``backend.main`` 的 HTTP 路由）与桌面界面（``desktop``）都调用
这里，避免「同一件事两套实现」带来的行为漂移：媒体落位、字幕导入解析、
导出序列化与文件命名集中在本模块。

时间单位统一为秒（float，3 位小数），cue 结构 ``{"start", "end", "text"}``
见 :mod:`backend.srt`。
"""
from __future__ import annotations

import logging
import os
import re
from typing import BinaryIO, Iterable

from . import srt

logger = logging.getLogger("subtitle-studio.service")

# ---------------------------------------------------------------------------
# 体积上限
# ---------------------------------------------------------------------------

#: 识别媒体体积上限（默认 10GB，可用环境变量 MAX_UPLOAD_MB 调整）
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "10240"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
#: 字幕文件导入上限（SRT/VTT/ASS 纯文本，20MB 足够）
IMPORT_MAX_BYTES = 20 * 1024 * 1024
#: 分块读写粒度
CHUNK_BYTES = 1 << 20

#: 导出格式 -> (MIME, 扩展名, 序列化函数)；新增格式只需在此登记一行
EXPORT_FORMATS: dict[str, tuple[str, str, object]] = {
    "srt": ("application/x-subrip; charset=utf-8", "srt", srt.to_srt),
    "vtt": ("text/vtt; charset=utf-8", "vtt", srt.to_vtt),
    "ass": ("text/plain; charset=utf-8", "ass", srt.to_ass),
    "txt": ("text/plain; charset=utf-8", "txt", srt.to_txt),
    "json": ("application/json; charset=utf-8", "json", srt.to_json),
}

#: 导出菜单显示名（与 :data:`EXPORT_FORMATS` 的键一致）
EXPORT_LABELS = {
    "srt": "SRT 字幕（通用）",
    "vtt": "WebVTT 字幕",
    "ass": "ASS 字幕（必剪/剪映）",
    "txt": "TXT 纯文本",
    "json": "JSON 数据",
}

#: 导入可识别的字幕扩展名（文件对话框、拖放分流共用）
SUBTITLE_EXTS = (".srt", ".vtt", ".ass", ".ssa")

#: 可识别的媒体扩展名（识别文件对话框用；HTTP 响应的 MIME 映射见
#: ``backend.main.MEDIA_TYPES``，两者用途不同故分开维护）
MEDIA_EXTS = ("mp4", "m4v", "mov", "mkv", "webm", "avi", "flv", "ts",
              "mp3", "wav", "m4a", "aac", "flac", "ogg", "wma")


class UploadTooLarge(Exception):
    """媒体/字幕文件超出体积上限。"""


class SubtitleParseError(Exception):
    """字幕文件解析不出任何条目。"""


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def safe_ext(filename: str) -> str:
    """取安全的媒体扩展名（非法时退回 ``.mp4``）。"""
    ext = os.path.splitext(filename or "")[1].lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", ext):
        return ".mp4"
    return ext


def is_subtitle_file(filename: str) -> bool:
    """是否是可导入的字幕文件（决定拖放走导入还是识别）。"""
    return os.path.splitext(filename or "")[1].lower() in SUBTITLE_EXTS


def decode_subtitle_bytes(raw: bytes) -> str:
    """解码字幕文本：优先 UTF-8（含 BOM），失败退回 GBK（不抛错）。"""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("gbk", errors="replace")


def import_cues_from_bytes(raw: bytes) -> list[dict]:
    """解析字幕字节流为 cue 列表（SRT / VTT / ASS 自动判别）。

    :raises SubtitleParseError: 未解析到任何条目
    """
    if len(raw) > IMPORT_MAX_BYTES:
        raise UploadTooLarge(
            f"字幕文件过大（上限 {IMPORT_MAX_BYTES // 1024 // 1024} MB）")
    cues = srt.normalize_cues(srt.parse_auto(decode_subtitle_bytes(raw)))
    if not cues:
        raise SubtitleParseError("未解析到任何字幕条目，请检查文件格式（SRT / VTT / ASS）")
    return cues


def import_cues_from_file(path: str) -> list[dict]:
    """从本地字幕文件读取并解析（桌面模式用）。"""
    size = os.path.getsize(path)
    if size > IMPORT_MAX_BYTES:
        raise UploadTooLarge(
            f"字幕文件过大（上限 {IMPORT_MAX_BYTES // 1024 // 1024} MB）")
    with open(path, "rb") as f:
        return import_cues_from_bytes(f.read())


def sorted_cues(cues: Iterable[dict]) -> list[dict]:
    """导出前统一按 (start, end) 排序。"""
    return sorted(cues, key=lambda c: (c["start"], c["end"]))


def export_text(cues: Iterable[dict], fmt: str) -> str:
    """序列化 cue 列表为指定格式文本。

    ASS 额外加 BOM：必剪/剪映在 Windows 上可能按 ANSI 解码无 BOM 的 UTF-8。
    """
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"不支持的导出格式: {fmt}")
    _, _, serializer = EXPORT_FORMATS[fmt]
    content = serializer(sorted_cues(cues))  # type: ignore[operator]
    return "\ufeff" + content if fmt == "ass" else content


def export_bytes(cues: Iterable[dict], fmt: str) -> bytes:
    """序列化并编码为 UTF-8 字节。"""
    return export_text(cues, fmt).encode("utf-8")


def export_filename(source_name: str, fmt: str) -> str:
    """按任务原文件名生成导出文件名（``视频.mp4`` -> ``视频.srt``）。"""
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"不支持的导出格式: {fmt}")
    ext = EXPORT_FORMATS[fmt][1]
    base = os.path.splitext(os.path.basename(source_name or ""))[0] or "subtitles"
    return f"{base}.{ext}"


# ---------------------------------------------------------------------------
# 媒体落位
# ---------------------------------------------------------------------------

class MediaStager:
    """把媒体写入任务目录，并强制体积上限。

    浏览器模式（HTTP 分块上传）与桌面模式（本地文件复制）共用，保证
    上限校验、目标命名（``media.<ext>``）与中途失败清理行为一致。

    用法::

        with MediaStager(job_dir, "video.mp4") as stager:
            stager.write(chunk)
            ...
        path = stager.path
    """

    def __init__(self, job_dir: str, filename: str,
                 max_bytes: int | None = None) -> None:
        self.job_dir = job_dir
        self.filename = os.path.basename(filename or "media.mp4")
        # 运行时读取模块级上限：便于通过环境变量/测试调整（默认参数会固化取值）
        self.max_bytes = MAX_UPLOAD_BYTES if max_bytes is None else int(max_bytes)
        self.path = os.path.join(job_dir, "media" + safe_ext(self.filename))
        self.written = 0
        self._fp: BinaryIO | None = None

    def __enter__(self) -> "MediaStager":
        os.makedirs(self.job_dir, exist_ok=True)
        self._fp = open(self.path, "wb")
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if self._fp is not None:
            self._fp.close()
            self._fp = None
        if exc_type is not None:
            self.abort()
        return False

    def write(self, chunk: bytes) -> None:
        """写入一块数据；超限抛 :class:`UploadTooLarge`。"""
        if self._fp is None:
            raise RuntimeError("MediaStager 必须在 with 块内使用")
        if not chunk:
            return
        self.written += len(chunk)
        if self.written > self.max_bytes:
            raise UploadTooLarge(
                f"文件超过上限 {self.max_bytes // 1024 // 1024} MB")
        self._fp.write(chunk)

    def copy_from(self, source_path: str) -> None:
        """复制本地文件内容（分块写入，同样受上限保护）。"""
        size = os.path.getsize(source_path)
        if size > self.max_bytes:
            raise UploadTooLarge(
                f"文件超过上限 {self.max_bytes // 1024 // 1024} MB")
        with open(source_path, "rb") as src:
            for chunk in iter(lambda: src.read(CHUNK_BYTES), b""):
                self.write(chunk)

    def abort(self) -> None:
        """删除半截文件（失败时不留下残缺媒体）。"""
        try:
            if os.path.exists(self.path):
                os.remove(self.path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 任务编排
# ---------------------------------------------------------------------------

def create_import_job(manager, filename: str, cues: list[dict]):
    """把解析好的字幕建成任务并落库（HTTP 与桌面共用）。

    导入即置 ``done``：避免永远停留 ``queued`` 导致每次重启都被误判为中断任务。
    """
    job = manager.create("import", filename)
    manager.set_segments(job.id, cues)
    manager.set_status(job.id, "done", "导入完成")
    return job


def run_transcription(manager, job_id: str, progress_cb=None) -> bool:
    """执行识别任务并落库，返回是否成功。

    HTTP 路由与桌面界面共用这一份编排，避免「识别流程」两处实现漂移：
    状态置 running → asr.transcribe（进度同时写入任务内存态）→ 落库 → 置 done；
    任何异常都会置 ``error`` 并写入原因，返回 ``False``。

    :param progress_cb: 可选的额外进度回调（桌面端用它发 Qt 信号）
    """
    from . import asr  # 延迟导入：仅导入/导出功能无需加载识别栈

    job = manager.get(job_id)
    if job is None or not job.media_path:
        return False

    def report(value: float, message: str | None = None) -> None:
        manager.set_progress(job_id, value, message)
        if progress_cb is not None:
            progress_cb(value, message)

    try:
        manager.set_status(job_id, "running", "正在加载模型…")
        settings = job.settings or {}
        segments = asr.transcribe(
            job.media_path,
            model_size=settings.get("model", "small"),
            language=settings.get("language") or None,
            task=settings.get("task", "transcribe"),
            script=settings.get("script", "auto"),
            progress_cb=report,
        )
        manager.set_segments(job_id, segments)
        manager.set_status(job_id, "done", f"识别完成，共 {len(segments)} 条")
        return True
    except Exception as exc:  # noqa: BLE001  失败原因要落到任务上供界面展示
        logger.exception("识别任务失败：%s", job_id)
        manager.set_status(job_id, "error", f"识别失败：{exc}")
        return False

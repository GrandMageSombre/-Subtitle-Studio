"""FastAPI 主应用：识别任务、字幕导入导出、媒体流式播放。"""
from __future__ import annotations

import logging
import math
import os
import re
import sys
import threading
import urllib.parse

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from . import asr, srt
from .jobs import JobManager

logger = logging.getLogger("subtitle-studio")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_static_dir() -> str:
    """定位 static 目录：源码运行为项目内目录；PyInstaller 打包后在
    ``sys._MEIPASS``（onedir 下即 exe 所在目录）内。"""
    if getattr(sys, "frozen", False):
        return os.path.join(
            getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable),
            "static",
        )
    return os.path.join(BASE_DIR, "static")


STATIC_DIR = _resolve_static_dir()

#: 常见媒体扩展名 -> MIME（浏览器 <video>/<audio> 可播放的子集优先）
MEDIA_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".avi": "video/x-msvideo",
    ".flv": "video/x-flv",
    ".ts": "video/mp2t",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".wma": "audio/x-ms-wma",
}

#: 导出格式 -> (MIME, 扩展名)
EXPORT_FORMATS = {
    "srt": ("application/x-subrip; charset=utf-8", "srt"),
    "vtt": ("text/vtt; charset=utf-8", "vtt"),
    "txt": ("text/plain; charset=utf-8", "txt"),
    "json": ("application/json; charset=utf-8", "json"),
}

#: 识别上传体积上限（默认 10GB，可用环境变量 MAX_UPLOAD_MB 调整）
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "10240"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
#: 字幕文件导入上限（SRT/VTT 纯文本，20MB 足够）
IMPORT_MAX_BYTES = 20 * 1024 * 1024

app = FastAPI(title="字幕工坊 Subtitle Studio", docs_url="/api/docs")
manager = JobManager()


class _UploadTooLarge(Exception):
    """上传超出体积上限。"""


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

class SegmentsBody(BaseModel):
    """前端保存编辑结果的请求体。"""

    segments: list

    @field_validator("segments")
    @classmethod
    def _validate(cls, value):
        cleaned = []
        for item in value:
            try:
                start = float(item["start"])
                end = float(item["end"])
                text = str(item.get("text", ""))
            except (KeyError, TypeError, ValueError):
                raise ValueError("字幕条目格式不正确，需要 start/end/text 字段")
            if not (math.isfinite(start) and math.isfinite(end)):
                raise ValueError("字幕时间必须是有限数值")
            start = max(0.0, start)
            end = max(end, start + 0.001)
            cleaned.append({"start": round(start, 3), "end": round(end, 3), "text": text})
        return cleaned


# ---------------------------------------------------------------------------
# 转录线程
# ---------------------------------------------------------------------------

def _run_transcription(job_id: str) -> None:
    job = manager.get(job_id)
    if job is None or not job.media_path:
        return
    try:
        manager.set_status(job_id, "running", "正在加载模型…")
        settings = job.settings or {}
        segments = asr.transcribe(
            job.media_path,
            model_size=settings.get("model", "small"),
            language=settings.get("language") or None,
            task=settings.get("task", "transcribe"),
            progress_cb=lambda p, m=None: manager.set_progress(job_id, p, m),
        )
        manager.set_segments(job_id, segments)
        manager.set_status(job_id, "done", f"识别完成，共 {len(segments)} 条")
    except Exception as exc:  # noqa: BLE001
        logger.exception("识别任务失败: %s", job_id)
        manager.set_status(job_id, "error", f"识别失败：{exc}")


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------

def _safe_ext(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", ext):
        return ".mp4"
    return ext


def _iter_file(path: str, start: int, length: int, chunk: int = 1 << 20):
    with open(path, "rb") as f:
        f.seek(start)
        remaining = length
        while remaining > 0:
            data = f.read(min(chunk, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data


def _content_disposition(filename: str) -> str:
    quoted = urllib.parse.quote(filename)
    return f"attachment; filename*=UTF-8''{quoted}"


# ---------------------------------------------------------------------------
# 页面与静态资源
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/config")
def config():
    """前端可用的模型 / 语言 / 任务列表。"""
    return {
        "models": [{"id": k, "label": v} for k, v in asr.MODELS.items()],
        "languages": [{"id": k, "label": v} for k, v in asr.LANGUAGES.items()],
        "tasks": [{"id": k, "label": v} for k, v in asr.TASKS.items()],
    }


# ---------------------------------------------------------------------------
# 识别
# ---------------------------------------------------------------------------

@app.post("/api/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    model: str = Form("small"),
    language: str = Form(""),
    task: str = Form("transcribe"),
):
    if model not in asr.MODELS:
        raise HTTPException(400, f"不支持的模型: {model}")
    if language not in asr.LANGUAGES:
        raise HTTPException(400, f"不支持的语言: {language}")
    if task not in asr.TASKS:
        raise HTTPException(400, f"不支持的任务: {task}")
    if file.size is not None and file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(400, f"文件过大（上限 {MAX_UPLOAD_MB} MB）")

    filename = os.path.basename(file.filename or "media.mp4")
    job = manager.create(
        "transcribe",
        filename,
        settings={"model": model, "language": language, "task": task},
    )
    dest = os.path.join(manager.job_dir(job.id), "media" + _safe_ext(filename))
    written = 0
    try:
        with open(dest, "wb") as f:
            while True:
                chunk = await file.read(1 << 20)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise _UploadTooLarge(f"文件超过上限 {MAX_UPLOAD_MB} MB")
                f.write(chunk)
    except _UploadTooLarge as exc:
        manager.delete(job.id)
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        manager.delete(job.id)
        raise HTTPException(500, f"文件保存失败: {exc}") from exc
    finally:
        await file.close()

    manager.set_media(job.id, dest)
    threading.Thread(target=_run_transcription, args=(job.id,), daemon=True).start()
    return {"job_id": job.id}


# ---------------------------------------------------------------------------
# 任务查询 / 编辑保存
# ---------------------------------------------------------------------------

@app.get("/api/jobs")
def list_jobs():
    return [
        {
            "id": j.id,
            "kind": j.kind,
            "filename": j.filename,
            "status": j.status,
            "created_at": j.created_at,
            "cue_count": len(j.segments),
        }
        for j in manager.list()
    ]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在或已被删除")
    return job.to_dict()


@app.post("/api/jobs/{job_id}/segments")
def save_segments(job_id: str, body: SegmentsBody):
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在或已被删除")
    manager.set_segments(job_id, body.segments)
    return {"ok": True, "count": len(body.segments)}


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在或已被删除")
    if job.status in ("queued", "running"):
        # 识别中删除会留残缺目录（Windows 下媒体文件被占用删不掉）且识别线程空跑
        raise HTTPException(409, "识别任务进行中，请等待完成后再删除")
    manager.delete(job_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 导入 / 导出
# ---------------------------------------------------------------------------

@app.post("/api/import")
async def import_subtitle(file: UploadFile = File(...)):
    if file.size is not None and file.size > IMPORT_MAX_BYTES:
        raise HTTPException(400, f"字幕文件过大（上限 {IMPORT_MAX_BYTES // 1024 // 1024} MB）")
    raw = await file.read()
    await file.close()
    if len(raw) > IMPORT_MAX_BYTES:
        raise HTTPException(400, f"字幕文件过大（上限 {IMPORT_MAX_BYTES // 1024 // 1024} MB）")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("gbk", errors="replace")
    cues = srt.normalize_cues(srt.parse_auto(text))
    if not cues:
        raise HTTPException(400, "未解析到任何字幕条目，请检查文件格式（SRT / VTT）")
    filename = os.path.basename(file.filename or "subtitles.srt")
    job = manager.create("import", filename)
    manager.set_segments(job.id, cues)
    # 导入即完成：置为 done，避免永远停留 queued 导致每次重启被误判为中断任务
    manager.set_status(job.id, "done", "导入完成")
    return {"job_id": job.id, "count": len(cues)}


@app.get("/api/jobs/{job_id}/export")
def export_subtitle(job_id: str, format: str = Query("srt")):
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在或已被删除")
    fmt = format.lower()
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(400, f"不支持的导出格式: {format}")
    cues = sorted(job.segments, key=lambda c: (c["start"], c["end"]))
    if fmt == "srt":
        content = srt.to_srt(cues)
    elif fmt == "vtt":
        content = srt.to_vtt(cues)
    elif fmt == "txt":
        content = srt.to_txt(cues)
    else:
        content = srt.to_json(cues)
    mime, ext = EXPORT_FORMATS[fmt]
    base = os.path.splitext(os.path.basename(job.filename))[0] or "subtitles"
    return Response(
        content=content.encode("utf-8"),
        media_type=mime,
        headers={"Content-Disposition": _content_disposition(f"{base}.{ext}")},
    )


# ---------------------------------------------------------------------------
# 媒体流（支持 Range，保证视频可拖动进度）
# ---------------------------------------------------------------------------

@app.get("/api/media/{job_id}")
def media(job_id: str, request: Request):
    job = manager.get(job_id)
    if job is None or not job.media_path or not os.path.exists(job.media_path):
        raise HTTPException(404, "媒体文件不存在")

    path = job.media_path
    size = os.path.getsize(path)
    media_type = MEDIA_TYPES.get(os.path.splitext(path)[1].lower(),
                                 "application/octet-stream")

    range_header = request.headers.get("range")
    if range_header:
        m = re.match(r"bytes=(\d*)-(\d*)", range_header.strip())
        if m:
            try:
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                elif m.group(2):  # 后缀范围：bytes=-N 表示最后 N 字节
                    start = max(0, size - int(m.group(2)))
                    end = size - 1
                else:  # "bytes=-" 等畸形请求
                    return Response(status_code=416,
                                    headers={"Content-Range": f"bytes */{size}"})
            except ValueError:
                return Response(status_code=416,
                                headers={"Content-Range": f"bytes */{size}"})
            end = min(end, size - 1)
            if start > end or start >= size:
                return Response(status_code=416,
                                headers={"Content-Range": f"bytes */{size}"})
            length = end - start + 1
            return StreamingResponse(
                _iter_file(path, start, length),
                status_code=206,
                media_type=media_type,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(length),
                },
            )
    return FileResponse(path, media_type=media_type,
                        headers={"Accept-Ranges": "bytes"})

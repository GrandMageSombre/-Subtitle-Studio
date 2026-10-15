"""任务管理：内存态 + 磁盘持久化。

每个任务一个目录 ``data/jobs/<job_id>/``：

- ``media.<ext>``   上传的媒体文件（识别任务）
- ``job.json``      任务元数据（状态、设置等）
- ``segments.json`` 字幕条目（识别结果 / 编辑结果自动保存）

服务重启后自动恢复历史任务；重启时处于 running/queued 的任务标记为 error。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid

logger = logging.getLogger("subtitle-studio.jobs")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _resolve_data_dir() -> str:
    """定位可写的 data 目录。

    优先级：
    1. 环境变量 ``SUBTITLE_STUDIO_DATA``（显式指定，最优先）
    2. 源码运行：项目目录下的 ``data/``
    3. 打包运行：exe 同级 ``data/``（便携模式）
    4. ``%LOCALAPPDATA%/SubtitleStudio/data``
    5. 系统临时目录（最后兜底，会打告警：清理临时目录会丢数据）
    """
    override = os.environ.get("SUBTITLE_STUDIO_DATA")
    if override:
        os.makedirs(override, exist_ok=True)
        return override

    if not getattr(sys, "frozen", False):
        return os.path.join(BASE_DIR, "data")

    candidates = [
        os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "data"),
        os.path.join(
            os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
            "SubtitleStudio",
            "data",
        ),
        os.path.join(tempfile.gettempdir(), "SubtitleStudio", "data"),
    ]
    for index, candidate in enumerate(candidates):
        try:
            os.makedirs(candidate, exist_ok=True)
            probe = os.path.join(candidate, ".write_test")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
            return candidate
        except OSError:
            if index < len(candidates) - 1:
                logger.warning("数据目录不可写，尝试下一个：%s", candidate)
    logger.warning(
        "所有候选数据目录均不可写，已退到系统临时目录 %s；"
        "清理临时目录会导致识别任务与字幕丢失，请设置环境变量 "
        "SUBTITLE_STUDIO_DATA 指向一个固定目录。",
        candidates[-1],
    )
    return candidates[-1]


DATA_DIR = _resolve_data_dir()
JOBS_DIR = os.path.join(DATA_DIR, "jobs")


class Job:
    """单个字幕任务。"""

    def __init__(self, job_id, kind, filename, media_path=None, settings=None,
                 created_at=None):
        self.id = job_id
        self.kind = kind                # transcribe | import
        self.filename = filename
        self.media_path = media_path
        self.settings = settings or {}
        self.created_at = created_at or time.time()
        self.status = "queued"          # queued | running | done | error
        self.progress = 0.0
        self.message = ""
        self.segments: list[dict] = []

    @property
    def dir(self) -> str:
        return os.path.join(JOBS_DIR, self.id)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "filename": self.filename,
            "status": self.status,
            "progress": round(self.progress, 4),
            "message": self.message,
            "segments": self.segments,
            "settings": self.settings,
            "has_media": bool(self.media_path and os.path.exists(self.media_path)),
            "created_at": self.created_at,
        }


class JobManager:
    """线程安全的任务注册表。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        os.makedirs(JOBS_DIR, exist_ok=True)
        self._restore()

    # -- 持久化 ------------------------------------------------------------

    def _restore(self) -> None:
        try:
            names = os.listdir(JOBS_DIR)
        except OSError:
            return
        for name in names:
            meta = os.path.join(JOBS_DIR, name, "job.json")
            if not os.path.isfile(meta):
                continue
            try:
                with open(meta, encoding="utf-8") as f:
                    data = json.load(f)
                job = Job(
                    data["id"],
                    data.get("kind", "import"),
                    data.get("filename", ""),
                    data.get("media_path"),
                    data.get("settings"),
                    data.get("created_at"),
                )
                if job.status in ("queued", "running"):
                    job.status = "error"
                    job.message = "服务曾中断，该识别任务未完成"
                    # 落盘，避免每次启动都重复翻转且丢失中断原因
                    self._jobs[job.id] = job
                    self._persist(job)
                seg_file = os.path.join(job.dir, "segments.json")
                if os.path.isfile(seg_file):
                    with open(seg_file, encoding="utf-8") as f:
                        job.segments = json.load(f)
                self._jobs[job.id] = job
            except Exception:
                continue

    def _persist(self, job: Job) -> None:
        """原子写盘：先写 .tmp 再 os.replace，避免异常退出留下半截 JSON。"""
        try:
            os.makedirs(job.dir, exist_ok=True)
            job_path = os.path.join(job.dir, "job.json")
            seg_path = os.path.join(job.dir, "segments.json")
            job_tmp, seg_tmp = job_path + ".tmp", seg_path + ".tmp"
            with open(job_tmp, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "id": job.id,
                        "kind": job.kind,
                        "filename": job.filename,
                        "media_path": job.media_path,
                        "settings": job.settings,
                        "status": job.status,
                        "created_at": job.created_at,
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            # segments.json 用紧凑格式：条目多时显著减小写盘体积与耗时
            with open(seg_tmp, "w", encoding="utf-8") as f:
                json.dump(job.segments, f, ensure_ascii=False, separators=(",", ":"))
            os.replace(seg_tmp, seg_path)
            os.replace(job_tmp, job_path)
        except OSError as exc:
            logger.warning("任务 %s 持久化失败：%s", job.id, exc)

    # -- 查询 --------------------------------------------------------------

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at,
                          reverse=True)

    def job_dir(self, job_id: str) -> str:
        return os.path.join(JOBS_DIR, job_id)

    # -- 变更 --------------------------------------------------------------

    def create(self, kind: str, filename: str, settings: dict | None = None) -> Job:
        with self._lock:
            job_id = uuid.uuid4().hex[:12]
            os.makedirs(os.path.join(JOBS_DIR, job_id), exist_ok=True)
            job = Job(job_id, kind, filename, None, settings)
            self._jobs[job_id] = job
            self._persist(job)
            return job

    def set_media(self, job_id: str, media_path: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.media_path = media_path
                self._persist(job)

    def set_status(self, job_id: str, status: str, message: str = "") -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = status
                job.message = message
                self._persist(job)

    def set_progress(self, job_id: str, progress: float, message: str | None = None) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.progress = max(0.0, min(1.0, float(progress)))
                if message is not None:
                    job.message = message

    def set_segments(self, job_id: str, segments: list[dict]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.segments = segments
                self._persist(job)

    def delete(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.pop(job_id, None)
            if job is None:
                return False
            shutil.rmtree(job.dir, ignore_errors=True)
            return True

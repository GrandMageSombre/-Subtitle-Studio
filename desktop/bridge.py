"""后端桥接：桌面界面与 ``backend`` 之间的唯一入口。

- 桌面模式**不启动 HTTP 服务**：界面直接调用后端 Python API；
- 识别耗时（模型加载 + CPU 推理），放在后台线程执行，进度经 Qt 信号回到
  界面线程（Qt 自动排队跨线程信号，界面无需加锁）；
- 媒体落位、字幕导入、导出序列化复用 :mod:`backend.service`，与浏览器模式
  共用同一实现，避免两条路径行为漂移；
- 「上次任务」「字幕样式」等界面状态存 :class:`QSettings`（原网页版用
  localStorage）。
"""
from __future__ import annotations

import logging
import os
import threading

from PySide6.QtCore import QObject, QSettings, Signal

from backend import service
from backend.jobs import JobManager

logger = logging.getLogger("subtitle-studio.desktop.bridge")

#: QSettings 键名
SETTINGS_LAST_JOB = "ui/last_job_id"


class TaskBridge(QObject):
    """任务创建 / 识别执行 / 导入导出 / 状态持久化。"""

    #: 识别进度：job_id, 0~1, 状态文本
    progressChanged = Signal(str, float, str)
    #: 任务状态变化：job_id, status(done/error), 消息
    jobStateChanged = Signal(str, str, str)
    #: 非致命错误提示：消息
    failed = Signal(str)

    def __init__(self, manager: JobManager | None = None, parent=None) -> None:
        super().__init__(parent)
        self.manager = manager if manager is not None else JobManager()
        self._settings = QSettings()
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    # -- 任务创建 / 识别 ---------------------------------------------------

    def job_dir(self, job_id: str) -> str:
        return self.manager.job_dir(job_id)

    def start_transcription(self, source_path: str, settings: dict) -> str:
        """创建任务并在后台线程「复制媒体 → 识别」，返回 job_id。

        大文件复制可能耗时数十秒，因此连「导入媒体」也放在后台线程，
        界面立刻拿到 job_id 并显示进度。
        """
        filename = os.path.basename(source_path)
        job = self.manager.create("transcribe", filename, settings=settings)
        with self._lock:
            thread = threading.Thread(
                target=self._run_new_job, args=(job.id, source_path),
                name=f"asr-{job.id}", daemon=True,
            )
            self._threads[job.id] = thread
        thread.start()
        return job.id

    def _run_new_job(self, job_id: str, source_path: str) -> None:
        """后台：先把媒体落位，再执行识别。"""
        try:
            self.manager.set_status(job_id, "running", "正在导入媒体…")
            with service.MediaStager(
                self.manager.job_dir(job_id), os.path.basename(source_path)
            ) as stager:
                stager.copy_from(source_path)
                media_path = stager.path
            self.manager.set_media(job_id, media_path)
        except Exception as exc:  # noqa: BLE001
            logger.exception("导入媒体失败：%s", source_path)
            self._fail(job_id, f"导入媒体失败：{exc}")
            with self._lock:
                self._threads.pop(job_id, None)
            return
        self._run_transcription(job_id)

    def is_running(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._threads

    @property
    def busy(self) -> bool:
        """是否有识别任务在跑（识别串行，界面据此禁用入口）。"""
        with self._lock:
            return bool(self._threads)

    def _run_transcription(self, job_id: str) -> None:
        """后台：执行识别（编排复用 backend/service.run_transcription）。"""
        try:
            ok = service.run_transcription(
                self.manager, job_id,
                progress_cb=lambda value, message: self.progressChanged.emit(
                    job_id, float(value or 0.0), message or ""),
            )
            job = self.manager.get(job_id)
            message = job.message if job is not None else ""
            self.jobStateChanged.emit(job_id, "done" if ok else "error", message)
        except BaseException as exc:  # noqa: BLE001
            # 兜底：逃逸异常也要回到界面，别让任务永远卡在 running
            logger.exception("识别线程异常退出：%s", job_id)
            self._fail(job_id, f"识别线程异常：{exc}")
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
        finally:
            with self._lock:
                self._threads.pop(job_id, None)

    def _fail(self, job_id: str, message: str) -> None:
        """把任务置为错误并通知界面。"""
        self.manager.set_status(job_id, "error", message)
        self.jobStateChanged.emit(job_id, "error", message)

    # -- 导入 / 导出 -------------------------------------------------------

    def import_subtitle(self, path: str) -> tuple[str, int]:
        """导入字幕文件为任务，返回 (job_id, 条数)。"""
        cues = service.import_cues_from_file(path)
        job = service.create_import_job(self.manager, os.path.basename(path), cues)
        return job.id, len(cues)

    def export_to_file(self, job_id: str, fmt: str, target_path: str) -> str:
        """按格式导出到指定路径，返回写入的文件名。"""
        job = self.manager.get(job_id)
        if job is None:
            raise ValueError("任务不存在或已被删除")
        data = service.export_bytes(job.segments, fmt)
        with open(target_path, "wb") as f:
            f.write(data)
        return os.path.basename(target_path)

    def export_filename(self, job_id: str, fmt: str) -> str:
        """建议的导出文件名（``视频.mp4`` -> ``视频.srt``）。"""
        job = self.manager.get(job_id)
        source = job.filename if job is not None else "subtitles.srt"
        return service.export_filename(source, fmt)

    # -- 任务读写 ----------------------------------------------------------

    def load_job(self, job_id: str) -> dict | None:
        job = self.manager.get(job_id)
        return job.to_dict() if job is not None else None

    def media_path(self, job_id: str) -> str:
        """任务媒体文件路径（不存在时返回空串，界面据此走「无媒体」分支）。"""
        job = self.manager.get(job_id)
        if job is None or not job.media_path:
            return ""
        return job.media_path if os.path.exists(job.media_path) else ""

    def list_jobs(self) -> list[dict]:
        return [
            {
                "id": job.id,
                "kind": job.kind,
                "filename": job.filename,
                "status": job.status,
                "created_at": job.created_at,
                "cue_count": len(job.segments),
            }
            for job in self.manager.list()
        ]

    def save_segments(self, job_id: str, cues: list[dict]) -> bool:
        """保存编辑结果（原子落盘，识别重启后可恢复）。"""
        job = self.manager.get(job_id)
        if job is None:
            return False
        self.manager.set_segments(job_id, [dict(c) for c in cues])
        return True

    def delete_job(self, job_id: str) -> bool:
        if self.is_running(job_id):
            return False
        return self.manager.delete(job_id)

    # -- 界面状态持久化 ----------------------------------------------------

    def last_job_id(self) -> str:
        value = self._settings.value(SETTINGS_LAST_JOB, "")
        return str(value) if value else ""

    def remember_job(self, job_id: str) -> None:
        self._settings.setValue(SETTINGS_LAST_JOB, job_id)

    def forget_job(self) -> None:
        self._settings.remove(SETTINGS_LAST_JOB)

    def get_setting(self, key: str, default=None):
        return self._settings.value(key, default)

    def set_setting(self, key: str, value) -> None:
        self._settings.setValue(key, value)

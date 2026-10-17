"""主窗口：顶栏 / 播放器 / 时间轴 / 字幕列表 / 识别与导出流程的编排。

界面与交互规格对齐浏览器版 ``static/app.js``：编辑条目集合、快捷键、
自动保存（防抖 1.2 秒）、重叠高亮、恢复上次任务、全局识别进度。
桌面模式不启动 HTTP 服务，识别与导入导出都直接调用后端 Python API
（见 :mod:`desktop.bridge`）。
"""
from __future__ import annotations

import logging
import os

from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtGui import QAction, QCursor
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from backend import service

from . import cues as C
from . import theme
from .bridge import TaskBridge
from .cue_table import CueListPanel
from .dialogs import HelpDialog, RecognizeDialog, ShiftDialog, Toast
from .player import VideoPanel, SubtitleOverlay
from .timeline import TimelineScrollBar, TimelineWidget

logger = logging.getLogger("subtitle-studio.desktop.main")

WINDOW_TITLE = "字幕工坊 Subtitle Studio"
#: 编辑自动保存防抖（与前端 SAVE_DEBOUNCE_MS 一致）
SAVE_DEBOUNCE_MS = 1200
#: 界面状态持久化键
KEY_FONT = "ui/sub_font_px"
KEY_POS = "ui/sub_bottom_pct"
KEY_SHOW_SUB = "ui/show_subtitle"


class MainWindow(QMainWindow):
    def __init__(self, bridge: TaskBridge) -> None:
        super().__init__()
        self.bridge = bridge
        self.cues = C.CueList()
        self.job: dict | None = None
        self.local_media_path = ""
        self._active = -1
        self._play_cue_end: float | None = None
        self._closing = False

        self.setWindowTitle(WINDOW_TITLE)
        self.resize(1360, 860)
        self.setMinimumSize(1080, 700)
        self.setAcceptDrops(True)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self._save_now)

        self._build_ui()
        self._wire()
        self._load_ui_settings()
        QTimer.singleShot(0, self._restore_last_job)

    # ------------------------------------------------------------------
    # 界面构建
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_topbar())
        root_layout.addWidget(self._build_global_progress())

        self.player = VideoPanel()
        self.timeline = TimelineWidget(self.cues)
        self.scrollbar = TimelineScrollBar(self.timeline)
        self.overlay = SubtitleOverlay()

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 6, 12)
        left_layout.setSpacing(8)
        left_layout.addWidget(self.player, 1)
        left_layout.addWidget(self.timeline)
        left_layout.addWidget(self.scrollbar)

        self.cue_panel = CueListPanel(self.cues)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(6, 12, 12, 12)
        right_layout.setSpacing(8)
        right_layout.addWidget(self._build_editor_toolbar())
        right_layout.addWidget(self.cue_panel, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 6)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([820, 520])
        root_layout.addWidget(splitter, 1)

        self.setCentralWidget(root)
        self.toast = Toast(root)

        self.recognize_dialog = RecognizeDialog(self)
        self.shift_dialog = ShiftDialog(self)
        self.help_dialog = HelpDialog(self)

    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("TopBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        name = QLabel("字幕工坊")
        name.setObjectName("BrandName")
        sub = QLabel("Subtitle Studio · 本地识别 + 在线编辑")
        sub.setObjectName("BrandSub")

        brand = QVBoxLayout()
        brand.setSpacing(0)
        brand.addWidget(name)
        brand.addWidget(sub)
        icon = QLabel("🎬")

        self._btn_recognize = QPushButton("🎙️ 语音识别")
        self._btn_recognize.setProperty("variant", "primary")
        self._btn_recognize.clicked.connect(self.open_recognize)
        self._btn_import = QPushButton("📂 导入字幕")
        self._btn_import.clicked.connect(self.open_import)
        self._btn_local = QPushButton("🎞️ 本地视频")
        self._btn_local.setToolTip("加载本地媒体预览对轴（不复制、不上传）")
        self._btn_local.clicked.connect(self.open_local_media)
        self._btn_export = QPushButton("💾 导出 ▾")
        self._btn_export.clicked.connect(self._show_export_menu)
        self._btn_help = QPushButton("?")
        self._btn_help.setFixedWidth(34)
        self._btn_help.setToolTip("快捷键与帮助")
        self._btn_help.clicked.connect(lambda: self.help_dialog.exec())

        layout.addWidget(icon)
        layout.addLayout(brand)
        layout.addStretch(1)
        for button in (self._btn_recognize, self._btn_import,
                       self._btn_local, self._btn_export, self._btn_help):
            layout.addWidget(button)
        return bar

    def _build_global_progress(self) -> QWidget:
        self._global_bar_wrap = QWidget()
        self._global_bar_wrap.setObjectName("GlobalProgressBar")
        layout = QHBoxLayout(self._global_bar_wrap)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(10)
        self._global_bar = QProgressBar()
        self._global_bar.setRange(0, 100)
        self._global_bar.setTextVisible(False)
        self._global_label = QLabel()
        self._global_label.setProperty("role", "hint")
        layout.addWidget(self._global_bar, 1)
        layout.addWidget(self._global_label)
        self._global_bar_wrap.setVisible(False)
        return self._global_bar_wrap

    def _build_editor_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("EditorToolbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        def small(text: str, tip: str, slot) -> QPushButton:
            button = QPushButton(text)
            button.setProperty("size", "small")
            button.setToolTip(tip)
            button.clicked.connect(slot)
            return button

        layout.addWidget(small("＋ 添加", "在播放处插入新字幕", self.add_cue))
        layout.addWidget(small("🗑 删除", "删除选中条目（Delete）", self.delete_selected))
        layout.addWidget(small("⇔ 合并下一条", "与下一条合并", self.merge_next))
        layout.addWidget(small("✂ 播放处拆分", "在播放处拆分选中条目", self.split_at_playhead))
        layout.addWidget(small("按标点拆分", "按中英文标点拆分所有长条目", self.split_all_by_punct))
        layout.addWidget(small("⇄ 平移…", "整体平移时间轴", self.open_shift))
        layout.addStretch(1)
        self._stats = QLabel()
        self._stats.setProperty("role", "stats")
        layout.addWidget(self._stats)
        return bar

    # ------------------------------------------------------------------
    # 信号连接
    # ------------------------------------------------------------------

    def _wire(self) -> None:
        # 播放器
        self.player.positionTicked.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.errorOccurred.connect(
            lambda msg: self._toast(f"无法预览该媒体（识别与编辑不受影响）：{msg}",
                                    "error"))
        self.player.subtitleVisibilityChanged.connect(self._on_subtitle_visibility)
        self.player.subtitleStyleChanged.connect(self._on_subtitle_style)
        self.player.recognizeRequested.connect(self.open_recognize)
        self.player.importRequested.connect(self.open_import)
        self.player.localVideoRequested.connect(self.open_local_media)

        # 时间轴
        self.timeline.seekRequested.connect(self.player.seek)
        self.timeline.cueSelected.connect(self.select_cue)
        self.timeline.cueEdited.connect(self._on_cues_edited)
        self.timeline.cueCommitted.connect(self._schedule_save)

        # 字幕列表
        self.cue_panel.cueSelected.connect(self.select_cue)
        self.cue_panel.textEdited.connect(self._on_text_edited)
        self.cue_panel.timeEdited.connect(self._on_time_edited)
        self.cue_panel.timeInvalid.connect(
            lambda i, f: self._toast("时间格式不正确，请用 时:分:秒.毫秒", "error"))
        self.cue_panel.playRequested.connect(self.play_cue)

        # 识别 / 导入
        self.recognize_dialog.startRequested.connect(self._start_transcription)
        self.recognize_dialog.backgroundRequested.connect(self._on_background_run)
        self.bridge.progressChanged.connect(self._on_progress)
        self.bridge.jobStateChanged.connect(self._on_job_state)

    # ------------------------------------------------------------------
    # 字幕编辑
    # ------------------------------------------------------------------

    def select_cue(self, index: int) -> None:
        if index >= len(self.cues):
            index = -1
        self.cue_panel.set_selected(index)
        self.timeline.set_selected(index)

    def add_cue(self) -> None:
        index = self.cues.add(self._playhead_time() if self._has_media() else 0.0)
        self._after_structural_change()
        self.select_cue(index)
        self.cue_panel.focus_row_text(index)

    def delete_selected(self) -> None:
        index = self.cue_panel.selected_index()
        if index < 0:
            return
        if not self.cues.remove(index):
            return
        self._after_structural_change()
        self.select_cue(min(index, len(self.cues) - 1))

    def merge_next(self) -> None:
        index = self.cue_panel.selected_index()
        if not self.cues.merge_next(index):
            self._toast("没有可合并的下一条", "error")
            return
        self._after_structural_change()
        self.select_cue(index)

    def split_at_playhead(self) -> None:
        index = self.cue_panel.selected_index()
        if index < 0:
            self._toast("请先选中一条字幕", "error")
            return
        if not self._has_media():
            self._toast("未加载媒体，无法按播放位置拆分", "error")
            return
        if not self.cues.split_at(index, self._playhead_time()):
            self._toast("请把播放头移到选中条目内部再拆分", "error")
            return
        self._after_structural_change()
        self.select_cue(index + 1)
        self.cue_panel.focus_row_text(index + 1)

    def split_all_by_punct(self) -> None:
        changed = self.cues.split_all_by_punct()
        if not changed:
            self._toast("没有需要拆分的长条目", "")
            return
        self._after_structural_change()
        self.select_cue(-1)
        self._toast(f"已按标点拆分 {changed} 条", "success")

    def open_shift(self) -> None:
        if not len(self.cues):
            self._toast("没有字幕可平移", "error")
            return
        if self.shift_dialog.exec() != ShiftDialog.DialogCode.Accepted:
            return
        offset, scope = self.shift_dialog.values()
        if not offset:
            return
        start = max(0, self.cue_panel.selected_index()) if scope == "from" else 0
        if self.cues.shift(offset, from_index=start):
            self._after_structural_change()
            self.select_cue(min(self.cue_panel.selected_index(), len(self.cues) - 1))
            self._toast(f"已平移 {offset:+g}s", "success")

    def play_cue(self, index: int) -> None:
        if not 0 <= index < len(self.cues):
            return
        if not self.player.has_source:
            self._toast("未加载媒体，无法预览", "error")
            return
        cue = self.cues.cues[index]
        self._play_cue_end = cue["end"]
        self.player.seek(cue["start"])
        self.player.play()

    def _on_text_edited(self, index: int, text: str) -> None:
        self.cues.set_text(index, text)
        self._schedule_save()

    def _on_time_edited(self, index: int, field: str, seconds: float) -> None:
        if field == "start":
            self.cues.set_start(index, seconds, snapshot=True)
        else:
            self.cues.set_end(index, seconds, snapshot=True)
        self._after_structural_change()

    def _on_cues_edited(self) -> None:
        """时间轴拖动过程中的实时刷新（排序与保存由提交阶段完成）。"""
        self.cue_panel.refresh_times()
        self.timeline.refresh()
        self._update_stats()

    def _after_structural_change(self) -> None:
        self.cue_panel.rebuild()
        self.timeline.refresh()
        self._update_stats()
        self._refresh_overlay()
        self._schedule_save()

    def _update_stats(self) -> None:
        count, overlaps, span = self.cues.stats()
        text = f"<b>{count}</b> 条 · 跨度 <b>{C.fmt_duration(span)}</b>"
        if overlaps:
            text += (f" · <b style='color:{theme.DANGER}'>{overlaps}</b> 处重叠")
        self._stats.setText(text)

    # ------------------------------------------------------------------
    # 播放联动
    # ------------------------------------------------------------------

    def _has_media(self) -> bool:
        return self.player.has_source

    def _playhead_time(self) -> float:
        return self.player.current_time()

    def _on_position(self, seconds: float) -> None:
        self.timeline.set_playhead(seconds)
        self._update_active_cue(seconds)
        if self._play_cue_end is not None and seconds >= self._play_cue_end:
            self.player.pause()
            self._play_cue_end = None

    def _on_duration(self, seconds: float) -> None:
        self.timeline.set_media_duration(seconds)

    def _update_active_cue(self, seconds: float) -> None:
        index = -1
        for i, cue in enumerate(self.cues.cues):
            if cue["start"] <= seconds < cue["end"]:
                index = i
                break
            if cue["start"] > seconds:
                break
        if index == self._active:
            return
        self._active = index
        self.cue_panel.set_active(index)
        self.timeline.set_active(index)
        self._refresh_overlay()

    def _refresh_overlay(self) -> None:
        if not self.player.subtitle_visible() or not self.player.has_source:
            self.overlay.clear()
            return
        index = self._active
        if 0 <= index < len(self.cues):
            self.overlay.set_text(self.cues.cues[index]["text"])
        else:
            self.overlay.clear()
        self._sync_overlay_geometry()

    def _sync_overlay_geometry(self) -> None:
        if self._closing or not self.isVisible():
            return
        self.overlay.sync_geometry(self.player.video_rect_global())
        if self._active < 0 or not self.player.subtitle_visible():
            self.overlay.hide()

    def _on_subtitle_visibility(self, visible: bool) -> None:
        self.bridge.set_setting(KEY_SHOW_SUB, "1" if visible else "0")
        self._refresh_overlay()

    def _on_subtitle_style(self, font_px: int, bottom_pct: int) -> None:
        self.bridge.set_setting(KEY_FONT, str(font_px))
        self.bridge.set_setting(KEY_POS, str(bottom_pct))
        self.overlay.set_style(font_px, bottom_pct)
        self._refresh_overlay()

    # ------------------------------------------------------------------
    # 保存 / 恢复
    # ------------------------------------------------------------------

    def _schedule_save(self) -> None:
        if self.job is None:
            return
        self._save_timer.start()

    def _save_now(self) -> bool:
        if self.job is None:
            return True
        # 兜底：把尚未回车/失焦的时间输入先提交，避免编辑丢失
        self.cue_panel.commit_pending_edits()
        if not self.bridge.save_segments(self.job["id"], self.cues.cues):
            logger.warning("自动保存失败：任务不存在（%s）", self.job.get("id"))
            self._toast("自动保存失败：任务已不存在", "error")
            return False
        return True

    def _load_ui_settings(self) -> None:
        font = int(self.bridge.get_setting(KEY_FONT, self.player.subtitle_style()[0]) or 19)
        pos = int(self.bridge.get_setting(KEY_POS, self.player.subtitle_style()[1]) or 7)
        show = str(self.bridge.get_setting(KEY_SHOW_SUB, "1")) != "0"
        self.player.apply_subtitle_style(font, pos)
        self.overlay.set_style(font, pos)
        self.player.set_subtitle_visible(show)

    def load_job_data(self, job: dict) -> None:
        """切换/载入任务：清空编辑态并重建界面。"""
        self._save_timer.stop()
        self.job = {
            "id": job["id"],
            "kind": job.get("kind", "import"),
            "filename": job.get("filename", ""),
            "has_media": bool(job.get("has_media")),
        }
        self.bridge.remember_job(job["id"])
        self.cues.replace_all(job.get("segments") or [], snapshot=False)
        self.cues.clear_history()
        self._active = -1
        self.local_media_path = ""

        media_path = self.bridge.media_path(job["id"])
        if self.job["has_media"] and media_path:
            self.player.set_source(media_path)
        else:
            self.player.clear_source()
            self.timeline.set_media_duration(0.0)

        self.cue_panel.rebuild()
        self.timeline.refresh()
        self._update_stats()
        if len(self.cues):
            self.select_cue(0)
        self._refresh_overlay()
        self.setWindowTitle(f"{WINDOW_TITLE} · {self.job['filename']}")

    def _restore_last_job(self) -> None:
        job_id = self.bridge.last_job_id()
        if not job_id:
            return
        job = self.bridge.load_job(job_id)
        if job is None:
            self.bridge.forget_job()
            return
        if job.get("status") != "done" and job.get("kind") != "import":
            return
        self.load_job_data(job)
        self._toast(f"已恢复上次字幕：{job.get('filename', '')}", "")

    # ------------------------------------------------------------------
    # 识别 / 导入 / 导出
    # ------------------------------------------------------------------

    def open_recognize(self) -> None:
        if self.bridge.busy:
            self._toast("已有识别任务进行中，请等待完成", "error")
            return
        self.recognize_dialog.reset_for_new_task()
        self.recognize_dialog.exec()

    def _start_transcription(self, path: str, settings: dict) -> None:
        if self.bridge.busy:
            self._toast("已有识别任务进行中，请等待完成", "error")
            return
        try:
            job_id = self.bridge.start_transcription(path, settings)
        except Exception as exc:  # noqa: BLE001
            self._toast(f"启动识别失败：{exc}", "error")
            self.recognize_dialog.set_running(False)
            return
        # 切换任务：清空旧编辑态（旧任务数据仍在服务端，可恢复）
        self.job = {"id": job_id, "kind": "transcribe",
                    "filename": os.path.basename(path), "has_media": True}
        self.bridge.remember_job(job_id)
        self.cues.replace_all([], snapshot=False)
        self.cues.clear_history()
        self.cue_panel.rebuild()
        self.timeline.refresh()
        self._update_stats()
        self._show_global_progress(True, "正在导入媒体…", 0.0)
        self.recognize_dialog.set_progress(0.0, "正在导入媒体…")

    def _on_progress(self, job_id: str, value: float, message: str) -> None:
        if self.job is None or job_id != self.job["id"]:
            return
        text = message or "识别中…"
        self._show_global_progress(True, text, value)
        self.recognize_dialog.set_progress(value, text)

    def _on_job_state(self, job_id: str, status: str, message: str) -> None:
        if self.job is not None and job_id != self.job["id"]:
            return          # 已切到别的任务：忽略旧任务的完成/失败回调
        if status == "error":
            self._show_global_progress(False)
            self.recognize_dialog.set_running(False)
            self.recognize_dialog.reject()
            self._toast(message, "error")
            return
        job = self.bridge.load_job(job_id)
        self._show_global_progress(False)
        self.recognize_dialog.set_running(False)
        self.recognize_dialog.accept()
        if job is None:
            return
        self.load_job_data(job)
        self._toast(message, "success")

    def _on_background_run(self) -> None:
        self._toast("识别仍在后台进行，完成后会自动加载", "")

    def open_import(self) -> None:
        if self.bridge.busy:
            self._toast("识别进行中，请等待完成后再导入字幕", "error")
            return
        patterns = " ".join(f"*{ext}" for ext in service.SUBTITLE_EXTS)
        path, _ = QFileDialog.getOpenFileName(
            self, "导入字幕文件", "",
            f"字幕文件 ({patterns});;所有文件 (*)")
        if not path:
            return
        self.import_subtitle(path)

    def import_subtitle(self, path: str) -> None:
        try:
            job_id, count = self.bridge.import_subtitle(path)
        except service.SubtitleParseError as exc:
            self._toast(f"导入失败：{exc}", "error")
            return
        except service.UploadTooLarge as exc:
            self._toast(str(exc), "error")
            return
        except OSError as exc:
            self._toast(f"导入失败：{exc}", "error")
            return
        job = self.bridge.load_job(job_id)
        if job is not None:
            self.load_job_data(job)
        self._toast(f"已导入 {count} 条字幕", "success")

    def open_local_media(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择本地媒体预览", "", "媒体文件 (*.*)")
        if not path:
            return
        self.local_media_path = path
        self.player.set_source(path)
        self.timeline.set_media_duration(self.player.duration())
        self._toast(f"已加载本地媒体：{os.path.basename(path)}", "success")

    def _show_export_menu(self) -> None:
        if self.job is None or not len(self.cues):
            self._toast("没有可导出的字幕", "error")
            return
        menu = QMenu(self)
        for fmt, label in service.EXPORT_LABELS.items():
            action = QAction(label, menu)
            action.triggered.connect(lambda _checked=False, f=fmt: self._export(f))
            menu.addAction(action)
        position = self._btn_export.mapToGlobal(
            QPoint(0, self._btn_export.height()))
        menu.exec(position)

    def _export(self, fmt: str) -> None:
        if self.job is None:
            return
        self.cue_panel.commit_pending_edits()   # 导出前提交未回车的编辑
        suggested = self.bridge.export_filename(self.job["id"], fmt)
        ext = os.path.splitext(suggested)[1].lstrip(".")
        path, _ = QFileDialog.getSaveFileName(
            self, "保存字幕文件", suggested, f"{ext.upper()} 字幕 (*.{ext});;所有文件 (*)")
        if not path:
            return
        try:
            filename = self.bridge.export_to_file(self.job["id"], fmt, path)
        except Exception as exc:  # noqa: BLE001
            self._toast(f"导出失败：{exc}", "error")
            return
        self._toast(f"已导出：{filename}", "success")

    def open_help(self) -> None:
        self.help_dialog.exec()

    # ------------------------------------------------------------------
    # 快捷键 / 拖放 / 事件
    # ------------------------------------------------------------------

    def keyPressEvent(self, event) -> None:  # noqa: N802
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QPlainTextEdit, QComboBox, QAbstractSpinBox)):
            super().keyPressEvent(event)     # 输入控件内保留原生编辑（含 Ctrl+Z）
            return
        key = event.key()
        modifiers = event.modifiers()
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        if ctrl and key == Qt.Key.Key_Z and not shift:
            self._undo()
            return
        if ctrl and (key == Qt.Key.Key_Y or (key == Qt.Key.Key_Z and shift)):
            self._redo()
            return
        if key == Qt.Key.Key_Space:
            self.player.toggle()
            return
        if key == Qt.Key.Key_Left:
            self.player.seek(self._playhead_time() - (10 if shift else 2))
            return
        if key == Qt.Key.Key_Right:
            self.player.seek(self._playhead_time() + (10 if shift else 2))
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.play_cue(self.cue_panel.selected_index())
            return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.delete_selected()
            return
        if key == Qt.Key.Key_BracketLeft:
            self._set_selected_start()
            return
        if key == Qt.Key.Key_BracketRight:
            self._set_selected_end()
            return
        super().keyPressEvent(event)

    def _undo(self) -> None:
        if self.cues.undo():
            self._after_undo_redo()

    def _redo(self) -> None:
        if self.cues.redo():
            self._after_undo_redo()

    def _after_undo_redo(self) -> None:
        self.cue_panel.rebuild()
        self.timeline.refresh()
        self._update_stats()
        self._refresh_overlay()
        self._schedule_save()

    def _set_selected_start(self) -> None:
        index = self.cue_panel.selected_index()
        if index < 0 or not self._has_media():
            return
        self.cues.set_start_at(index, self._playhead_time())
        self._after_structural_change()

    def _set_selected_end(self) -> None:
        index = self.cue_panel.selected_index()
        if index < 0 or not self._has_media():
            return
        self.cues.set_end_at(index, self._playhead_time())
        self._after_structural_change()

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if not path:
            return
        if service.is_subtitle_file(path):
            if self.bridge.busy:
                self._toast("识别进行中，请等待完成后再导入字幕", "error")
            else:
                self.import_subtitle(path)
        else:
            self.open_recognize_with(path)
        event.acceptProposedAction()

    def open_recognize_with(self, path: str) -> None:
        if self.bridge.busy:
            self._toast("已有识别任务进行中，请等待完成", "error")
            return
        self.recognize_dialog.reset_for_new_task()
        self.recognize_dialog.set_file(path)
        self.recognize_dialog.exec()

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        self._sync_overlay_geometry()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.toast.reposition()
        self._sync_overlay_geometry()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == event.Type.WindowStateChange:
            if self.isMinimized():
                self.overlay.hide()
            else:
                self._sync_overlay_geometry()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._closing = True
        self._save_timer.stop()
        saved = self._save_now()
        self.player.pause()          # 关闭时停止播放，避免残留声音
        self.overlay.close()
        if not saved:
            # 关窗后 toast 看不到，改用弹窗提示保存失败
            QMessageBox.warning(
                self, "保存失败",
                "当前字幕未能写入磁盘（任务可能已被删除或数据目录不可写）。")
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # 全局进度与提示
    # ------------------------------------------------------------------

    def _show_global_progress(self, show: bool, text: str = "",
                              value: float = 0.0) -> None:
        self._global_bar_wrap.setVisible(show)
        if show:
            self._global_bar.setValue(int(max(0.0, min(1.0, value)) * 100))
            self._global_label.setText(text)

    def _toast(self, message: str, level: str = "") -> None:
        self.toast.show_message(message, level)

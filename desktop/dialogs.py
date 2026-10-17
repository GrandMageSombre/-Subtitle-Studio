"""弹窗与浮动提示（识别 / 整体平移 / 帮助 / Toast）。"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from backend import asr
from backend import service

#: 可识别的媒体扩展名（与 backend.service.MEDIA_EXTS 同源，避免两处漂移）
MEDIA_EXTS = service.MEDIA_EXTS


class RecognizeDialog(QDialog):
    """语音识别弹窗：选文件 + 参数选择 + 进度。"""

    startRequested = Signal(str, dict)      # 文件路径, 识别设置
    backgroundRequested = Signal()          # 关闭弹窗但保持后台识别

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("🎙️ 语音识别生成字幕")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setAcceptDrops(True)
        self._file_path = ""
        self._running = False

        self._drop = ClickableLabel(
            "📹 点击选择或拖入视频 / 音频文件\n支持 mp4 / mov / mkv / webm / mp3 / wav / m4a …")
        self._drop.setObjectName("DropZone")
        self._drop.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._drop.setMinimumHeight(96)
        self._drop.clicked.connect(self._choose_file)

        self._file_label = QLabel()
        self._file_label.setProperty("role", "hint")
        self._file_label.setWordWrap(True)

        self._sel_model = QComboBox()
        for key, label in asr.MODELS.items():
            self._sel_model.addItem(label, key)
        self._sel_model.setCurrentIndex(list(asr.MODELS).index("small"))

        self._sel_language = QComboBox()
        for key, label in asr.LANGUAGES.items():
            self._sel_language.addItem(label, key)

        self._sel_task = QComboBox()
        for key, label in asr.TASKS.items():
            self._sel_task.addItem(label, key)

        self._sel_script = QComboBox()
        for key, label in asr.SCRIPTS.items():
            self._sel_script.addItem(label, key)

        form = QFormLayout()
        form.addRow("识别模型", self._sel_model)
        form.addRow("语言", self._sel_language)
        form.addRow("任务", self._sel_task)
        form.addRow("中文字形", self._sel_script)

        note = QLabel(
            "首次使用所选模型需联网下载（国内网络自动使用 hf-mirror 镜像）；"
            "识别全程在本机 CPU 完成，文件不会外传。「中文字形」用于把识别结果"
            "统一转为简体或繁体（词汇级转换），默认「自动」保持识别原样。")
        note.setProperty("role", "hint")
        note.setWordWrap(True)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setVisible(False)
        self._status = QLabel()
        self._status.setProperty("role", "hint")
        self._status.setVisible(False)

        self._btn_cancel = QPushButton("取消")
        self._btn_start = QPushButton("开始识别")
        self._btn_start.setProperty("variant", "primary")
        self._btn_start.setEnabled(False)
        self._btn_cancel.clicked.connect(self._on_cancel)
        self._btn_start.clicked.connect(self._on_start)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self._btn_cancel)
        buttons.addWidget(self._btn_start)

        layout = QVBoxLayout(self)
        layout.addWidget(self._drop)
        layout.addWidget(self._file_label)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(self._progress)
        layout.addWidget(self._status)
        layout.addLayout(buttons)

    # -- 外部接口 ----------------------------------------------------------

    def set_file(self, path: str) -> None:
        if not path:
            return
        self._file_path = path
        size_mb = os.path.getsize(path) / 1048576 if os.path.exists(path) else 0
        self._file_label.setText(f"已选择：{os.path.basename(path)}"
                                 f"（{size_mb:.1f} MB）")
        self._btn_start.setEnabled(not self._running)

    def settings(self) -> dict:
        return {
            "model": self._sel_model.currentData(),
            "language": self._sel_language.currentData() or "",
            "task": self._sel_task.currentData(),
            "script": self._sel_script.currentData(),
        }

    def reset_for_new_task(self) -> None:
        """打开弹窗时复位（保留上次的参数选择）。"""
        self._running = False
        self._file_path = ""
        self._file_label.clear()
        self._btn_start.setEnabled(False)
        self._btn_start.setText("开始识别")
        self._btn_cancel.setText("取消")
        self._progress.setVisible(False)
        self._progress.setValue(0)
        self._status.clear()
        self._status.setVisible(False)

    def set_running(self, running: bool, attach_background: bool = False) -> None:
        """识别进行中：禁用开始、取消按钮变为「后台运行」。"""
        self._running = running
        self._btn_start.setEnabled(not running and bool(self._file_path))
        self._btn_cancel.setText("后台运行" if attach_background else "取消")
        self._progress.setVisible(running)
        self._status.setVisible(running)

    def set_progress(self, value: float, message: str) -> None:
        self._progress.setValue(int(max(0.0, min(1.0, value)) * 100))
        self._status.setText(message)

    # -- 事件 --------------------------------------------------------------

    def _choose_file(self) -> None:
        patterns = " ".join(f"*.{ext}" for ext in MEDIA_EXTS)
        path, _ = QFileDialog.getOpenFileName(
            self, "选择视频 / 音频文件", "",
            f"媒体文件 ({patterns});;所有文件 (*)")
        if path:
            self.set_file(path)

    def _on_start(self) -> None:
        if not self._file_path:
            return
        self.set_running(True, attach_background=True)
        self.startRequested.emit(self._file_path, self.settings())

    def _on_cancel(self) -> None:
        if self._running:
            self.backgroundRequested.emit()
        self.reject()

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        if path and not service.is_subtitle_file(path):
            self.set_file(path)
        event.acceptProposedAction()


class ShiftDialog(QDialog):
    """整体平移时间轴。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("⇄ 整体平移时间轴")
        self.setModal(True)
        self.setMinimumWidth(360)

        self._offset = QDoubleSpinBox()
        self._offset.setRange(-36000.0, 36000.0)
        self._offset.setDecimals(1)
        self._offset.setSingleStep(0.1)
        self._offset.setSuffix(" 秒")
        self._offset.setToolTip("负数表示提前")

        self._scope = QComboBox()
        self._scope.addItem("全部字幕", "all")
        self._scope.addItem("从选中条目开始", "from")

        form = QFormLayout()
        form.addRow("平移量", self._offset)
        form.addRow("应用范围", self._scope)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("应用")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> tuple[float, str]:
        return self._offset.value(), self._scope.currentData()


class HelpDialog(QDialog):
    """快捷键与使用说明。"""

    HELP_HTML = """
    <b>播放</b>
    <div>空格：播放 / 暂停</div>
    <div>← →：快退 / 快进 2 秒（Shift 为 10 秒）</div>
    <div>Enter：播放选中字幕段</div>
    <br><b>对轴</b>
    <div>[ ]：选中条开始 / 结束时间对齐到播放处</div>
    <div>时间轴：单击色块跳转；按住拖动移动整条；拖边缘改时长；
         Ctrl+滚轮缩放；双击复位</div>
    <div>字幕「字号 / 位置」滑块实时调整画面叠加层</div>
    <br><b>编辑</b>
    <div>Delete：删除选中条目</div>
    <div>Ctrl+Z / Ctrl+Y：撤销 / 重做</div>
    <div>双击字幕行：跳转并播放该段</div>
    <br><b>提示</b>
    <div>编辑内容自动保存，重启不丢失</div>
    <div>红色条目表示与上一条时间重叠</div>
    <div>把视频 / 音频拖入窗口即开始识别，拖入字幕文件即导入</div>
    <div>导出 ASS 可直接导入必剪 / 剪映</div>
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("⌨️ 快捷键与使用说明")
        self.setModal(True)
        self.setMinimumWidth(520)

        text = QLabel(self.HELP_HTML)
        text.setTextFormat(Qt.TextFormat.RichText)
        text.setWordWrap(True)

        close = QPushButton("知道了")
        close.setProperty("variant", "primary")
        close.clicked.connect(self.accept)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(close)

        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addLayout(buttons)


class ClickableLabel(QLabel):
    """可点击的标签（拖放区 / 链接式控件）。"""

    clicked = Signal()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class Toast(QLabel):
    """右下角浮动提示（3.6 秒后自动消失，与前端 TOAST_MS 一致）。"""

    TOAST_MS = 3600

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, message: str, level: str = "") -> None:
        self.setText(message)
        self.setProperty("level", level or "info")
        self.style().unpolish(self)
        self.style().polish(self)
        self.adjustSize()
        self.reposition()
        self.show()
        self.raise_()
        self._timer.start(self.TOAST_MS)

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        width = min(max(self.width(), 160), max(200, parent.width() - 40))
        self.setFixedWidth(width)
        self.move(max(20, (parent.width() - width) // 2),
                  max(20, parent.height() - self.height() - 24))

"""原生播放器面板：QVideoWidget + QMediaPlayer + 控制条 + 画面字幕叠加。

视频解码走 QtMultimedia（Windows 上是 Media Foundation，用系统解码器）：
实测可正常播放 4K H.264/AAC mp4，而 Qt WebEngine 的 ffmpeg 构建不含
专有编解码器——这是本工程改回 Qt 原生播放的原因。

字幕叠加层（:class:`SubtitleOverlay`）是**独立透明窗口**：Windows 上
QVideoWidget 是 native 子窗口，同父的普通控件会被它遮挡，独立顶层窗口
才能稳定压在画面之上。
"""
from __future__ import annotations

import os

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import cues as C
from . import theme

#: 播放位置轮询间隔（毫秒）：界面按帧刷新播放头与字幕（前端用 rAF 轮询同理）
TICK_MS = 33
#: 字幕叠加字号/位置取值范围（与前端滑块一致）
FONT_MIN, FONT_MAX, FONT_DEFAULT = 12, 44, 19
POS_MIN, POS_MAX, POS_DEFAULT = 0, 90, 7
RATES = (0.5, 1.0, 1.5, 2.0)


class SubtitleOverlay(QWidget):
    """画面字幕叠加层（独立无边框透明窗口）。"""

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._label = QLabel(self)
        self._label.setWordWrap(True)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._font_px = FONT_DEFAULT
        self._bottom_pct = POS_DEFAULT
        self._apply_style()

    # -- 内容 --------------------------------------------------------------

    def set_style(self, font_px: int, bottom_pct: int) -> None:
        self._font_px = max(FONT_MIN, min(FONT_MAX, int(font_px)))
        self._bottom_pct = max(POS_MIN, min(POS_MAX, int(bottom_pct)))
        self._apply_style()

    def _apply_style(self) -> None:
        self._label.setStyleSheet(
            f"color: #ffffff; font-size: {self._font_px}px; font-weight: 600;"
            " background: rgba(0, 0, 0, 150); border-radius: 4px;"
            " padding: 2px 8px;"
        )

    def set_text(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            self.hide()
            return
        self._label.setText(text)
        self._relayout()

    def clear(self) -> None:
        self._label.clear()
        self.hide()

    # -- 几何 --------------------------------------------------------------

    def sync_geometry(self, rect: QRect) -> None:
        """跟随视频显示区域（全局坐标）；区域无效或窗口最小化时隐藏。"""
        self._rect = QRect(rect)
        if not rect.isValid() or rect.width() < 40 or rect.height() < 40:
            self.hide()
            return
        self.setGeometry(rect)
        self._relayout()
        if self._label.text() and not self.isVisible():
            self.show()

    def _relayout(self) -> None:
        rect = getattr(self, "_rect", QRect())
        if not rect.isValid():
            return
        width = max(80, rect.width() - 40)
        self._label.setFixedWidth(width)
        height = self._label.heightForWidth(width)
        if height <= 0:
            height = self._label.sizeHint().height()
        y = int(rect.height() * (100 - self._bottom_pct) / 100) - height
        y = max(0, min(y, max(0, rect.height() - height)))
        self._label.setGeometry((rect.width() - width) // 2, y, width, height)


class VideoPanel(QWidget):
    """视频显示区 + 播放控制条。"""

    positionTicked = Signal(float)
    durationChanged = Signal(float)
    playingChanged = Signal(bool)
    errorOccurred = Signal(str)
    subtitleVisibilityChanged = Signal(bool)
    subtitleStyleChanged = Signal(int, int)
    recognizeRequested = Signal()
    importRequested = Signal()
    localVideoRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._has_source = False
        self._sub_visible = True

        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._audio.setVolume(1.0)
        self._player.setAudioOutput(self._audio)

        self._video = QVideoWidget(self)
        self._player.setVideoOutput(self._video)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._video)
        self._stack.addWidget(self._build_empty_page())

        transport = self._build_transport()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._stack, 1)
        layout.addWidget(transport)

        self._player.durationChanged.connect(
            lambda ms: self.durationChanged.emit(ms / 1000.0))
        self._player.playbackStateChanged.connect(self._on_playback_state)
        self._player.errorOccurred.connect(self._on_error)

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(TICK_MS)
        self._tick_timer.timeout.connect(self._tick)
        # 只在有媒体时轮询播放位置（无媒体时时不空转）

    # -- 控件构建 ----------------------------------------------------------

    def _build_empty_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("EmptyState")
        page.setStyleSheet(f"#EmptyState {{ background: {theme.SURFACE}; }}")
        title = QLabel("🎬 开始制作字幕")
        title.setProperty("role", "title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc = QLabel("选择视频/音频自动识别字幕，或导入已有字幕文件在线编辑")
        desc.setProperty("role", "muted")
        desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = QLabel("所有处理均在本机完成，文件不上传云端")
        hint.setProperty("role", "hint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        btn_rec = QPushButton("🎙️ 语音识别")
        btn_rec.setProperty("variant", "primary")
        btn_rec.clicked.connect(self.recognizeRequested.emit)
        btn_imp = QPushButton("📂 导入字幕")
        btn_imp.clicked.connect(self.importRequested.emit)
        btn_local = QPushButton("🎞️ 本地视频")
        btn_local.clicked.connect(self.localVideoRequested.emit)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(btn_rec)
        buttons.addWidget(btn_imp)
        buttons.addWidget(btn_local)
        buttons.addStretch(1)

        layout = QVBoxLayout(page)
        layout.addStretch(2)
        layout.addWidget(title)
        layout.addWidget(desc)
        layout.addSpacing(10)
        layout.addLayout(buttons)
        layout.addSpacing(8)
        layout.addWidget(hint)
        layout.addStretch(3)
        return page

    def _build_transport(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("Transport")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        self._btn_play = QPushButton("▶")
        self._btn_play.setFixedWidth(36)
        self._btn_play.setToolTip("播放 / 暂停（空格）")
        self._btn_play.clicked.connect(self.toggle)

        self._time_label = QLabel(C.fmt_time(0) + " / " + C.fmt_time(0))
        self._time_label.setProperty("role", "mono")
        self._time_label.setMinimumWidth(170)

        self._chk_sub = QCheckBox("字幕")
        self._chk_sub.setChecked(True)
        self._chk_sub.setToolTip("在画面上显示字幕")
        self._chk_sub.toggled.connect(self._on_subtitle_toggled)

        self._rng_font = QSlider(Qt.Orientation.Horizontal)
        self._rng_font.setRange(FONT_MIN, FONT_MAX)
        self._rng_font.setValue(FONT_DEFAULT)
        self._rng_font.setFixedWidth(84)
        self._rng_font.setToolTip("字幕字号（像素），拖动即时预览")
        self._lbl_font = QLabel(f"{FONT_DEFAULT}px")
        self._lbl_font.setProperty("role", "mono")
        self._lbl_font.setMinimumWidth(34)

        self._rng_pos = QSlider(Qt.Orientation.Horizontal)
        self._rng_pos.setRange(POS_MIN, POS_MAX)
        self._rng_pos.setValue(POS_DEFAULT)
        self._rng_pos.setFixedWidth(84)
        self._rng_pos.setToolTip("字幕距画面底部的距离（百分比）")
        self._lbl_pos = QLabel(f"{POS_DEFAULT}%")
        self._lbl_pos.setProperty("role", "mono")
        self._lbl_pos.setMinimumWidth(30)

        self._sel_rate = QComboBox()
        for rate in RATES:
            self._sel_rate.addItem(f"{rate:g}x", rate)
        self._sel_rate.setCurrentIndex(1)
        self._sel_rate.setFixedWidth(64)
        self._sel_rate.setToolTip("播放倍速")
        self._sel_rate.currentIndexChanged.connect(
            lambda _i: self._player.setRate(self._sel_rate.currentData()))

        self._rng_font.valueChanged.connect(self._on_font_changed)
        self._rng_pos.valueChanged.connect(self._on_pos_changed)

        layout.addWidget(self._btn_play)
        layout.addWidget(self._time_label)
        layout.addStretch(1)
        layout.addWidget(self._chk_sub)
        layout.addWidget(QLabel("字号"))
        layout.addWidget(self._rng_font)
        layout.addWidget(self._lbl_font)
        layout.addWidget(QLabel("位置"))
        layout.addWidget(self._rng_pos)
        layout.addWidget(self._lbl_pos)
        layout.addWidget(self._sel_rate)
        return bar

    # -- 播放控制 ----------------------------------------------------------

    def set_source(self, path: str) -> None:
        """载入媒体（本地路径），停在 0 秒不自动播放。"""
        self._has_source = True
        self._stack.setCurrentWidget(self._video)
        self._player.setSource(QUrl.fromLocalFile(os.path.abspath(path)))
        self._player.setPosition(0)
        self._refresh_time_label()
        self._tick_timer.start()

    def clear_source(self) -> None:
        self._has_source = False
        self._tick_timer.stop()
        self._player.stop()
        self._player.setSource(QUrl())
        self._stack.setCurrentIndex(1)
        self._refresh_time_label()

    @property
    def has_source(self) -> bool:
        return self._has_source

    def toggle(self) -> None:
        if not self._has_source:
            return
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def play(self) -> None:
        if self._has_source:
            self._player.play()

    def pause(self) -> None:
        self._player.pause()

    def seek(self, seconds: float) -> None:
        if not self._has_source:
            return
        duration = self.duration()
        seconds = max(0.0, seconds)
        if duration > 0:
            seconds = min(seconds, duration)
        self._player.setPosition(int(round(seconds * 1000)))
        self.positionTicked.emit(seconds)
        self._refresh_time_label()

    def set_rate(self, rate: float) -> None:
        self._player.setRate(float(rate))

    def current_time(self) -> float:
        return self._player.position() / 1000.0

    def duration(self) -> float:
        ms = self._player.duration()
        return ms / 1000.0 if ms > 0 else 0.0

    def is_playing(self) -> bool:
        return (self._player.playbackState()
                == QMediaPlayer.PlaybackState.PlayingState)

    def video_rect_global(self) -> QRect:
        """视频显示区域的全局矩形（字幕叠加层定位用）。"""
        top_left = self._video.mapToGlobal(QPoint(0, 0))
        return QRect(top_left, self._video.size())

    # -- 字幕样式 ----------------------------------------------------------

    def subtitle_visible(self) -> bool:
        return self._chk_sub.isChecked()

    def set_subtitle_visible(self, visible: bool) -> None:
        """从持久化配置恢复显示开关（不重复触发保存）。"""
        self._chk_sub.blockSignals(True)
        self._chk_sub.setChecked(bool(visible))
        self._chk_sub.blockSignals(False)
        self._sub_visible = bool(visible)

    def subtitle_style(self) -> tuple[int, int]:
        return self._rng_font.value(), self._rng_pos.value()

    def apply_subtitle_style(self, font_px: int, bottom_pct: int) -> None:
        """设置字幕样式（从持久化配置恢复时用），不触发一次重复保存。"""
        for widget, value in ((self._rng_font, font_px), (self._rng_pos, bottom_pct)):
            widget.blockSignals(True)
            widget.setValue(int(value))
            widget.blockSignals(False)
        self._lbl_font.setText(f"{self._rng_font.value()}px")
        self._lbl_pos.setText(f"{self._rng_pos.value()}%")
        self.subtitleStyleChanged.emit(*self.subtitle_style())

    def _on_subtitle_toggled(self, checked: bool) -> None:
        self._sub_visible = checked
        self.subtitleVisibilityChanged.emit(checked)

    def _on_font_changed(self, value: int) -> None:
        self._lbl_font.setText(f"{value}px")
        self.subtitleStyleChanged.emit(*self.subtitle_style())

    def _on_pos_changed(self, value: int) -> None:
        self._lbl_pos.setText(f"{value}%")
        self.subtitleStyleChanged.emit(*self.subtitle_style())

    # -- 内部 --------------------------------------------------------------

    def _tick(self) -> None:
        if not self._has_source:
            return
        self.positionTicked.emit(self.current_time())
        self._refresh_time_label()

    def _refresh_time_label(self) -> None:
        self._time_label.setText(
            f"{C.fmt_time(self.current_time())} / {C.fmt_time(self.duration())}")

    def _on_playback_state(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._btn_play.setText("⏸" if playing else "▶")
        self.playingChanged.emit(playing)

    def _on_error(self, _error, message: str) -> None:
        if not self._has_source:
            return
        self.errorOccurred.emit(message or "媒体无法播放")

"""字幕列表编辑器（对应浏览器版的字幕表格）。

每行一个 :class:`CueRow`：序号 / 开始时间 / 结束时间 / 时长 / 文本 / 播放。
行控件直接编辑，改动即回传上层保存（防抖由主窗口负责）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import cues as C
from . import theme

#: 列宽（与表头保持一致）
COL_IDX_W = 42
COL_TIME_W = 108
COL_DUR_W = 62
COL_PLAY_W = 34
#: 文本行高与最大展开行数
LINE_H = 20
BASE_H = 8
MAX_LINES = 4


class CueRow(QWidget):
    """单条字幕行。"""

    selected = Signal(int)
    textEdited = Signal(int, str)
    timeEdited = Signal(int, str, float)     # index, field(start/end), 秒
    timeInvalid = Signal(int, str)           # 非法输入（保留原值并提示）
    playRequested = Signal(int)

    def __init__(self, index: int, cue: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CueRow")
        self.index = index
        self._loading = True
        self._values: tuple[float, float, str] = (0.0, 0.0, "")

        self._lbl_index = QLabel(str(index + 1))
        self._lbl_index.setFixedWidth(COL_IDX_W)
        self._lbl_index.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_index.setProperty("role", "hint")

        self._edit_start = QLineEdit()
        self._edit_start.setFixedWidth(COL_TIME_W)
        self._edit_start.setProperty("role", "mono")
        self._edit_start.setToolTip("开始时间，格式 时:分:秒.毫秒")

        self._edit_end = QLineEdit()
        self._edit_end.setFixedWidth(COL_TIME_W)
        self._edit_end.setProperty("role", "mono")
        self._edit_end.setToolTip("结束时间，格式 时:分:秒.毫秒")

        self._lbl_dur = QLabel()
        self._lbl_dur.setFixedWidth(COL_DUR_W)
        self._lbl_dur.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_dur.setProperty("role", "hint")

        self._text = QPlainTextEdit()
        self._text.setPlaceholderText("字幕文本")
        # 单行/多行文本都按控件宽度换行；滚动条一律关闭（高度按行数自适应）
        self._text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._text.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._text.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._text.document().setDocumentMargin(2)

        self._btn_play = QPushButton("▶")
        self._btn_play.setFixedWidth(COL_PLAY_W)
        self._btn_play.setToolTip("播放此段")
        self._btn_play.clicked.connect(lambda: self.playRequested.emit(self.index))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(8)
        layout.addWidget(self._lbl_index)
        layout.addWidget(self._edit_start)
        layout.addWidget(self._edit_end)
        layout.addWidget(self._lbl_dur)
        layout.addWidget(self._text, 1)
        layout.addWidget(self._btn_play)

        self._edit_start.editingFinished.connect(
            lambda: self._commit_time("start"))
        self._edit_end.editingFinished.connect(
            lambda: self._commit_time("end"))
        self._text.textChanged.connect(self._on_text_changed)

        self.set_cue(cue)
        self._loading = False

    # -- 数据 --------------------------------------------------------------

    def cue_values(self) -> tuple[float, float, str]:
        start = C.parse_time(self._edit_start.text())
        end = C.parse_time(self._edit_end.text())
        return start or 0.0, end or 0.0, self._text.toPlainText()

    def set_cue(self, cue: dict) -> None:
        """用数据刷新控件（不触发编辑信号）。"""
        self._loading = True
        self._values = (float(cue["start"]), float(cue["end"]), str(cue["text"]))
        self._edit_start.setText(C.fmt_time(cue["start"]))
        self._edit_start.setProperty("invalid", "false")
        self._edit_end.setText(C.fmt_time(cue["end"]))
        self._edit_end.setProperty("invalid", "false")
        self._lbl_dur.setText(f"{cue['end'] - cue['start']:.2f}s")
        if self._text.toPlainText() != cue["text"]:
            self._text.setPlainText(cue["text"])
        self._resize_text(cue["text"])
        self._repolish(self._edit_start)
        self._repolish(self._edit_end)
        self._loading = False

    def update_times(self, cue: dict) -> None:
        """只刷新时间与时长（时间轴拖动时用，避免打断文本编辑）。"""
        self.set_cue(cue)

    def set_index(self, index: int) -> None:
        self.index = index
        self._lbl_index.setText(str(index + 1))

    def set_state(self, state: str) -> None:
        """视觉状态：``""``/``overlap``/``selected``/``playing``。"""
        if self.property("state") == state:
            return
        self.setProperty("state", state)
        self._repolish(self)

    def focus_text(self) -> None:
        self._text.setFocus()

    def commit_pending(self) -> None:
        """提交时间输入框的当前内容（未回车/未失焦时的手动触发）。"""
        self._commit_time("start")
        self._commit_time("end")

    def scroll_into_view(self) -> None:
        area = self.parentWidget()
        while area is not None and not isinstance(area, QScrollArea):
            area = area.parentWidget()
        if area is not None:
            area.ensureWidgetVisible(self)

    # -- 事件 --------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.selected.emit(self.index)
        super().mousePressEvent(event)

    def _commit_time(self, field: str) -> None:
        if self._loading:
            return
        edit = self._edit_start if field == "start" else self._edit_end
        seconds = C.parse_time(edit.text())
        if seconds is None:
            edit.setProperty("invalid", "true")
            self._repolish(edit)
            self.timeInvalid.emit(self.index, field)
            return
        edit.setProperty("invalid", "false")
        self._repolish(edit)
        current = self._values[0 if field == "start" else 1]
        if abs(seconds - current) < 0.0005:
            # 内容未变：只把显示规范化，避免产生多余的保存/撤销步
            edit.setText(C.fmt_time(current))
            return
        self.timeEdited.emit(self.index, field, seconds)

    def _on_text_changed(self) -> None:
        if self._loading:
            return
        self._resize_text(self._text.toPlainText())
        self.textEdited.emit(self.index, self._text.toPlainText())

    def _resize_text(self, text: str) -> None:
        lines = min(MAX_LINES, max(1, len(str(text).split("\n"))))
        self._text.setFixedHeight(BASE_H + LINE_H * lines)

    @staticmethod
    def _repolish(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)


class CueListPanel(QWidget):
    """字幕列表（滚动区 + 行集合）。"""

    cueSelected = Signal(int)
    textEdited = Signal(int, str)
    timeEdited = Signal(int, str, float)
    timeInvalid = Signal(int, str)
    playRequested = Signal(int)

    def __init__(self, cue_list: C.CueList,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cues = cue_list
        self._rows: list[CueRow] = []
        self._selected = -1
        self._active = -1
        self._overlaps: list[bool] = []

        header = QWidget()
        header.setObjectName("CueHeader")
        self._build_header(header)

        self._container = QWidget()
        self._columns = QVBoxLayout(self._container)
        self._columns.setContentsMargins(0, 0, 0, 0)
        self._columns.setSpacing(0)
        self._columns.addStretch(1)

        self._empty = QLabel("暂无字幕条目")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setProperty("role", "hint")

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(self._container)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addWidget(self._scroll, 1)
        layout.addWidget(self._empty)

        self.rebuild()

    def _build_header(self, header: QWidget) -> None:
        header.setObjectName("CueHeader")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(8)

        def label(text: str, width: int | None = None,
                  align=Qt.AlignmentFlag.AlignCenter) -> QLabel:
            widget = QLabel(text)
            widget.setProperty("role", "hint")
            if width:
                widget.setFixedWidth(width)
            widget.setAlignment(align)
            return widget

        layout.addWidget(label("#", COL_IDX_W))
        layout.addWidget(label("开始时间", COL_TIME_W))
        layout.addWidget(label("结束时间", COL_TIME_W))
        layout.addWidget(label("时长", COL_DUR_W))
        layout.addWidget(label("字幕文本", None, Qt.AlignmentFlag.AlignLeft), 1)
        layout.addWidget(label("", COL_PLAY_W))

    # -- 数据 --------------------------------------------------------------

    def rebuild(self) -> None:
        """全量重建行（结构性改动后调用）。"""
        self._clear_rows()
        self._overlaps = self.cues.overlaps()
        for index, cue in enumerate(self.cues.cues):
            row = CueRow(index, cue)
            row.selected.connect(self._on_row_selected)
            row.textEdited.connect(self.textEdited)
            row.timeEdited.connect(self.timeEdited)
            row.timeInvalid.connect(self.timeInvalid)
            row.playRequested.connect(self.playRequested)
            self._columns.insertWidget(index, row)
            self._rows.append(row)
        # 条目变少时索引可能越界：与前端一致，选中回退到最后一条、播放中清空
        if self._selected >= len(self._rows):
            self._selected = len(self._rows) - 1
        if self._active >= len(self._rows):
            self._active = -1
        self._empty.setVisible(not self._rows)
        self._apply_states()

    def refresh_times(self) -> None:
        """时间变化（拖动）后的轻量刷新：只更新值，不重建控件。"""
        self._overlaps = self.cues.overlaps()
        for index, row in enumerate(self._rows):
            if index < len(self.cues.cues):
                row.set_cue(self.cues.cues[index])
        self._apply_states()

    def set_selected(self, index: int) -> None:
        self._selected = index
        self._apply_states()
        if 0 <= index < len(self._rows):
            self._rows[index].scroll_into_view()

    def set_active(self, index: int) -> None:
        self._active = index
        self._apply_states()
        if 0 <= index < len(self._rows) and self._active >= 0:
            self._rows[index].scroll_into_view()

    def selected_index(self) -> int:
        return self._selected

    def focus_row_text(self, index: int) -> None:
        if 0 <= index < len(self._rows):
            self._rows[index].focus_text()
            self._rows[index].scroll_into_view()

    def commit_pending_edits(self) -> None:
        """提交各行尚未回车/失焦的时间输入（导出、保存、关闭前兜底）。"""
        for row in self._rows:
            row.commit_pending()

    def selected_values(self) -> tuple[float, float, str]:
        """选中行的当前编辑值（保存时以控件内容为准）。"""
        if 0 <= self._selected < len(self._rows):
            return self._rows[self._selected].cue_values()
        return 0.0, 0.0, ""

    # -- 内部 --------------------------------------------------------------

    def _clear_rows(self) -> None:
        for row in self._rows:
            self._columns.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        self._rows.clear()

    def _on_row_selected(self, index: int) -> None:
        self._selected = index
        self._apply_states()
        self.cueSelected.emit(index)

    def _apply_states(self) -> None:
        for index, row in enumerate(self._rows):
            if index == self._active and index >= 0:
                state = "playing"
            elif index == self._selected:
                state = "selected"
            elif index < len(self._overlaps) and self._overlaps[index]:
                state = "overlap"
            else:
                state = ""
            row.set_state(state)

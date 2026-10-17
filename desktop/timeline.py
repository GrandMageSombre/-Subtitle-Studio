"""自绘时间轴（对齐 ``static/app.js`` 的 drawTimeline 与拖动规则）。

交互规格与浏览器版一致：

- 单击空白 / 单击色块（未超过拖动阈值）= 跳转到点击处（播放中选点）；
- 按住拖动超过 5px 才移动整条；色块两端 5px 为改时长把手（块宽 > 16px 才有）；
- ``Ctrl+滚轮`` 缩放（1x~200x）、滚轮平移、双击复位；
- 拖动结束按开始时间排序，维持「有序」不变量。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QWidget

from . import cues as C
from . import theme

#: 拖动阈值（像素）：超过才算拖动，否则视为点击跳转
DRAG_THRESHOLD_PX = 5
#: 色块边缘改时长的把手宽度
EDGE_PX = 5
#: 窄于此宽度的色块不提供边缘把手（与前端一致）
MIN_EDGE_WIDTH = 16
#: 色块区顶部偏移与底部留白
CUE_TOP = 26
CUE_BOTTOM_GAP = 6
#: 标尺步长候选（秒）
STEPS = (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800, 3600)


class TimelineWidget(QWidget):
    """时间轴：标尺 + 字幕色块 + 播放头。"""

    #: 视口（缩放/平移）变化，滚动条据此刷新
    viewChanged = Signal()
    #: 请求跳转到某时间
    seekRequested = Signal(float)
    #: 选中了某条
    cueSelected = Signal(int)
    #: 拖动过程中实时改动（界面刷新用）
    cueEdited = Signal()
    #: 拖动结束（需要落盘保存）
    cueCommitted = Signal()

    def __init__(self, cue_list: C.CueList, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cues = cue_list
        self.setMinimumHeight(92)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)

        self._zoom = 1.0
        self._view_start = 0.0
        self._media_duration = 0.0
        self._selected = -1
        self._active = -1
        self._playhead = 0.0
        self._drag: dict | None = None
        self._duration = 10.0
        self._view = 10.0
        self._px_per_sec = 10.0
        self._update_geom()

    # -- 外部接口 ----------------------------------------------------------

    def set_media_duration(self, seconds: float) -> None:
        self._media_duration = max(0.0, float(seconds or 0.0))
        self.refresh()

    def set_selected(self, index: int) -> None:
        if index != self._selected:
            self._selected = index
            self.update()

    def set_active(self, index: int) -> None:
        if index != self._active:
            self._active = index
            self.update()

    def set_playhead(self, seconds: float) -> None:
        seconds = max(0.0, float(seconds or 0.0))
        if abs(seconds - self._playhead) < 0.001:
            return
        self._playhead = seconds
        self.update()

    def refresh(self) -> None:
        """条目或时长变化后重绘（几何随之更新）。"""
        self._update_geom()
        self.update()
        self.viewChanged.emit()

    def view_state(self) -> tuple[float, float, float]:
        """返回 (view_start, view 跨度, 总时长)，滚动条绘制用。"""
        return self._view_start, self._view, self._duration

    def set_view_start(self, value: float) -> None:
        self._view_start = C.clamp(value, 0.0,
                                  max(0.0, self._duration - self._view))
        self.update()
        self.viewChanged.emit()

    def zoom_level(self) -> float:
        return self._zoom

    def reset_view(self) -> None:
        self._zoom = 1.0
        self._view_start = 0.0
        self.refresh()

    # -- 几何换算 ----------------------------------------------------------

    def _compute_duration(self) -> float:
        return max(self._media_duration, self.cues.duration_hint(), 1.0)

    def _update_geom(self) -> None:
        self._duration = self._compute_duration()
        self._view = self._duration / self._zoom
        self._view_start = C.clamp(self._view_start, 0.0,
                                  max(0.0, self._duration - self._view))
        width = max(1, self.width())
        self._px_per_sec = width / self._view if self._view > 0 else 10.0

    def x_of(self, seconds: float) -> float:
        return (seconds - self._view_start) * self._px_per_sec

    def t_of(self, x: float) -> float:
        return self._view_start + x / self._px_per_sec

    def _hit_test(self, t: float, x: float) -> tuple[int, str | None] | None:
        for i, cue in enumerate(self.cues.cues):
            x1, x2 = self.x_of(cue["start"]), self.x_of(cue["end"])
            if (x >= x1 - 4 and x <= x2 + 4
                    and t >= cue["start"] - 0.001 and t <= cue["end"] + 0.001):
                edge = None
                if x2 - x1 > MIN_EDGE_WIDTH:
                    if x <= x1 + EDGE_PX:
                        edge = "l"
                    elif x >= x2 - EDGE_PX:
                        edge = "r"
                return i, edge
        return None

    # -- 绘制 --------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802  Qt 约定命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width, height = self.width(), self.height()
        painter.fillRect(0, 0, width, height, QColor(theme.SURFACE))

        overlaps = self.cues.overlaps()
        self._paint_ruler(painter, width)
        self._paint_cues(painter, width, height, overlaps)
        self._paint_playhead(painter, height)
        painter.end()

    def _paint_ruler(self, painter: QPainter, width: int) -> None:
        step = next((s for s in STEPS if s * self._px_per_sec >= 64), STEPS[-1])
        painter.setFont(QFont(painter.font().family(), 8))
        painter.setPen(QColor("#d6dce8"))
        start = int(self._view_start / step) * step
        end = self._view_start + self._view + step
        t = start
        while t <= end:
            x = round(self.x_of(t))
            painter.drawLine(x, 0, x, 8)
            t += step
        painter.setPen(QColor(theme.TEXT_3))
        t = start
        while t <= end:
            x = round(self.x_of(t))
            painter.drawText(x + 3, 17, C.fmt_time_short(t))
            t += step
        painter.setPen(QColor("#dde3ee"))
        painter.drawLine(0, 23, width, 23)

    def _paint_cues(self, painter: QPainter, width: int, height: int,
                    overlaps: list[bool]) -> None:
        top = CUE_TOP
        block_height = max(6, height - top - CUE_BOTTOM_GAP)
        painter.setFont(QFont(painter.font().family(), 8))
        for i, cue in enumerate(self.cues.cues):
            x1, x2 = self.x_of(cue["start"]), self.x_of(cue["end"])
            if x2 < -20 or x1 > width + 20:
                continue
            block_width = max(2.0, x2 - x1)
            color = theme.CUE_FILL
            if i == self._selected:
                color = theme.CUE_SELECTED
            elif i == self._active:
                color = theme.CUE_ACTIVE
            if overlaps[i]:
                color = (theme.CUE_OVERLAP_SELECTED if i == self._selected
                         else theme.CUE_OVERLAP)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color))
            painter.drawRoundedRect(QRect(int(x1), top, int(round(block_width)),
                                          block_height), 3, 3)
            if block_width > 30:
                painter.save()
                painter.setClipRect(int(x1 + 2), top, int(block_width - 4),
                                    block_height)
                painter.setPen(QColor(255, 255, 255, 235))
                text = str(cue["text"]).split("\n")[0]
                painter.drawText(int(x1) + 5, top + block_height // 2 + 4, text)
                painter.restore()

    def _paint_playhead(self, painter: QPainter, height: int) -> None:
        if self._media_duration <= 0:
            return
        x = self.x_of(self._playhead)
        if x < -2 or x > self.width() + 2:
            return
        painter.setPen(QColor(theme.PLAYHEAD))
        painter.drawLine(int(x), 0, int(x), height)

    # -- 鼠标交互 ----------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x = event.position().x()
        y = event.position().y()
        t = self.t_of(x)
        hit = self._hit_test(t, x)
        if hit is None:
            self._drag = {"mode": "seek", "t": t, "x0": x, "y0": y}
            return
        index, edge = hit
        self._selected = index
        self.cueSelected.emit(index)
        self.update()
        self._drag = {
            "mode": "pending",
            "intent": f"resize-{edge}" if edge else "move",
            "idx": index,
            "t": t,
            "x0": x,
            "y0": y,
            "orig": dict(self.cues.cues[index]),
            "snap": False,
        }

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        x = event.position().x()
        y = event.position().y()
        if self._drag is None:
            self._update_cursor(x)
            return
        t = self.t_of(x)
        mode = self._drag["mode"]
        if mode == "seek":
            self._drag["t"] = t
            return
        if mode == "pending":
            if abs(x - self._drag["x0"]) <= DRAG_THRESHOLD_PX and \
                    abs(y - self._drag["y0"]) <= DRAG_THRESHOLD_PX:
                return
            self._drag["mode"] = self._drag["intent"]
            mode = self._drag["mode"]
        index = self._drag["idx"]
        if not 0 <= index < len(self.cues):
            self._drag = None
            return
        if not self._drag["snap"]:
            self.cues.snapshot()      # 真正拖动才留撤销步，单击不产生空撤销
            self._drag["snap"] = True
        delta = t - self._drag["t"]
        origin = self._drag["orig"]
        if mode == "move":
            self.cues.move_to(index, max(0.0, origin["start"] + delta),
                              max(0.0, origin["end"] + delta))
        elif mode == "resize-l":
            self.cues.resize_start(index, origin["start"] + delta)
        else:
            self.cues.resize_end(index, origin["end"] + delta)
        self.setCursor(Qt.CursorShape.SizeHorCursor if mode.startswith("resize")
                       else Qt.CursorShape.ClosedHandCursor)
        self.update()
        self.cueEdited.emit()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag is None or event.button() != Qt.MouseButton.LeftButton:
            return
        drag, self._drag = self._drag, None
        if drag["mode"] in ("seek", "pending"):
            # 单击空白或色块（未拖动）：跳转到点击处，便于播放中选点
            self.seekRequested.emit(drag["t"])
        else:
            self.cues.sort()
            self.cueEdited.emit()
            self.cueCommitted.emit()
        self.setCursor(Qt.CursorShape.CrossCursor)

    def cancel_drag(self) -> None:
        """窗口失焦等场景结束拖动：已发生的改动照常排序保存。"""
        if self._drag is None:
            return
        drag, self._drag = self._drag, None
        if drag["mode"] not in ("seek", "pending"):
            self.cues.sort()
            self.cueEdited.emit()
            self.cueCommitted.emit()
        self.setCursor(Qt.CursorShape.CrossCursor)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.reset_view()

    def wheelEvent(self, event) -> None:  # noqa: N802
        x = event.position().x()
        delta = event.angleDelta().y() or event.angleDelta().x()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            anchor = self.t_of(x)
            factor = 1.25 if delta > 0 else 0.8
            self._zoom = C.clamp(self._zoom * factor, 1.0, 200.0)
            self._update_geom()
            self._view_start = C.clamp(
                anchor - (x / max(1, self.width())) * self._view, 0.0,
                max(0.0, self._duration - self._view))
        else:
            self._view_start = C.clamp(
                self._view_start + delta * 0.002 * self._view, 0.0,
                max(0.0, self._duration - self._view))
        event.accept()
        self.update()
        self.viewChanged.emit()

    def _update_cursor(self, x: float) -> None:
        hit = self._hit_test(self.t_of(x), x)
        if hit is None:
            self.setCursor(Qt.CursorShape.CrossCursor)
        elif hit[1]:
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._update_geom()
        super().resizeEvent(event)
        self.viewChanged.emit()


class TimelineScrollBar(QWidget):
    """时间轴水平滚动条（自绘 thumb，拖动平移视口）。"""

    TRACK_MARGIN = 3

    def __init__(self, timeline: TimelineWidget, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.timeline = timeline
        self.setFixedHeight(12)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._dragging = False
        timeline.viewChanged.connect(self.update)

    def _thumb(self) -> QRect:
        view_start, view, duration = self.timeline.view_state()
        track = max(1, self.width())
        fraction = C.clamp(view / duration, 0.02, 1.0) if duration > 0 else 1.0
        width = max(28, int(track * fraction))
        left = int((view_start / duration) * track) if duration > 0 else 0
        left = int(C.clamp(left, 0, max(0, track - width)))
        return QRect(left, self.TRACK_MARGIN, width,
                     self.height() - 2 * self.TRACK_MARGIN)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(theme.BORDER))
        painter.drawRoundedRect(0, self.TRACK_MARGIN, self.width(),
                                self.height() - 2 * self.TRACK_MARGIN, 3, 3)
        painter.setBrush(QColor("#aab5c9" if self._dragging else "#c6cedd"))
        painter.drawRoundedRect(self._thumb(), 3, 3)
        painter.end()

    def _scroll_to(self, x: float) -> None:
        thumb = self._thumb()
        view_start, view, duration = self.timeline.view_state()
        center_frac = C.clamp(x / max(1, self.width()), 0.0, 1.0)
        self.timeline.set_view_start(center_frac * duration - view / 2)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._dragging = True
        if not self._thumb().contains(int(event.position().x()),
                                      int(event.position().y())):
            self._scroll_to(event.position().x())
        self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._dragging:
            self._scroll_to(event.position().x())

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._dragging = False
        self.update()

"""桌面界面控件测试（offscreen 平台，无需真实显示器）。

覆盖时间轴的几何换算、点击/拖动/缩放语义、字幕列表的非法输入处理、
浮动提示定位，以及主窗口装配（无法创建视频控件时自动跳过）。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QWheelEvent  # noqa: E402

from desktop import cues as C  # noqa: E402


def _mouse(kind, x, y, button=Qt.MouseButton.LeftButton):
    return QMouseEvent(kind, QPointF(x, y), QPointF(x, y), button, button,
                       Qt.KeyboardModifier.NoModifier)


def _press(widget, x, y):
    widget.mousePressEvent(_mouse(QEvent.Type.MouseButtonPress, x, y))


def _move(widget, x, y):
    widget.mouseMoveEvent(_mouse(QEvent.Type.MouseMove, x, y))


def _release(widget, x, y):
    widget.mouseReleaseEvent(_mouse(QEvent.Type.MouseButtonRelease, x, y))


def _wheel(widget, x, y, delta, ctrl=True):
    modifiers = (Qt.KeyboardModifier.ControlModifier if ctrl
                 else Qt.KeyboardModifier.NoModifier)
    event = QWheelEvent(QPointF(x, y), QPointF(x, y), QPoint(0, 0),
                        QPoint(0, delta), Qt.MouseButton.NoButton,
                        modifiers, Qt.ScrollPhase.NoScrollPhase, False)
    widget.wheelEvent(event)


@pytest.fixture
def timeline(qapp):
    from desktop.timeline import TimelineWidget

    cues = C.CueList([{"start": 1.0, "end": 3.0, "text": "第一条"},
                      {"start": 5.0, "end": 7.0, "text": "第二条"}])
    widget = TimelineWidget(cues)
    widget.resize(800, 92)
    widget.show()
    qapp.processEvents()
    return widget


# ---------------------------------------------------------------- 几何与命中

def test_geometry_roundtrip(timeline):
    for seconds in (0.0, 2.5, 9.99):
        assert timeline.t_of(timeline.x_of(seconds)) == pytest.approx(seconds)


def test_media_duration_extends_view(timeline):
    timeline.set_media_duration(120.0)
    start, view, duration = timeline.view_state()
    assert duration == pytest.approx(120.0)
    assert view == pytest.approx(120.0)
    assert start == 0.0


def test_hit_test_finds_cue_and_edge(timeline):
    x1 = timeline.x_of(1.0)
    x2 = timeline.x_of(3.0)
    index, edge = timeline._hit_test(timeline.t_of(x1 + 20), x1 + 20)
    assert index == 0 and edge is None
    index, edge = timeline._hit_test(timeline.t_of(x1 + 1), x1 + 1)
    assert (index, edge) == (0, "l")
    index, edge = timeline._hit_test(timeline.t_of(x2 - 1), x2 - 1)
    assert (index, edge) == (0, "r")
    assert timeline._hit_test(0.0, 0.0) is None


def test_narrow_cue_has_no_edge_handle(qapp):
    from desktop.timeline import TimelineWidget

    cues = C.CueList([{"start": 0.0, "end": 0.02, "text": "极短"}])
    widget = TimelineWidget(cues)
    widget.resize(800, 92)
    widget.show()
    qapp.processEvents()
    x = widget.x_of(0.0) + 1
    index, edge = widget._hit_test(widget.t_of(x), x)
    assert index == 0 and edge is None


# ---------------------------------------------------------------- 交互

def test_click_on_cue_selects_and_seeks(timeline):
    selected, seeks = [], []
    timeline.cueSelected.connect(selected.append)
    timeline.seekRequested.connect(seeks.append)
    x = timeline.x_of(1.0) + 30
    _press(timeline, x, 40)
    _release(timeline, x, 40)
    assert selected == [0]
    assert seeks and seeks[0] == pytest.approx(timeline.t_of(x))
    assert timeline.cues.cues[0]["start"] == pytest.approx(1.0)


def test_click_on_empty_area_only_seeks(timeline):
    selected, seeks = [], []
    timeline.cueSelected.connect(selected.append)
    timeline.seekRequested.connect(seeks.append)
    x = timeline.x_of(20.0)
    _press(timeline, x, 40)
    _release(timeline, x, 40)
    assert selected == []
    assert seeks and seeks[0] == pytest.approx(20.0)


def test_drag_below_threshold_does_not_move(timeline):
    committed = []
    timeline.cueCommitted.connect(lambda: committed.append(True))
    x = timeline.x_of(1.0) + 30
    _press(timeline, x, 40)
    _move(timeline, x + 3, 40)       # 3px < 阈值 5px
    _release(timeline, x + 3, 40)
    assert timeline.cues.cues[0]["start"] == pytest.approx(1.0)
    assert committed == []


def test_drag_moves_cue_and_commits(timeline):
    edited, committed = [], []
    timeline.cueEdited.connect(lambda: edited.append(True))
    timeline.cueCommitted.connect(lambda: committed.append(True))
    x = timeline.x_of(1.0) + 30
    _press(timeline, x, 40)
    _move(timeline, x + 100, 40)     # 越过阈值 -> 移动
    _release(timeline, x + 100, 40)
    delta = timeline.t_of(x + 100) - timeline.t_of(x)
    assert timeline.cues.cues[0]["start"] == pytest.approx(1.0 + delta, abs=0.02)
    assert edited and committed


def test_drag_left_edge_resizes(timeline):
    x1 = timeline.x_of(1.0)
    _press(timeline, x1 + 1, 40)
    _move(timeline, x1 + 40, 40)
    _release(timeline, x1 + 40, 40)
    assert timeline.cues.cues[0]["start"] > 1.0
    assert timeline.cues.cues[0]["end"] == pytest.approx(3.0, abs=0.01)


def test_cancel_drag_keeps_changes_and_commits(timeline):
    committed = []
    timeline.cueCommitted.connect(lambda: committed.append(True))
    x = timeline.x_of(1.0) + 30
    _press(timeline, x, 40)
    _move(timeline, x + 100, 40)
    timeline.cancel_drag()            # 窗口失焦等场景
    assert committed == [True]
    assert timeline._drag is None


def test_wheel_zoom_and_reset(timeline):
    assert timeline.zoom_level() == 1.0
    _wheel(timeline, 400, 40, 120)
    assert timeline.zoom_level() > 1.0
    for _ in range(40):
        _wheel(timeline, 400, 40, 120)
    assert timeline.zoom_level() <= 200.0
    _wheel(timeline, 400, 40, -120)
    assert timeline.zoom_level() < 200.0
    timeline.reset_view()
    assert timeline.zoom_level() == 1.0
    assert timeline.view_state()[0] == 0.0


def test_wheel_without_ctrl_scrolls_view(timeline):
    timeline.set_media_duration(600.0)
    timeline._zoom = 10.0
    timeline.refresh()
    # 向下滚动 = 视图向后移动（与前端 tl.viewStart += delta * 0.002 * view 一致）
    _wheel(timeline, 400, 40, 240, ctrl=False)
    assert timeline.view_state()[0] > 0.0


# ---------------------------------------------------------------- 列表 / 提示

def test_cue_row_flags_invalid_time(qapp):
    from desktop.cue_table import CueRow

    invalid = []
    row = CueRow(0, {"start": 0.0, "end": 1.0, "text": "x"})
    row.timeInvalid.connect(lambda i, f: invalid.append(f))
    row._edit_start.setText("不是时间")
    row._edit_start.editingFinished.emit()
    assert invalid == ["start"]
    assert row._edit_start.property("invalid") == "true"


def test_cue_row_emits_valid_time_change(qapp):
    from desktop.cue_table import CueRow

    edits = []
    row = CueRow(0, {"start": 0.0, "end": 1.0, "text": "x"})
    row.timeEdited.connect(lambda i, f, s: edits.append((f, s)))
    row._edit_start.setText("00:00:02.500")
    row._edit_start.editingFinished.emit()
    assert edits == [("start", 2.5)]


def test_cue_row_skips_unchanged_time(qapp):
    """内容没变时不应产生保存/撤销信号（否则每次失焦都多一步撤销）。"""
    from desktop.cue_table import CueRow

    edits = []
    row = CueRow(0, {"start": 1.0, "end": 2.0, "text": "x"})
    row.timeEdited.connect(lambda i, f, s: edits.append((f, s)))
    row._edit_start.setText("00:00:01.000")
    row._edit_end.setText("2.0")           # 等价写法，值未变
    row.commit_pending()
    assert edits == []


def test_cue_panel_commit_pending_edits(qapp):
    """未回车的时间输入可由 commit_pending_edits 兜底提交（导出/保存/关窗前）。"""
    from desktop.cue_table import CueListPanel

    cues = C.CueList([{"start": 0.0, "end": 1.0, "text": "一"}])
    panel = CueListPanel(cues)
    edits = []
    panel.timeEdited.connect(lambda i, f, s: edits.append((i, f, s)))
    panel._rows[0]._edit_start.setText("00:00:05.000")
    panel.commit_pending_edits()
    assert edits == [(0, "start", 5.0)]


def test_cue_panel_rebuild_and_state(qapp):
    from desktop.cue_table import CueListPanel

    cues = C.CueList([{"start": 0.0, "end": 3.0, "text": "一"},
                      {"start": 2.0, "end": 4.0, "text": "二"}])
    panel = CueListPanel(cues)
    assert len(panel._rows) == 2
    panel.set_selected(1)
    assert panel.selected_index() == 1
    assert panel._rows[1].property("state") == "selected"
    panel.set_active(0)
    assert panel._rows[0].property("state") == "playing"
    # 与上一条重叠的行标红
    assert panel._rows[1].property("state") == "selected"
    cues.remove(0)
    panel.rebuild()
    assert len(panel._rows) == 1
    assert panel.selected_values()[2] == "二"


def test_toast_positions_inside_parent(qapp):
    from PySide6.QtWidgets import QWidget

    from desktop.dialogs import Toast

    parent = QWidget()
    parent.resize(600, 400)
    parent.show()
    qapp.processEvents()
    toast = Toast(parent)
    toast.show_message("提示信息", "success")
    assert toast.isVisible()
    assert 0 <= toast.y() < parent.height()
    assert toast.width() <= parent.width()


def test_recognize_dialog_settings_roundtrip(qapp):
    from desktop.dialogs import RecognizeDialog

    dialog = RecognizeDialog()
    settings = dialog.settings()
    assert set(settings) == {"model", "language", "task", "script"}
    assert settings["model"] in ("tiny", "base", "small", "medium", "large-v3")
    assert settings["script"] in ("auto", "simplified", "traditional")


def test_main_window_builds_and_loads_job(bridge, tmp_path, qapp):
    """主窗口整体装配：加载导入任务后列表、时间轴、统计同步更新。"""
    pytest.importorskip("PySide6.QtMultimedia")
    from desktop.main_window import MainWindow

    try:
        window = MainWindow(bridge)
    except Exception as exc:  # noqa: BLE001  offscreen 下可能不支持视频控件
        pytest.skip(f"当前平台无法创建视频控件：{exc}")

    srt = tmp_path / "装配.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\n你好\n", encoding="utf-8")
    job_id, _ = bridge.import_subtitle(str(srt))
    window.load_job_data(bridge.load_job(job_id))

    assert len(window.cues) == 1
    assert len(window.cue_panel._rows) == 1
    assert "1" in window._stats.text()
    assert window.job["id"] == job_id

    window.add_cue()
    assert len(window.cues) == 2
    assert window.cue_panel.selected_index() >= 0

    window.delete_selected()
    assert len(window.cues) == 1

    window._undo()
    assert len(window.cues) == 2
    window._redo()
    assert len(window.cues) == 1

    # 未回车的时间输入在保存前被提交（导出/关窗同样走这条兜底）
    row = window.cue_panel._rows[0]
    row._edit_start.setText("00:00:03.000")
    assert window._save_now() is True
    assert window.cues.cues[0]["start"] == pytest.approx(3.0)

    # 别的任务的状态回调不得覆盖当前任务
    window._on_job_state("other-job", "done", "识别完成")
    assert window.job["id"] == job_id

    window.close()

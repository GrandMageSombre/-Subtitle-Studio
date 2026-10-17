"""桌面版字幕模型（desktop/cues.py）单元测试。

覆盖与前端 static/app.js 对齐的编辑语义：新增避让、合并、拆分、按标点拆分、
平移、撤销/重做、时间格式化与解析。
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import asr  # noqa: E402
from desktop import cues as C  # noqa: E402


def cue(start, end, text="文本"):
    return {"start": start, "end": end, "text": text}


# ---------------------------------------------------------------- 时间格式

@pytest.mark.parametrize("seconds,expected", [
    (0, "00:00:00.000"),
    (1.5, "00:00:01.500"),
    (61.25, "00:01:01.250"),
    (3661.001, "01:01:01.001"),
    (-5, "00:00:00.000"),
    (float("nan"), "00:00:00.000"),
])
def test_fmt_time(seconds, expected):
    assert C.fmt_time(seconds) == expected


@pytest.mark.parametrize("text,expected", [
    ("00:00:01.500", 1.5),
    ("0:01:01,250", 61.25),
    ("01:01.001", 61.001),
    ("00:00:07", 7.0),
    ("1:2:3", 3723.0),
    ("00:99", 99.0),          # 与前端一致：秒位不设上限，按数值累加
])
def test_parse_time_valid(text, expected):
    assert C.parse_time(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["", "abc", "1:2:3:4", "12"])
def test_parse_time_invalid(text):
    assert C.parse_time(text) is None


def test_fmt_time_short_and_duration():
    assert C.fmt_time_short(65) == "01:05"
    assert C.fmt_time_short(3725) == "1:02:05"
    assert C.fmt_duration(62) == "1分02秒"
    assert C.fmt_duration(3600) == "60分00秒"


# ---------------------------------------------------------------- 标点拆分

def test_split_text_at_punct_lines():
    parts = C.split_text_at_punct("你好，世界。第二句！\n第三句？")
    assert parts == ["你好，世界。", "第二句！", "第三句？"]


def test_split_text_at_punct_without_punct_keeps_line():
    assert C.split_text_at_punct("没有标点的一行") == ["没有标点的一行"]


def test_split_text_at_punct_empty_falls_back_to_original():
    assert C.split_text_at_punct("") == [""]


def test_punct_set_matches_backend():
    """桌面实现的标点集必须与 backend.asr.SENT_PUNCT 完全一致。"""
    assert C.SENT_PUNCT == asr.SENT_PUNCT


# ---------------------------------------------------------------- 集合操作

def test_init_sorts_and_normalizes():
    cl = C.CueList([cue(5, 6), {"start": "1", "end": "1", "text": "x"}])
    assert [c["start"] for c in cl.cues] == [1.0, 5.0]
    # 结束时间不晚于开始时间时补 0.1 秒，避免零长条目
    assert cl.cues[0]["end"] == pytest.approx(1.1)


def test_add_inserts_after_earlier_cues():
    cl = C.CueList([cue(0, 1), cue(5, 6)])
    index = cl.add(2.0)
    assert index == 1                      # 插在「结束时间已早于 2.0」的条目之后
    assert cl.cues[1]["start"] == 2.0
    assert cl.cues[1]["end"] == pytest.approx(4.0)


def test_add_avoids_next_cue():
    cl = C.CueList([cue(0, 1.0), cue(2.2, 4)])
    assert cl.add(2.0) == 1
    # 下一条从 2.2 开始：新条目结束时间被压到 max(t+0.2, next.start-0.05)
    assert cl.cues[1]["end"] == pytest.approx(2.2)


def test_add_at_playhead_without_media_starts_at_zero():
    cl = C.CueList([])
    assert cl.add(0.0) == 0
    assert cl.cues[0]["end"] == pytest.approx(C.DEFAULT_DURATION)


def test_add_appends_after_all_earlier_cues():
    cl = C.CueList([cue(0, 1), cue(2, 3)])
    assert cl.add(10.0) == 2
    assert cl.cues[-1]["end"] == pytest.approx(12.0)


def test_remove_and_bounds():
    cl = C.CueList([cue(0, 1), cue(2, 3)])
    assert cl.remove(5) is False
    assert cl.remove(0) is True
    assert len(cl) == 1
    assert cl.cues[0]["start"] == 2.0


def test_merge_next_unions_time_and_text():
    cl = C.CueList([cue(0, 1, "第一行"), cue(2, 3, "第二行")])
    assert cl.merge_next(0) is True
    assert len(cl) == 1
    assert cl.cues[0]["end"] == 3.0
    assert cl.cues[0]["text"] == "第一行\n第二行"


def test_merge_next_requires_following_cue():
    cl = C.CueList([cue(0, 1)])
    assert cl.merge_next(0) is False


def test_split_at_middle():
    cl = C.CueList([cue(0, 4, "ABCDEFGH")])
    assert cl.split_at(0, 2.0) is True
    assert len(cl) == 2
    assert cl.cues[0]["end"] == pytest.approx(2.0)
    assert cl.cues[1]["start"] == pytest.approx(2.0)
    assert cl.cues[0]["text"] + cl.cues[1]["text"] == "ABCDEFGH"
    assert cl.cues[0]["text"]  # 两侧都不为空


@pytest.mark.parametrize("at", [0.0, 0.02, 3.98, 4.0, 5.0])
def test_split_at_rejects_outside_window(at):
    cl = C.CueList([cue(0, 4, "ABCDEFGH")])
    assert cl.split_at(0, at) is False
    assert len(cl) == 1


def test_split_all_by_punct_assigns_scaled_time():
    cl = C.CueList([cue(0, 6, "你好。世界啊！好")])
    changed = cl.split_all_by_punct()
    assert changed == 1
    assert [c["text"] for c in cl.cues] == ["你好。", "世界啊！", "好"]
    starts = [c["start"] for c in cl.cues]
    assert starts == sorted(starts)
    assert cl.cues[0]["start"] == 0.0
    assert cl.cues[-1]["end"] == pytest.approx(6.0)
    for cur, nxt in zip(cl.cues, cl.cues[1:]):
        assert nxt["start"] >= cur["end"] - 0.001


def test_split_all_by_punct_no_change_leaves_no_history():
    cl = C.CueList([cue(0, 2, "单句无标点")])
    assert cl.split_all_by_punct() == 0
    assert cl.can_undo is False


def test_shift_moves_and_clamps_at_zero():
    cl = C.CueList([cue(1, 2), cue(5, 6)])
    assert cl.shift(-3.0) is True
    assert cl.cues[0]["start"] == 0.0     # 不越过 0
    assert cl.cues[1]["start"] == 2.0
    assert cl.shift(0.0) is False


def test_shift_from_index_keeps_earlier_cues():
    cl = C.CueList([cue(0, 1), cue(3, 4), cue(6, 7)])
    cl.shift(1.0, from_index=1)
    assert cl.cues[0]["start"] == 0.0
    assert [c["start"] for c in cl.cues[1:]] == [4.0, 7.0]


def test_shift_preserves_positive_duration():
    cl = C.CueList([cue(0.2, 0.3)])
    cl.shift(-10.0)
    assert cl.cues[0]["start"] == 0.0
    assert cl.cues[0]["end"] > cl.cues[0]["start"]


def test_set_start_end_constraints():
    cl = C.CueList([cue(2, 4)])
    cl.set_start(0, 4.5)
    assert cl.cues[0]["end"] > cl.cues[0]["start"]
    cl.set_end(0, 0.5)
    assert cl.cues[0]["start"] < cl.cues[0]["end"]
    cl.set_start(0, -3)
    assert cl.cues[0]["start"] == 0.0


def test_set_at_playhead_keeps_min_gap():
    cl = C.CueList([cue(2, 4)])
    cl.set_start_at(0, 10.0)     # 远超结束时间
    assert cl.cues[0]["start"] == pytest.approx(4 - C.MIN_GAP)
    cl.set_end_at(0, 0.0)        # 早于开始时间
    assert cl.cues[0]["end"] == pytest.approx(cl.cues[0]["start"] + C.MIN_GAP)


def test_move_to_and_resize():
    cl = C.CueList([cue(2, 4)])
    cl.move_to(0, 5.0, 7.0)
    assert (cl.cues[0]["start"], cl.cues[0]["end"]) == (5.0, 7.0)
    cl.resize_start(0, 6.0)
    assert cl.cues[0]["start"] == 6.0
    cl.resize_start(0, 99.0)      # 不得超过结束时间
    assert cl.cues[0]["start"] <= cl.cues[0]["end"] - C.MIN_GAP
    cl.resize_end(0, 0.0)         # 不得早于开始时间
    assert cl.cues[0]["end"] >= cl.cues[0]["start"] + C.MIN_GAP
    cl.move_to(0, -3.0, -1.0)     # 负值截止到 0，时长仍为正
    assert cl.cues[0]["start"] == 0.0
    assert cl.cues[0]["end"] > 0.0


# ---------------------------------------------------------------- 统计 / 历史

def test_overlaps_and_stats():
    cl = C.CueList([cue(0, 3), cue(2, 4), cue(5, 6)])
    assert cl.overlaps() == [False, True, False]
    count, overlaps, span = cl.stats()
    assert (count, overlaps) == (3, 1)
    assert span == pytest.approx(6.0)


def test_undo_redo_roundtrip():
    cl = C.CueList([cue(0, 1)])
    cl.add(5.0)
    assert len(cl) == 2
    assert cl.undo() is True
    assert len(cl) == 1
    assert cl.redo() is True
    assert len(cl) == 2
    assert cl.redo() is False


def test_undo_history_limit():
    cl = C.CueList([cue(0, 1)], history_limit=3)
    for _ in range(5):
        cl.shift(1.0)
    assert len(cl._undo) == 3
    while cl.undo():
        pass
    assert len(cl) == 1          # 只能回退到最早保留的快照


def test_snapshot_clears_redo():
    cl = C.CueList([cue(0, 1)])
    cl.add(5.0)
    cl.undo()
    cl.add(9.0)                  # 新的编辑应清空重做栈
    assert cl.can_redo is False


def test_continuous_edit_without_snapshot():
    cl = C.CueList([cue(0, 1, "旧")])
    cl.snapshot()
    cl.set_text(0, "新")
    cl.set_text(0, "更新")
    assert cl.can_undo is True
    cl.undo()
    assert cl.cues[0]["text"] == "旧"


def test_replace_all_resets_and_sorts():
    cl = C.CueList([cue(0, 1)])
    cl.add(5.0)
    cl.replace_all([cue(9, 10), cue(2, 3)])
    assert [c["start"] for c in cl.cues] == [2.0, 9.0]
    assert cl.can_undo is True   # 整体替换本身可撤销


def test_index_of_tracks_objects_after_sort():
    cl = C.CueList([cue(0, 1), cue(5, 6)])
    ref = cl.cues[1]
    cl.move_to(1, -3.0, -2.0)
    cl.sort()
    assert cl.index_of(ref) == 0


def test_duration_hint_uses_last_cue():
    cl = C.CueList([])
    assert cl.duration_hint() == 1.0
    cl.replace_all([cue(0, 30)])
    assert cl.duration_hint() == pytest.approx(30.3)

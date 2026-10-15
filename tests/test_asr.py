"""ASR 断句逻辑测试（用合成词级时间戳，不依赖真实模型）。"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import asr  # noqa: E402


def make_segment(words, start=None, end=None, text=None):
    """words: [(word, start, end), ...]"""
    ws = [SimpleNamespace(word=w, start=s, end=e) for w, s, e in words]
    return SimpleNamespace(
        words=ws,
        start=start if start is not None else (ws[0].start if ws else 0),
        end=end if end is not None else (ws[-1].end if ws else 0),
        text=text if text is not None else "".join(w for w, _, _ in words),
    )


def test_split_at_sentence_punct():
    # 4 个句子，每句 5 字符 × 0.4s = 2.0s，应切成 4 条
    words = []
    t = 0.0
    for sentence in ["你好世界。", "这是测试。", "字幕识别。", "编辑完成。"]:
        for ch in sentence:
            words.append((ch, t, t + 0.4))
            t += 0.4
    seg = make_segment(words)
    cues = asr.refine_segments([seg])
    assert len(cues) == 4
    assert cues[0]["text"] == "你好世界。"
    assert cues[0]["start"] == pytest.approx(0.0)
    assert cues[0]["end"] == pytest.approx(2.0)
    assert cues[-1]["text"] == "编辑完成。"
    # 时间连续
    for a, b in zip(cues, cues[1:]):
        assert b["start"] >= a["start"]


def test_short_fragments_are_merged():
    # 单字碎片 "好。" / "行。" 应并入相邻句子；1.5s 的句子独立成条
    words = []
    t = 0.0
    for token in ["这是", "一个", "测试", "句子", "。", "好。", "行。", "完整", "结尾", "。"]:
        words.append((token, t, t + 0.5))
        t += 0.5
    cues = asr.refine_segments([make_segment(words)])
    assert len(cues) == 2
    assert cues[0]["text"] == "这是一个测试句子。好。行。"
    assert cues[1]["text"] == "完整结尾。"


def test_long_segment_without_punct_gets_hard_cut():
    # 30 秒无标点长文本 -> 触发硬切（2 * MAX_DURATION = 14s）
    words = [("字", i * 0.5, i * 0.5 + 0.5) for i in range(60)]
    seg = make_segment(words)
    cues = asr.refine_segments([seg])
    assert len(cues) >= 2
    for c in cues:
        assert c["end"] - c["start"] <= asr.HARD_DURATION + 0.6


def test_short_segment_kept_whole():
    words = [("你", 0.0, 0.3), ("好", 0.3, 0.6), ("。", 0.6, 0.9)]
    seg = make_segment(words)
    cues = asr.refine_segments([seg])
    assert len(cues) == 1
    assert cues[0]["text"] == "你好。"


def test_segment_without_words_fallback():
    seg = SimpleNamespace(words=None, start=1.0, end=3.5, text="  没有词级时间戳  ")
    cues = asr.refine_segments([seg])
    assert cues == [{"start": 1.0, "end": 3.5, "text": "没有词级时间戳"}]


def test_english_words_joined_with_spaces():
    words = [
        (" Hello", 0.0, 0.5), (" world", 0.5, 1.0), (".", 1.0, 1.2),
        (" This", 1.2, 1.6), (" is", 1.6, 2.0), (" a", 2.0, 2.4),
        (" test", 2.4, 2.8), (".", 2.8, 3.0),
    ]
    seg = make_segment(words)
    cues = asr.refine_segments([seg])
    assert len(cues) == 2
    assert cues[0]["text"] == "Hello world."
    assert cues[1]["text"] == "This is a test."


def test_decimal_and_abbrev_not_sentence_end():
    # 小数（3.5）与缩写（Mr./U.S.A.）中的句点不触发断句
    words = [
        ("圆周率", 0.0, 0.5), ("是", 0.5, 1.0), ("3.5", 1.0, 1.5),
        ("左右", 1.5, 2.0), ("。", 2.0, 2.5),
        ("Mr.", 2.5, 3.0), ("Smith", 3.0, 3.5), ("说", 3.5, 4.0), ("。", 4.0, 4.5),
        ("U.S.A.", 4.5, 5.5), ("的", 5.5, 6.0), ("公司", 6.0, 6.5), ("。", 6.5, 7.0),
    ]
    cues = asr.refine_segments([make_segment(words)])
    texts = [c["text"] for c in cues]
    assert "圆周率是3.5左右。" in texts
    assert "Mr.Smith说。" in texts
    assert "U.S.A.的公司。" in texts


def test_plain_english_dot_still_splits():
    words = [
        ("I", 0.0, 0.3), (" like", 0.3, 0.6), (" it", 0.6, 0.9), (".", 0.9, 1.1),
        (" Good", 1.1, 1.5), (".", 1.5, 1.7),
    ]
    cues = asr.refine_segments([make_segment(words)])
    assert [c["text"] for c in cues] == ["I like it.", "Good."]

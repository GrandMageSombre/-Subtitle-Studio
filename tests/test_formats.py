"""字幕格式（SRT / VTT / TXT / JSON）解析与序列化测试。"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import srt  # noqa: E402
from tests.gen_vectors import gen_cues, gen_overlapping_cues  # noqa: E402


# ---------------------------------------------------------------- 时间戳

def test_format_and_parse_roundtrip():
    for s in [0, 0.001, 1.5, 59.999, 60, 3599.999, 3600, 7325.25, 359999.999]:
        assert srt.parse_ts(srt.format_ts_srt(s)) == pytest.approx(s, abs=0.001)
        assert srt.parse_ts(srt.format_ts_vtt(s)) == pytest.approx(s, abs=0.001)


def test_parse_ts_lenient():
    assert srt.parse_ts("01:02.500") == pytest.approx(62.5)
    assert srt.parse_ts("1:02:03.4") == pytest.approx(3723.4)
    assert srt.parse_ts("00:00:01,5") == pytest.approx(1.5)
    assert srt.parse_ts(" 00:00:10,000 ") == pytest.approx(10.0)
    with pytest.raises(ValueError):
        srt.parse_ts("not-a-time")


# ---------------------------------------------------------------- SRT

@pytest.mark.parametrize("seed", range(15))
@pytest.mark.parametrize("n", [1, 5, 40])
def test_srt_roundtrip(seed, n):
    cues = gen_cues(n, seed * 100 + n)
    parsed = srt.parse_srt(srt.to_srt(cues))
    assert parsed == srt.normalize_cues(cues)


def test_srt_dirty_input():
    text = (
        "\ufeff1\r\n00:00:01,000 --> 00:00:03,000\r\n第一行\r\n第二行\r\n"
        "\r\n"
        "00:00:04,000 --> 00:00:05,500\n没有序号的一行\n"
        "\n\n"
        "3\n00:00:06,000 --> 00:00:07,000\n"      # 空文本条目 -> 跳过
        "\n\n"
        "坏块没有时间轴\n"                          # 无时间轴 -> 跳过
        "\n\n"
        "4\n00:01:00,000 --> 00:01:02,000\n正常结尾\n"
    )
    cues = srt.parse_srt(text)
    assert len(cues) == 3
    assert cues[0]["text"] == "第一行\n第二行"
    assert cues[0]["start"] == 1.0 and cues[0]["end"] == 3.0
    assert cues[1]["text"] == "没有序号的一行"
    assert cues[2]["text"] == "正常结尾"


def test_srt_end_before_start_is_clamped():
    cues = srt.parse_srt("1\n00:00:05,000 --> 00:00:01,000\n文本\n")
    assert cues[0]["end"] > cues[0]["start"]


# ---------------------------------------------------------------- VTT

@pytest.mark.parametrize("seed", range(15))
def test_vtt_roundtrip(seed):
    cues = gen_cues(30, seed * 7 + 3)
    parsed = srt.parse_vtt(srt.to_vtt(cues))
    assert parsed == srt.normalize_cues(cues)


def test_vtt_full_features():
    text = (
        "WEBVTT - 标题\n"
        "Kind: captions\n"
        "Language: zh\n"
        "\n"
        "NOTE 这是一个注释块\n"
        "跨越多行\n"
        "\n"
        "intro\n"
        "00:00:01.000 --> 00:00:03.000 line:0 position:50%\n"
        "第一句 &amp; 第二句 &lt;tag&gt;\n"
        "\n"
        "00:00:04.000 --> 00:00:05.000\n"
        "没有标识行\n"
    )
    cues = srt.parse_vtt(text)
    assert len(cues) == 2
    assert cues[0]["text"] == "第一句 & 第二句 <tag>"
    assert cues[0]["start"] == 1.0 and cues[0]["end"] == 3.0
    assert cues[1]["text"] == "没有标识行"


def test_vtt_literal_entity_roundtrip():
    # 文本中本身含有 &lt; / &amp; 字样时必须原样往返（&amp; 最后反转义）
    cues = [{"start": 1.0, "end": 2.0, "text": "a &lt; b &amp; c <tag> &quot;d&quot;"}]
    assert srt.parse_vtt(srt.to_vtt(cues)) == srt.normalize_cues(cues)


def test_round3_half_up():
    # half-up 舍入，与前端 Math.round 一致（Python 原生 round 是银行家舍入）
    assert srt.round3(1.9999) == 2.0
    assert srt.round3(0.0004) == 0.0
    assert srt.round3(0.0006) == 0.001


def test_parse_auto():
    assert srt.parse_auto("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhi\n") == srt.parse_vtt(
        "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nhi\n")
    assert len(srt.parse_auto("1\n00:00:01,000 --> 00:00:02,000\nhi\n")) == 1


# ---------------------------------------------------------------- 其它格式

def test_txt_and_json_export():
    cues = gen_cues(10, 42)
    txt = srt.to_txt(cues)
    assert txt.count("\n") >= len(cues)
    for c in cues:
        for line in c["text"].split("\n"):
            assert line in txt
    import json
    data = json.loads(srt.to_json(cues))
    assert data == srt.normalize_cues(cues)


# ---------------------------------------------------------------- 清洗

@pytest.mark.parametrize("seed", range(10))
def test_normalize_sorts_and_cleans(seed):
    raw = gen_overlapping_cues(25, seed)
    out = srt.normalize_cues(raw)
    assert out == sorted(out, key=lambda c: (c["start"], c["end"]))
    for c in out:
        assert c["end"] > c["start"]
        assert c["text"].strip()

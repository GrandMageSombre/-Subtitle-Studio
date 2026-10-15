"""SRT / VTT / ASS / TXT / JSON 字幕格式的解析与序列化。

内部统一数据模型（cue 列表）::

    [{"start": 1.5, "end": 3.25, "text": "第一行\n第二行"}, ...]

- 时间单位为秒（float），统一保留 3 位小数精度；
- 文本保留换行符，解析时清理行尾空白与首尾空行；
- SRT 时间戳分隔符为逗号，VTT 为点号；两者均可被 :func:`parse_ts` 兼容解析。
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable

Cue = dict[str, Any]


def round3(value: float) -> float:
    """half-up 舍入到 3 位小数（与前端 ``Math.round`` 行为一致，避免 1ms 偏差）。"""
    return math.floor(float(value) * 1000 + 0.5) / 1000


def _ms(seconds: float) -> int:
    """秒 -> 毫秒，half-up 舍入。"""
    return math.floor(max(0.0, float(seconds)) * 1000 + 0.5)

# 支持 "HH:MM:SS,mmm" / "MM:SS.mmm" / "H:MM:SS.mmm" 等写法
_TS_RE = re.compile(r"(?:(\d{1,3}):)?(\d{1,2}):(\d{1,2})[.,](\d{1,3})")
_TIMING_LINE_RE = re.compile(r"^\s*(.+?)\s*-->\s*(.+?)\s*$")


# ---------------------------------------------------------------------------
# 时间戳
# ---------------------------------------------------------------------------

def format_ts_srt(seconds: float) -> str:
    """秒 -> ``HH:MM:SS,mmm``（SRT 格式）。"""
    ms = _ms(seconds)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def format_ts_vtt(seconds: float) -> str:
    """秒 -> ``HH:MM:SS.mmm``（WebVTT 格式）。"""
    return format_ts_srt(seconds).replace(",", ".")


def format_ts_ass(seconds: float) -> str:
    """秒 -> ``H:MM:SS.cc``（ASS 格式，厘秒 half-up 舍入）。"""
    cs = math.floor(max(0.0, float(seconds)) * 100 + 0.5)
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6_000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def parse_ts(token: str) -> float:
    """解析时间戳为秒。支持带/不带小时、逗号或点号分隔。"""
    m = _TS_RE.search(str(token))
    if not m:
        raise ValueError(f"无法解析时间戳: {token!r}")
    hours = int(m.group(1)) if m.group(1) else 0
    minutes = int(m.group(2))
    seconds = int(m.group(3))
    millis = int(m.group(4).ljust(3, "0"))
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


# ---------------------------------------------------------------------------
# 文本清理 / 转义
# ---------------------------------------------------------------------------

def _clean_text(text: str) -> str:
    """规范化文本：统一换行、去掉行尾空白与首尾空行。"""
    lines = [
        ln.rstrip()
        for ln in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _esc_vtt(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _unesc_vtt(text: str) -> str:
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def _normalize_newlines(text: str) -> str:
    return text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

def to_srt(cues: Iterable[Cue]) -> str:
    """cue 列表 -> SRT 文本。"""
    blocks = []
    for i, cue in enumerate(cues, 1):
        text = _clean_text(cue.get("text", ""))
        blocks.append(
            f"{i}\n{format_ts_srt(cue['start'])} --> {format_ts_srt(cue['end'])}\n{text}"
        )
    return "\n\n".join(blocks) + "\n"


def to_vtt(cues: Iterable[Cue]) -> str:
    """cue 列表 -> WebVTT 文本。"""
    blocks = []
    for cue in cues:
        text = _esc_vtt(_clean_text(cue.get("text", "")))
        blocks.append(
            f"{format_ts_vtt(cue['start'])} --> {format_ts_vtt(cue['end'])}\n{text}"
        )
    return "WEBVTT\n\n" + "\n\n".join(blocks) + "\n"


def to_txt(cues: Iterable[Cue]) -> str:
    """cue 列表 -> 纯文本（仅文字，无时间轴）。"""
    return "\n".join(_clean_text(cue.get("text", "")) for cue in cues) + "\n"


#: ASS 导出头部模板（必剪 / 剪映 / Aegisub 均可识别）
_ASS_HEADER = """[Script Info]
Title: Subtitle Studio Export
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def to_ass(cues: Iterable[Cue]) -> str:
    """cue 列表 -> ASS（SubStation Alpha）文本，换行转为 ``\\N``。"""
    lines = []
    for cue in cues:
        text = _clean_text(cue.get("text", "")).replace("\n", r"\N")
        lines.append(
            f"Dialogue: 0,{format_ts_ass(cue['start'])},{format_ts_ass(cue['end'])},"
            f"Default,,0,0,0,,{text}"
        )
    return _ASS_HEADER + "\n".join(lines) + "\n"


def to_json(cues: Iterable[Cue]) -> str:
    """cue 列表 -> JSON 文本。"""
    import json

    data = [
        {"start": round3(c["start"]), "end": round3(c["end"]),
         "text": _clean_text(c.get("text", ""))}
        for c in cues
    ]
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def _finish_cue(start: float, end: float, text: str) -> Cue | None:
    text = _clean_text(text)
    if not text:
        return None
    start = max(0.0, float(start))
    end = float(end)
    if end <= start:
        end = start + 0.1
    return {"start": round3(start), "end": round3(end), "text": text}


def parse_srt(text: str) -> list[Cue]:
    """解析 SRT。对缺序号、BOM、CRLF、空文本块等常见脏数据健壮。"""
    text = _normalize_newlines(text)
    cues: list[Cue] = []
    for block in re.split(r"\n{2,}", text.strip()):
        lines = block.split("\n")
        idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if idx is None:
            continue
        m = _TIMING_LINE_RE.match(lines[idx])
        if not m:
            continue
        try:
            start = parse_ts(m.group(1))
            end = parse_ts(m.group(2))
        except ValueError:
            continue
        cue = _finish_cue(start, end, "\n".join(lines[idx + 1:]))
        if cue:
            cues.append(cue)
    return cues


def parse_vtt(text: str) -> list[Cue]:
    """解析 WebVTT（兼容 cue 标识行、设置参数、NOTE 块与头部元数据）。"""
    text = _normalize_newlines(text)
    lines = text.split("\n")
    i = 0
    if lines and lines[0].startswith("WEBVTT"):
        i = 1
    cues: list[Cue] = []
    n = len(lines)
    while i < n:
        if not lines[i].strip():
            i += 1
            continue
        # NOTE 块 / 头部元数据（Kind: / Language: 等）：整块跳过
        if lines[i].startswith("NOTE") or re.match(
            r"^(Kind|Language|Region|X-TIMESTAMP-MAP)\s*:", lines[i]
        ):
            while i < n and lines[i].strip():
                i += 1
            continue
        if "-->" not in lines[i]:
            # 可能是 cue 标识行，看向下一行
            i += 1
            if i >= n or "-->" not in lines[i]:
                continue
        m = _TIMING_LINE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        end_token = m.group(2).split()[0] if m.group(2).split() else ""
        try:
            start = parse_ts(m.group(1))
            end = parse_ts(end_token)
        except ValueError:
            i += 1
            continue
        i += 1
        text_lines: list[str] = []
        while i < n and lines[i].strip():
            text_lines.append(lines[i])
            i += 1
        cue = _finish_cue(start, end, _unesc_vtt("\n".join(text_lines)))
        if cue:
            cues.append(cue)
    return cues


_ASS_TAG_RE = re.compile(r"\{[^}]*\}")


def parse_ass(text: str) -> list[Cue]:
    """解析 ASS/SSA：按 ``[Events]`` 段 Format 行定位字段，取 Dialogue 的
    Start/End/Text；剔除 ASS 覆盖标签（``{\\...}``），``\\N`` 转换行、
    ``\\h`` 转空格。

    小写 ``\\n``（SSA 软换行，现代播放器多忽略）保留原样：若转换行，
    文本里的路径类字面量（如 ``C:\\new``）会被静默损坏。
    """
    text = text.lstrip("\ufeff")  # 容忍 BOM（自家导出即带 BOM）
    cues: list[Cue] = []
    fields: list[str] | None = None
    in_events = False
    for raw in _normalize_newlines(text).split("\n"):
        line = raw.strip()
        low = line.lower()
        if low.startswith("[") and low.endswith("]"):
            in_events = low == "[events]"
            continue
        if not in_events:
            continue
        if low.startswith("format:"):
            fields = [f.strip().lower() for f in line[len("format:"):].split(",")]
            continue
        if not low.startswith("dialogue:"):
            continue
        rest = line[len("dialogue:"):].lstrip()
        if fields:
            # 文本可含逗号：只切前 len(fields)-1 刀，最后一列尽收 Text
            parts = rest.split(",", len(fields) - 1)
            if len(parts) < len(fields):
                continue
            row = dict(zip(fields, parts))
            start_s, end_s, body = row.get("start", ""), row.get("end", ""), row.get("text", "")
        else:
            # 无 Format 行时按 v4 默认 10 字段
            parts = rest.split(",", 9)
            if len(parts) < 10:
                continue
            start_s, end_s, body = parts[1], parts[2], parts[9]
        try:
            start, end = parse_ts(start_s), parse_ts(end_s)
        except ValueError:
            continue
        body = _ASS_TAG_RE.sub("", body)
        body = body.replace(r"\N", "\n").replace(r"\h", " ")
        cue = _finish_cue(start, end, body)
        if cue:
            cues.append(cue)
    return cues


def parse_auto(text: str) -> list[Cue]:
    """按内容自动判别 SRT / VTT / ASS 并解析。"""
    head = text.lstrip("\ufeff \t\r\n")
    if head.startswith("WEBVTT"):
        return parse_vtt(text)
    # ASS 判别须为行首的 [Script Info] / [Events] 段标记；
    # 纯子串匹配会把正文里提到 "[events]" 的 SRT 误判为 ASS
    if head.lower().startswith("[script info]") or re.search(
        r"(?mi)^\[events\]\s*$", head[:4096]
    ):
        return parse_ass(text)
    return parse_srt(text)


def normalize_cues(cues: Iterable[Cue]) -> list[Cue]:
    """清洗外部传入的 cue 列表：类型强转、去空文本、按开始时间排序。"""
    out: list[Cue] = []
    for cue in cues:
        try:
            start = max(0.0, float(cue["start"]))
            end = float(cue["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(start) and math.isfinite(end)):
            continue
        text = _clean_text(cue.get("text", ""))
        if not text:
            continue
        if end <= start:
            end = start + 0.1
        out.append({"start": round3(start), "end": round3(end), "text": text})
    out.sort(key=lambda c: (c["start"], c["end"]))
    return out

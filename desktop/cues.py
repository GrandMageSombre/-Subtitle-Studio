"""字幕条目模型与编辑操作（纯 Python，无 Qt 依赖，便于单元测试）。

语义与浏览器版 ``static/app.js`` 保持一致——桌面界面是同一套交互规格的
原生实现，两条路径的编辑结果应当一致：

- 时间统一 3 位小数 half-up（复用 :func:`backend.srt.round3`）；
- 条目始终按 ``start`` 排序（重叠检测与播放定位依赖该不变量）；
- 结构性操作（增/删/合并/拆分/平移）自动压入撤销栈，连续编辑（输入框、
  拖动）由调用方在编辑开始时调一次 :meth:`CueList.snapshot`。
"""
from __future__ import annotations

import re
import math

from backend.srt import round3

#: 撤销栈上限（与前端 HISTORY_LIMIT 一致）
HISTORY_LIMIT = 30
#: 条目间最小间隔（秒）：拆分/对轴时避免零长条目
MIN_GAP = 0.05
#: 新增条目的默认时长（秒）
DEFAULT_DURATION = 2.0

#: 句末标点（与 backend/asr.py 的 SENT_PUNCT、static/app.js 的
#: splitTextAtPunct 正则保持一致，改动需三处同步，由一致性测试守卫）
SENT_PUNCT = "。！？；!?;…."
_SPLIT_RE = re.compile(rf"[^{SENT_PUNCT}]+[{SENT_PUNCT}]*")
_TS_RE = re.compile(r"^(?:(\d{1,3}):)?(\d{1,2}):(\d{1,2})(?:[.,](\d{1,3}))?$")


# ---------------------------------------------------------------------------
# 时间格式化 / 解析（与 static/app.js 的同名函数行为一致）
# ---------------------------------------------------------------------------

def fmt_time(seconds: float) -> str:
    """秒 -> ``HH:MM:SS.mmm``。"""
    if seconds is None or seconds != seconds or seconds < 0:  # NaN 或负值
        seconds = 0.0
    ms = int(round(float(seconds) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def fmt_time_short(seconds: float) -> str:
    """秒 -> ``H:MM:SS`` 或 ``MM:SS``（时间轴标尺用）。"""
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_duration(seconds: float) -> str:
    """秒 -> ``M分SS秒``（统计栏用）。"""
    seconds = max(0.0, float(seconds))
    m = int(seconds // 60)
    s = int(round(seconds % 60))
    return f"{m}分{s:02d}秒"


def parse_time(text: str) -> float | None:
    """解析 ``时:分:秒.毫秒``（小时可省略，分隔符可用逗号），非法返回 ``None``。"""
    m = _TS_RE.match(str(text).strip())
    if not m:
        return None
    hours = int(m.group(1)) if m.group(1) else 0
    minutes = int(m.group(2))
    seconds = int(m.group(3))
    millis = int((m.group(4) or "0").ljust(3, "0"))
    return hours * 3600 + minutes * 60 + seconds + millis / 1000.0


def split_text_at_punct(text: str) -> list[str]:
    """按句末标点把文本切成多段（与前端 ``splitTextAtPunct`` 同规则）。

    逐行匹配，保证跨行不粘连；匹配不到时整行原样保留。
    """
    parts: list[str] = []
    for line in str(text).split("\n"):
        found = [s.strip() for s in _SPLIT_RE.findall(line)]
        found = [s for s in found if s]
        if found:
            parts.extend(found)
        elif line.strip():
            parts.append(line.strip())
    return parts or [str(text)]


def clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


# ---------------------------------------------------------------------------
# 条目集合
# ---------------------------------------------------------------------------

class CueList:
    """字幕条目集合 + 撤销/重做栈。"""

    def __init__(self, cues: list[dict] | None = None,
                 history_limit: int = HISTORY_LIMIT) -> None:
        self.cues: list[dict] = []
        self.history_limit = history_limit
        self._undo: list[list[dict]] = []
        self._redo: list[list[dict]] = []
        self.replace_all(cues, snapshot=False)

    # -- 基本信息 ----------------------------------------------------------

    def __len__(self) -> int:
        return len(self.cues)

    def __iter__(self):
        return iter(self.cues)

    def __getitem__(self, index: int) -> dict:
        return self.cues[index]

    def index_of(self, cue: dict) -> int:
        """按对象身份找索引（排序后重新定位条目用）。"""
        for i, item in enumerate(self.cues):
            if item is cue:
                return i
        return -1

    def overlaps(self) -> list[bool]:
        """与上一条时间重叠的标记（前端红色高亮依据）。"""
        flags = [False] * len(self.cues)
        for i in range(1, len(self.cues)):
            if self.cues[i]["start"] < self.cues[i - 1]["end"] - 0.001:
                flags[i] = True
        return flags

    def stats(self) -> tuple[int, int, float]:
        """返回 (条数, 重叠数, 时间跨度)。"""
        n = len(self.cues)
        span = (self.cues[-1]["end"] - self.cues[0]["start"]) if n else 0.0
        return n, sum(self.overlaps()), span

    def duration_hint(self) -> float:
        """内容所需的最短时间轴时长（无媒体时的时间轴范围）。"""
        longest = self.cues[-1]["end"] + 0.3 if self.cues else 0.0
        return max(longest, 1.0)

    def sort(self) -> None:
        """维持「按开始时间有序」不变量。"""
        self.cues.sort(key=lambda c: (c["start"], c["end"]))

    def replace_all(self, cues: list[dict] | None, snapshot: bool = True) -> None:
        """整体替换（加载任务/导入字幕），并清洗为合法条目。"""
        if snapshot:
            self.snapshot()
        self._normalize(cues)

    # -- 撤销 / 重做 -------------------------------------------------------

    def snapshot(self) -> None:
        """当前状态压入撤销栈（清空重做栈）。"""
        self._undo.append([dict(c) for c in self.cues])
        if len(self._undo) > self.history_limit:
            self._undo.pop(0)
        self._redo.clear()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append([dict(c) for c in self.cues])
        self.cues = self._undo.pop()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append([dict(c) for c in self.cues])
        self.cues = self._redo.pop()
        return True

    def clear_history(self) -> None:
        self._undo.clear()
        self._redo.clear()

    # -- 编辑 --------------------------------------------------------------

    def set_text(self, index: int, text: str, snapshot: bool = False) -> None:
        if not 0 <= index < len(self.cues):
            return
        if snapshot:
            self.snapshot()
        self.cues[index]["text"] = str(text)

    def set_start(self, index: int, seconds: float, snapshot: bool = False) -> None:
        """设置开始时间；结束时间不早于开始 + 最小间隔。"""
        if not 0 <= index < len(self.cues):
            return
        if snapshot:
            self.snapshot()
        cue = self.cues[index]
        start = max(0.0, round3(seconds))
        cue["start"] = start
        if cue["end"] < start + 0.001:
            cue["end"] = round3(start + 0.001)

    def set_end(self, index: int, seconds: float, snapshot: bool = False) -> None:
        """设置结束时间；开始时间不晚于结束 - 最小间隔。"""
        if not 0 <= index < len(self.cues):
            return
        if snapshot:
            self.snapshot()
        cue = self.cues[index]
        end = round3(seconds)
        cue["end"] = end
        if end < cue["start"] + 0.001:
            cue["start"] = round3(max(0.0, end - 0.001))

    def set_start_at(self, index: int, seconds: float) -> None:
        """``[`` 快捷键：开始时间对齐到播放处（不越过结束时间）。"""
        if not 0 <= index < len(self.cues):
            return
        cue = self.cues[index]
        self.set_start(index, clamp(seconds, 0, max(0.0, cue["end"] - MIN_GAP)),
                       snapshot=True)

    def set_end_at(self, index: int, seconds: float) -> None:
        """``]`` 快捷键：结束时间对齐到播放处。"""
        if not 0 <= index < len(self.cues):
            return
        cue = self.cues[index]
        self.set_end(index, max(cue["start"] + MIN_GAP, seconds), snapshot=True)

    def add(self, at: float, duration: float = DEFAULT_DURATION) -> int:
        """在 ``at`` 处插入空条目，返回其索引（自动避开下一条）。"""
        self.snapshot()
        t = max(0.0, round3(at))
        insert_at = len(self.cues)
        for i, cue in enumerate(self.cues):
            if cue["end"] <= t + 0.001:
                insert_at = i + 1
            else:
                break
        end = t + duration
        nxt = self.cues[insert_at] if insert_at < len(self.cues) else None
        if nxt and nxt["start"] < end + 0.05:
            end = max(t + 0.2, nxt["start"] - 0.05)
        self.cues.insert(insert_at, {"start": t, "end": round3(end), "text": ""})
        return insert_at

    def remove(self, index: int, snapshot: bool = True) -> bool:
        """删除条目。"""
        if not 0 <= index < len(self.cues):
            return False
        if snapshot:
            self.snapshot()
        del self.cues[index]
        return True

    def merge_next(self, index: int) -> bool:
        """与下一条合并：时长取并集，文本换行拼接。"""
        if not 0 <= index < len(self.cues) - 1:
            return False
        self.snapshot()
        current, nxt = self.cues[index], self.cues[index + 1]
        current["end"] = max(current["end"], nxt["end"])
        merged = f"{current['text']}\n{nxt['text']}".lstrip("\n")
        current["text"] = merged
        del self.cues[index + 1]
        return True

    def split_at(self, index: int, seconds: float) -> bool:
        """在 ``seconds`` 处拆分：文本按时间比例切分。"""
        if not 0 <= index < len(self.cues):
            return False
        cue = self.cues[index]
        if not cue["start"] + MIN_GAP < seconds < cue["end"] - MIN_GAP:
            return False
        self.snapshot()
        frac = (seconds - cue["start"]) / max(1e-6, cue["end"] - cue["start"])
        length = len(cue["text"])
        cut = int(clamp(round(length * frac), 1, max(1, length - 1)))
        left = {"start": cue["start"], "end": round3(seconds),
                "text": cue["text"][:cut]}
        right = {"start": round3(seconds), "end": cue["end"],
                 "text": cue["text"][cut:]}
        self.cues[index:index + 1] = [left, right]
        return True

    def split_all_by_punct(self) -> int:
        """按句末标点拆分所有长条目，返回被拆分的条目数。"""
        self.snapshot()
        out: list[dict] = []
        changed = 0
        for cue in self.cues:
            parts = split_text_at_punct(cue["text"])
            if len(parts) <= 1:
                out.append(dict(cue))
                continue
            changed += 1
            total = sum(len(p) for p in parts) or 1
            dur = cue["end"] - cue["start"]
            cursor = cue["start"]
            for k, part in enumerate(parts):
                is_last = k == len(parts) - 1
                start = clamp(cursor, cue["start"],
                              max(cue["start"], cue["end"] - 0.1))
                end = (cue["end"] if is_last
                       else clamp(cursor + max(0.2, dur * len(part) / total),
                                  start + 0.1, cue["end"]))
                out.append({"start": round3(start), "end": round3(end),
                            "text": part})
                cursor = end
        if not changed:
            self._undo.pop()      # 没有实际改动，不留空撤销步
            return 0
        self.cues = out
        return changed

    def shift(self, offset: float, from_index: int = 0) -> bool:
        """整体平移（负数提前）；保持条目时长，负值不越过 0。"""
        if not self.cues or not offset:
            return False
        self.snapshot()
        self._apply_shift(offset, from_index)
        return True

    def move_to(self, index: int, start: float, end: float) -> None:
        """把条目移到 [start, end]（调用方按拖动起点计算，避免误差累积）。"""
        if not 0 <= index < len(self.cues):
            return
        cue = self.cues[index]
        cue["start"] = round3(max(0.0, start))
        cue["end"] = round3(max(cue["start"] + 0.001, end))

    def resize_start(self, index: int, seconds: float) -> None:
        """拖左边缘改开始时间（不越过结束时间）。"""
        if not 0 <= index < len(self.cues):
            return
        cue = self.cues[index]
        cue["start"] = round3(clamp(seconds, 0, max(0.0, cue["end"] - MIN_GAP)))

    def resize_end(self, index: int, seconds: float) -> None:
        """拖右边缘改结束时间。"""
        if not 0 <= index < len(self.cues):
            return
        cue = self.cues[index]
        cue["end"] = round3(max(cue["start"] + MIN_GAP, seconds))

    # -- 内部 --------------------------------------------------------------

    def _apply_shift(self, offset: float, from_index: int) -> None:
        for cue in self.cues[max(0, from_index):]:
            cue["start"] = round3(max(0.0, cue["start"] + offset))
            cue["end"] = round3(max(0.0, cue["end"] + offset))
            if cue["end"] <= cue["start"]:
                cue["end"] = round3(cue["start"] + 0.1)
        self.sort()

    def _normalize(self, cues: list[dict] | None = None) -> None:
        """清洗外部数据：类型强转、跳过非法项、负时长补 0.1 秒，并按开始时间排序。

        与 :func:`backend.srt.normalize_cues` 规则一致，但**保留空文本条目**
        （用户新建的字幕条本来就是空的，等待输入）。
        """
        cleaned: list[dict] = []
        for cue in (self.cues if cues is None else cues):
            try:
                start = max(0.0, float(cue["start"]))
                end = float(cue["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (math.isfinite(start) and math.isfinite(end)):
                continue
            text = str(cue.get("text", "") or "")
            if end <= start:
                end = start + 0.1
            cleaned.append({"start": round3(start), "end": round3(end),
                            "text": text})
        cleaned.sort(key=lambda c: (c["start"], c["end"]))
        self.cues = cleaned

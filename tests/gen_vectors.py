"""测试向量生成器：用随机种子生成可复现的字幕数据。

不要在测试里手写向量——一律由本模块按种子生成，保证可复现、覆盖面广。
"""
from __future__ import annotations

import random

ZH_CHARS = "你好世界字幕识别编辑测试视频音频时间轴播放暂停快进剪切合并拆分导出导入模型语言中文英语"
EN_WORDS = [
    "hello", "world", "subtitle", "editor", "whisper", "model", "timeline",
    "play", "pause", "merge", "split", "export", "import", "video", "audio",
]
PUNCT = "。！？；!?;…"


def gen_cues(n: int, seed: int, *, multiline: bool = True, punct: bool = True) -> list[dict]:
    """生成 n 条随机字幕条目（时间递增、不重叠、文本中英混合）。"""
    rng = random.Random(seed)
    cues = []
    t = rng.uniform(0, 10)
    for _ in range(n):
        dur = rng.uniform(0.8, 9.0)
        lines = []
        for _ in range(rng.randint(1, 3) if multiline else 1):
            if rng.random() < 0.5:
                text = "".join(rng.choice(ZH_CHARS) for _ in range(rng.randint(3, 28)))
            else:
                text = " ".join(rng.choice(EN_WORDS) for _ in range(rng.randint(2, 8)))
            if punct and rng.random() < 0.5:
                text += rng.choice(PUNCT)
            lines.append(text)
        cues.append({
            "start": round(t, 3),
            "end": round(t + dur, 3),
            "text": "\n".join(lines),
        })
        t += dur + rng.uniform(0, 0.4)
    return cues


def gen_overlapping_cues(n: int, seed: int) -> list[dict]:
    """生成允许时间重叠的随机字幕（用于清洗/健壮性测试）。"""
    rng = random.Random(seed)
    cues = []
    t = 0.0
    for _ in range(n):
        start = max(0.0, t + rng.uniform(-1.5, 1.0))
        end = start + rng.uniform(0.2, 5.0)
        cues.append({
            "start": round(start, 3),
            "end": round(end, 3),
            "text": "".join(rng.choice(ZH_CHARS) for _ in range(rng.randint(1, 20))),
        })
        t = start + rng.uniform(0, 2.0)
    return cues

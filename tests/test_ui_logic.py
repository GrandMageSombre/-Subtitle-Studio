"""前端时间轴交互回归测试（调用 Node + DOM 桩，无需浏览器）。

覆盖本轮修复的两类用户反馈：
1. 点击时间轴色块应跳转到点击处，且不得移动字幕；
2. 只有拖动超过阈值才移动/改时长，且时间不得出现 NaN。
另覆盖右键不触发拖动、字幕字号/位置滑块的即时预览与持久化。
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

NODE_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "ui_logic_test.cjs")
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="未安装 Node.js，跳过前端逻辑测试")
def test_timeline_interaction_logic():
    result = subprocess.run(
        [NODE, NODE_SCRIPT], capture_output=True, text=True, timeout=180
    )
    assert result.returncode == 0, (
        f"前端交互逻辑测试失败：\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "UI-LOGIC-OK" in result.stdout, result.stdout
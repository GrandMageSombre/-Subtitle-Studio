"""桌面外壳：把字幕工坊做成原生窗口应用，不依赖浏览器与浏览器内核。

模块划分：

- :mod:`desktop.logging_setup` —— 日志落盘（打包为无控制台的窗口程序后
  ``sys.stdout`` / ``sys.stderr`` 为 ``None``，``print`` 会直接抛异常）
- :mod:`desktop.cues` —— 字幕条目模型与编辑操作（纯 Python，可单测）
- :mod:`desktop.bridge` —— 与 ``backend`` 的桥接（任务、识别线程、导入导出）
- :mod:`desktop.player` —— QtMultimedia 原生播放器 + 画面字幕叠加
- :mod:`desktop.timeline` —— 自绘时间轴（缩放 / 拖动 / 跳转）
- :mod:`desktop.cue_table` —— 字幕列表编辑
- :mod:`desktop.dialogs` —— 识别 / 平移 / 帮助弹窗与浮动提示
- :mod:`desktop.main_window` —— 主窗口装配与交互编排
- :mod:`desktop.app` —— QApplication 入口

服务模式（``SUBTITLE_STUDIO_NO_GUI=1``）不使用本包，也不加载 PySide6。
"""
from __future__ import annotations

__all__ = ["run_desktop"]


def __getattr__(name: str):
    """延迟导入：导入本包不触发 PySide6 加载（服务模式/无 GUI 环境友好）。"""
    if name == "run_desktop":
        from .app import run_desktop

        return run_desktop
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""桌面应用入口：QApplication + 主窗口 + 未捕获异常兜底。

桌面模式不启动 HTTP 服务：识别、导入、导出都直接调用后端 Python API
（:mod:`desktop.bridge`），因此没有端口、没有浏览器、也没有上传环节。
"""
from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QCoreApplication, Qt, qInstallMessageHandler
from PySide6.QtWidgets import QApplication, QMessageBox

from . import theme
from .bridge import TaskBridge
from .main_window import MainWindow

logger = logging.getLogger("subtitle-studio.desktop")

#: 组织 / 应用名：决定 QSettings 与窗口元数据的位置
ORG_NAME = "SubtitleStudio"
APP_NAME = "Subtitle Studio"
APP_DISPLAY_NAME = "字幕工坊 Subtitle Studio"


def _install_qt_message_handler() -> None:
    """Qt 内部告警统一落到日志（DEBUG 级，避免刷屏）。"""

    def handler(mode, _context, message: str) -> None:  # noqa: ANN001
        logger.debug("[Qt] %s", message)

    qInstallMessageHandler(handler)


def _install_excepthook(window: MainWindow) -> None:
    """未捕获异常写日志并弹窗提示（打包为窗口程序后没有控制台）。"""

    def hook(exc_type, exc, tb) -> None:  # noqa: ANN001
        if issubclass(exc_type, KeyboardInterrupt):
            return
        logger.error("未捕获异常", exc_info=(exc_type, exc, tb))
        try:
            QMessageBox.critical(
                window, "出错了",
                f"{exc_type.__name__}: {exc}\n\n"
                "详细信息已写入 data/logs/subtitle-studio.log",
            )
        except Exception:  # noqa: BLE001  弹窗失败不应再抛
            pass

    sys.excepthook = hook


def run_desktop() -> int:
    """创建桌面应用并阻塞到窗口关闭，返回进程退出码。"""
    QCoreApplication.setOrganizationName(ORG_NAME)
    QCoreApplication.setApplicationName(APP_NAME)
    _install_qt_message_handler()

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationDisplayName(APP_DISPLAY_NAME)
    app.setStyleSheet(theme.build_stylesheet())

    bridge = TaskBridge()
    window = MainWindow(bridge)
    _install_excepthook(window)
    window.show()
    logger.info("桌面窗口已打开")
    return app.exec()

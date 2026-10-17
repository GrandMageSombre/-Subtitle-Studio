"""桌面界面测试的共用夹具。

``QT_QPA_PLATFORM=offscreen`` 必须在导入 PySide6 之前设置，因此放在本文件
顶部：pytest 会先加载 conftest，测试再导入 Qt 时已经是无界面平台，
既能在 CI/无桌面环境运行，也不会真的弹出窗口。
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    """进程内唯一的 QApplication（offscreen）。"""
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    # 测试用的独立标识：QSettings 落到独立键，不污染真实用户配置
    QCoreApplication.setOrganizationName("SubtitleStudioTest")
    QCoreApplication.setApplicationName("Subtitle Studio Test")
    yield app


@pytest.fixture
def bridge(tmp_path, monkeypatch, qapp):
    """隔离到临时目录、且设置落在 ini 文件的 TaskBridge。"""
    from PySide6.QtCore import QSettings

    from backend import jobs

    monkeypatch.setattr(jobs, "JOBS_DIR", str(tmp_path / "jobs"))
    os.makedirs(jobs.JOBS_DIR, exist_ok=True)

    from desktop.bridge import TaskBridge

    instance = TaskBridge(jobs.JobManager())
    instance._settings = QSettings(str(tmp_path / "settings.ini"),
                                   QSettings.Format.IniFormat)
    return instance

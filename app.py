"""字幕工坊 Subtitle Studio 启动入口。

默认 **桌面模式**：直接打开原生窗口（PySide6 + QtMultimedia 播放），
不启动 HTTP 服务、不打开浏览器。

**服务模式**（调试 / 自动化 / 习惯浏览器的人）：``SUBTITLE_STUDIO_NO_GUI=1``
或 ``python app.py --server-only``，只监听本地端口，用浏览器访问
（接口文档 ``/api/docs``；前端 ``static/`` 保留给该模式）。

环境变量：

- ``PORT``                          服务模式端口，默认 8000（被占用自动顺延）
- ``SUBTITLE_STUDIO_DATA``          数据目录（任务与字幕），默认 exe/源码同级 ``data/``
- ``SUBTITLE_STUDIO_NO_GUI=1``      不打开桌面窗口，只提供 HTTP 服务
                                    （兼容旧变量 ``SUBTITLE_STUDIO_NO_BROWSER=1``）
- ``MAX_UPLOAD_MB``                 识别媒体体积上限（默认 10240MB）

日志写在 ``<数据目录>/logs/subtitle-studio.log``：打包为窗口程序后没有
控制台，这里同时接管 ``sys.stdout`` / ``sys.stderr``，第三方库输出同样落盘。
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from backend.jobs import DATA_DIR  # noqa: E402
from desktop.logging_setup import setup_logging  # noqa: E402

logger = logging.getLogger("subtitle-studio")

#: 不打开桌面窗口（只提供 HTTP 服务）的开关
SERVER_ONLY_ENV = "SUBTITLE_STUDIO_NO_GUI"
#: 旧变量：v0.3.x 用它「禁止自动打开浏览器」，语义等价于现在的服务模式
LEGACY_SERVER_ONLY_ENV = "SUBTITLE_STUDIO_NO_BROWSER"
DEFAULT_PORT = 8000
PORT_ATTEMPTS = 10
#: 服务线程启动后等待其存活的探测时间（秒）：已退出即视为启动失败
STARTUP_PROBE_S = 0.3
#: 停止服务时等待线程优雅退出的上限（秒）
SHUTDOWN_TIMEOUT_S = 5.0


def _resolve_port(preferred: int, attempts: int = PORT_ATTEMPTS) -> tuple[int, bool]:
    """返回 (可用端口, 是否发生顺延)。端口被占用时自动 +1，避免双击两次直接崩。"""
    for offset in range(attempts):
        candidate = preferred + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", candidate))
                return candidate, offset > 0
            except OSError:
                continue
    return preferred, False


def _read_port() -> int:
    """读取并校验 ``PORT`` 环境变量，非法值退回默认端口。"""
    raw = os.environ.get("PORT", str(DEFAULT_PORT))
    try:
        port = int(raw)
    except ValueError:
        logger.warning("PORT=%s 不是合法端口，改用 %s", raw, DEFAULT_PORT)
        return DEFAULT_PORT
    if not 1 <= port <= 65535:
        logger.warning("PORT=%s 超出 1-65535，改用 %s", raw, DEFAULT_PORT)
        return DEFAULT_PORT
    return port


def _server_only_requested(args: list[str]) -> bool:
    """是否只提供 HTTP 服务（不开桌面窗口）。"""
    if "--server-only" in args:
        return True
    return any(os.environ.get(name) == "1"
               for name in (SERVER_ONLY_ENV, LEGACY_SERVER_ONLY_ENV))


def _log_banner(mode: str, url: str = "", shifted_from: int | None = None) -> None:
    frozen = getattr(sys, "frozen", False)
    logger.info("=" * 56)
    logger.info("  字幕工坊 Subtitle Studio%s", "（单机版）" if frozen else "")
    logger.info("  运行模式: %s", mode)
    if url:
        logger.info("  访问地址: %s", url)
    if shifted_from is not None:
        logger.info("  端口 %s 已被占用，自动改用 %s", shifted_from, url.rsplit(":", 1)[-1])
    logger.info("  数据目录: %s", DATA_DIR)
    logger.info("=" * 56)


def _show_startup_error(message: str) -> None:
    """启动失败时尽量弹窗告知（打包为窗口程序时没有控制台）。"""
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication([])
        QMessageBox.critical(None, "字幕工坊启动失败",
                             f"{message}\n\n详细信息见 data/logs/subtitle-studio.log")
        del app
    except Exception:  # noqa: BLE001  弹窗失败时至少已写日志
        pass


def _run_desktop() -> int:
    """桌面模式：打开原生窗口（不启动 HTTP 服务）。"""
    try:
        from desktop.app import run_desktop
    except (ImportError, OSError) as exc:
        logger.warning("桌面界面不可用（%s），改为启动本地服务用浏览器访问", exc)
        return _run_server(open_browser=True)
    try:
        return run_desktop()
    except Exception as exc:  # noqa: BLE001
        logger.exception("桌面应用异常退出：%s", exc)
        _show_startup_error(f"{type(exc).__name__}: {exc}")
        return 1


def _run_server(open_browser: bool = False) -> int:
    """服务模式：只启动 uvicorn（浏览器访问 / 自动化测试用）。"""
    from backend.main import app as fastapi_app

    preferred_port = _read_port()
    port, shifted = _resolve_port(preferred_port)
    url = f"http://127.0.0.1:{port}"
    _log_banner("仅服务（浏览器访问）", url, preferred_port if shifted else None)

    import uvicorn

    # log_config=None：日志统一走 root logger（窗口程序下也能写进文件）
    config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=port,
                            log_level="info", log_config=None)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="subtitle-studio-server",
                              daemon=True)
    thread.start()
    thread.join(STARTUP_PROBE_S)
    if not thread.is_alive():
        # 绑定失败等致命错误已在服务线程内记录
        logger.error("本地服务启动失败，详见日志文件")
        return 1

    if open_browser:
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("打开浏览器失败：%s", exc)

    logger.info("服务已启动，按 Ctrl+C 停止")
    try:
        while thread.is_alive():
            thread.join(0.5)
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在停止服务…")
    finally:
        server.should_exit = True
        thread.join(SHUTDOWN_TIMEOUT_S)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    setup_logging(DATA_DIR)      # setup_logging 内部已记录日志文件路径

    if _server_only_requested(args):
        # 打包版（窗口程序）没有控制台可看地址，自动开浏览器
        open_browser = (getattr(sys, "frozen", False)
                        or getattr(sys, "stderr", None) is None)
        return _run_server(open_browser=open_browser)
    _log_banner("桌面窗口")
    return _run_desktop()


if __name__ == "__main__":
    raise SystemExit(main())

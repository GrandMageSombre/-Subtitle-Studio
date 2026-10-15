"""字幕工坊 Subtitle Studio 启动入口。

支持两种运行方式：

- 开发模式：``python app.py``
- 打包模式（PyInstaller）：直接双击 ``字幕工坊.exe``，自动打开浏览器

环境变量：

- ``PORT``                       服务端口，默认 8000（被占用时自动顺延）
- ``SUBTITLE_STUDIO_DATA``        数据目录（任务与字幕），默认 exe/源码同级 ``data/``
- ``SUBTITLE_STUDIO_NO_BROWSER=1`` 禁止自动打开浏览器（调试用）
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from backend.jobs import DATA_DIR  # noqa: E402
from backend.main import app  # noqa: E402


def _resolve_port(preferred: int, attempts: int = 10) -> tuple[int, bool]:
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


def _open_browser_when_ready(url: str, timeout: float = 60.0) -> None:
    """等服务真正监听后再打开浏览器。

    单文件版首次启动要解压上百 MB，若固定延时打开，用户会先看到一个
    「无法访问此网站」的错误页。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/api/config", timeout=1):
                webbrowser.open(url)
                return
        except Exception:  # noqa: BLE001  未就绪属正常情况，继续等
            time.sleep(0.3)
    print("  提示：服务似乎未就绪，请手动访问上面的地址")


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    frozen = getattr(sys, "frozen", False)
    preferred_port = int(os.environ.get("PORT", "8000"))
    port, shifted = _resolve_port(preferred_port)
    url = f"http://127.0.0.1:{port}"

    print("=" * 56)
    print("  字幕工坊 Subtitle Studio" + ("（单机版）" if frozen else ""))
    print(f"  浏览器访问: {url}")
    if shifted:
        print(f"  端口 {preferred_port} 已被占用，自动改用 {port}")
    print(f"  数据目录: {DATA_DIR}")
    if frozen:
        print("  识别模型首次使用时自动下载；关闭本窗口即停止服务")
    else:
        print("  按 Ctrl+C 停止服务")
    print("=" * 56)

    if os.environ.get("SUBTITLE_STUDIO_NO_BROWSER") != "1":
        threading.Thread(target=_open_browser_when_ready, args=(url,),
                         daemon=True).start()

    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
    except OSError as exc:
        print(f"[ERROR] 服务启动失败：{exc}")
        print(f"        请用 PORT=9000 {sys.executable if frozen else 'python app.py'} 换一个端口")
        raise SystemExit(1)
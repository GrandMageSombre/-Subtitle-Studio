"""字幕工坊 Subtitle Studio 启动入口。

用法::

    pip install -r requirements.txt
    python app.py

然后浏览器打开 http://127.0.0.1:8000
"""
from __future__ import annotations

import logging
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from backend.main import app  # noqa: E402

if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    port = int(os.environ.get("PORT", "8000"))
    print("=" * 56)
    print("  字幕工坊 Subtitle Studio")
    print(f"  浏览器访问: http://127.0.0.1:{port}")
    print("  按 Ctrl+C 停止服务")
    print("=" * 56)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")

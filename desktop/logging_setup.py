"""日志初始化：统一写入 ``<数据目录>/logs/subtitle-studio.log``。

为什么需要这个模块：

- 打包为窗口程序（``console=False``）后 ``sys.stdout`` / ``sys.stderr`` 是
  ``None``，任何 ``print`` 都会抛 ``AttributeError``；uvicorn 默认的日志
  handler 也会因写入 ``None`` 而失效。
- 因此这里把文件日志挂在 root logger 上，并在流缺失时用「按行转交 logging」
  的流对象顶替 ``sys.stdout`` / ``sys.stderr``，第三方库（uvicorn /
  huggingface_hub / tqdm 进度条）的输出同样能落到日志文件。
- 源码运行（有控制台）时保留 stderr 输出，实时观察日志。
"""
from __future__ import annotations

import io
import logging
import os
import re
import sys
import threading
from logging.handlers import RotatingFileHandler

#: 日志目录名（相对数据目录）与文件名
LOG_DIRNAME = "logs"
LOG_FILENAME = "subtitle-studio.log"
#: 单个日志文件上限与滚动份数（识别日志量大，避免无限增长）
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3
#: 超长单行（tqdm 进度条）截断长度
MAX_LINE_CHARS = 2000

#: 应用自身的 logger 前缀（沿用既有命名：subtitle-studio / subtitle-studio.jobs …）
LOGGER_NAME = "subtitle-studio"

#: 进度条用 \r 刷新，与 \n 一样都算一次换行
_LINE_SPLIT = re.compile(r"[\r\n]+")

#: 标记本模块挂上的 handler，便于重复初始化时替换而不是叠加
_HANDLER_FLAG = "_subtitle_studio_handler"


class _StreamToLogger(io.TextIOBase):
    """类文件对象：把写入的文本按行转交 logging。

    仅实现 print / tqdm / logging.StreamHandler 会用到的接口。``_busy``
    防止「写日志失败 → logging 回写 stderr → 又进本流」的递归。
    """

    def __init__(self, logger: logging.Logger, level: int = logging.INFO,
                 name: str = "stdout") -> None:
        self._logger = logger
        self._level = level
        self._name = name
        self._buffer = ""
        #: 跨线程排队写（识别线程与界面线程都会打印）
        self._lock = threading.Lock()
        #: 同线程重入检测：日志写失败时 logging 会回写 stderr，必须短路而非死锁
        self._local = threading.local()

    # -- 供 logging / print 使用 -------------------------------------------

    @property
    def encoding(self) -> str:  # 某些库会读 encoding
        return "utf-8"

    def isatty(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    def write(self, text) -> int:  # type: ignore[override]
        if not text:
            return 0
        text = str(text)
        if getattr(self._local, "busy", False):
            return len(text)          # 同线程重入：丢弃，避免递归死锁
        with self._lock:
            self._local.busy = True
            try:
                self._write_locked(text)
            finally:
                self._local.busy = False
        return len(text)

    def flush(self) -> None:
        if getattr(self._local, "busy", False):
            return
        with self._lock:
            if not self._buffer:
                return
            self._local.busy = True
            try:
                pending, self._buffer = self._buffer, ""
                self._emit(pending)
            finally:
                self._local.busy = False

    # -- 内部 --------------------------------------------------------------

    def _write_locked(self, text: str) -> None:
        self._buffer += text
        pieces = _LINE_SPLIT.split(self._buffer)
        self._buffer = pieces.pop()          # 末尾可能是不完整的半行
        for line in pieces:
            self._emit(line)
        if len(self._buffer) > MAX_LINE_CHARS:  # 无换行的超长输出
            self._emit(self._buffer)
            self._buffer = ""

    def _emit(self, line: str) -> None:
        line = line.strip()
        if not line:
            return
        self._logger.log(self._level, "[%s] %s", self._name,
                         line[:MAX_LINE_CHARS])


def setup_logging(data_dir: str, level: int = logging.INFO,
                  capture_streams: bool = True) -> str:
    """初始化日志，返回日志文件路径。

    幂等：重复调用会替换本模块此前挂上的 handler，不会叠加重复输出。
    ``data_dir`` 通常是 :data:`backend.jobs.DATA_DIR`。
    """
    log_dir = os.path.join(data_dir, LOG_DIRNAME)
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, LOG_FILENAME)

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_FLAG, False):
            root.removeHandler(handler)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s")

    file_handler = RotatingFileHandler(
        log_path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    setattr(file_handler, _HANDLER_FLAG, True)
    root.addHandler(file_handler)

    stderr = getattr(sys, "stderr", None)
    if stderr is not None:
        console_handler = logging.StreamHandler(stderr)
        console_handler.setFormatter(formatter)
        setattr(console_handler, _HANDLER_FLAG, True)
        root.addHandler(console_handler)

    if capture_streams:
        logger = logging.getLogger(LOGGER_NAME)
        # 窗口程序（console=False）下这两个流是 None：用日志流顶替，
        # 否则 python -m 的 print 与 uvicorn 的默认 handler 都会崩。
        if getattr(sys, "stdout", None) is None:
            sys.stdout = _StreamToLogger(logger, logging.INFO, "stdout")  # type: ignore[assignment]
        if getattr(sys, "stderr", None) is None:
            sys.stderr = _StreamToLogger(logger, logging.ERROR, "stderr")  # type: ignore[assignment]

    logging.getLogger(LOGGER_NAME).info("日志文件：%s", log_path)
    return log_path

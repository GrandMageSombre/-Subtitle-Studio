# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：字幕工坊 Subtitle Studio（单文件模式）。

构建：:

    pyinstaller subtitle_studio.spec --noconfirm --clean

产物：``dist/字幕工坊.exe``（约 100MB 单文件，双击即用；
首次识别时模型按需下载到用户缓存目录）。

如需目录版（免解压、启动更快）：把末尾 ``EXE`` 的 ``a.binaries, a.datas``
两个参数改为 ``[]``，并追加
``COLLECT(exe, a.binaries, a.datas, name="字幕工坊")``。
"""
from __future__ import annotations

import os

from PyInstaller.utils.hooks import collect_all, copy_metadata

#: 以 spec 文件所在目录为基准，避免在他处执行 pyinstaller 时漏打包 backend / static
SPEC_DIR = os.path.abspath(globals().get("SPECPATH") or os.path.dirname(os.path.abspath(__file__)))

#: True = 单文件（默认，分发方便）；False = 目录版（免解压、启动更快）
ONE_FILE = True


def _patch_winutils_inplace_rewrite() -> None:
    """沙箱兼容补丁。

    PyInstaller 的 ``set_exe_build_timestamp`` / ``update_exe_pe_checksum``
    用 ``open(exe, 'wb')`` 截断重写整个可执行文件；部分安全沙箱会拦截对
    .exe 的截断写（PermissionError）。这里改为 ``r+b`` 原地重写，
    语义等价（pefile 序列化结果长度不变， overlay 数据由 pefile 保留）。
    """
    try:
        from PyInstaller.utils.win32 import winutils
    except Exception:  # noqa: BLE001  非 Windows 平台无需处理
        return

    def _rewrite(exe_path: str, data: bytes) -> None:
        size_before = os.path.getsize(exe_path)
        with open(exe_path, "r+b") as fp:
            fp.write(data)
            if len(data) < size_before:
                fp.truncate()

    def set_exe_build_timestamp(exe_path, timestamp):  # noqa: ANN001
        import pefile

        with pefile.PE(exe_path, fast_load=True) as pe:
            pe.full_load()
            timestamp = int(timestamp)
            pe.FILE_HEADER.TimeDateStamp = timestamp
            for debug_entry in getattr(pe, "DIRECTORY_ENTRY_DEBUG", []):
                if debug_entry.struct.TimeDateStamp:
                    debug_entry.struct.TimeDateStamp = timestamp
            data = pe.write()
        _rewrite(exe_path, data)

    def update_exe_pe_checksum(exe_path):  # noqa: ANN001
        import pefile

        checksum = winutils.compute_exe_pe_checksum(exe_path)
        with pefile.PE(exe_path, fast_load=True) as pe:
            pe.OPTIONAL_HEADER.CheckSum = checksum
            data = pe.write()
        _rewrite(exe_path, data)

    winutils.set_exe_build_timestamp = set_exe_build_timestamp
    winutils.update_exe_pe_checksum = update_exe_pe_checksum


_patch_winutils_inplace_rewrite()

block_cipher = None

# 需要整体收集（二进制 + 数据 + 隐式导入）的包：
#   ctranslate2 / av / tokenizers —— C/C++ 扩展与 DLL
#   onnxruntime                   —— VAD 推理，PyInstaller 知名坑
#   faster_whisper                —— 自带 assets/silero_vad_v6.onnx
COLLECT_PACKAGES = [
    "ctranslate2",
    "av",
    "tokenizers",
    "onnxruntime",
    "faster_whisper",
    # huggingface_hub 使用 _LazyModule 惰性导入 requests/tqdm/filelock/fsspec，
    # 静态分析抓不到，必须整体收集，否则模型下载可能 ImportError
    "huggingface_hub",
]

# 复制 dist-info：huggingface_hub 等运行时会用 importlib.metadata 读版本，
# 缺元数据会抛 PackageNotFoundError
METADATA_PACKAGES = [
    "huggingface_hub",
    "tokenizers",
    "ctranslate2",
    "av",
    "onnxruntime",
    "fastapi",
    "starlette",
    "uvicorn",
    "anyio",
    "pydantic",
    "pydantic_core",
]

HIDDEN_IMPORTS = [
    # uvicorn 运行时段选择（Windows 无 uvloop，走 httptools / websockets）
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    # FastAPI / anyio / multipart 动态导入
    "anyio",
    "anyio._backends._asyncio",
    "multipart",
    "multipart.multipart",
    "python_multipart",
]

binaries: list = []
datas: list = [(os.path.join(SPEC_DIR, "static"), "static")]

for _pkg in COLLECT_PACKAGES:
    _bin, _data, _hidden = collect_all(_pkg)
    binaries.extend(_bin)
    datas.extend(_data)
    HIDDEN_IMPORTS.extend(_hidden)

for _pkg in METADATA_PACKAGES:
    try:
        datas.extend(copy_metadata(_pkg))
    except Exception:  # noqa: BLE001  包不存在时跳过
        pass

a = Analysis(
    [os.path.join(SPEC_DIR, "app.py")],
    pathex=[SPEC_DIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 与运行无关的重依赖 / 开发工具，减小体积
        "torch", "tensorflow", "transformers", "matplotlib", "pandas",
        "scipy", "PIL", "tkinter", "IPython", "jupyter", "notebook",
        "pytest", "edge_tts", "sphinx",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

_EXE_COMMON = dict(
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # 不开 UPX：避免误报与潜在损坏
    runtime_tmpdir=None,
    console=True,            # 保留控制台：显示识别日志与错误，便于定位
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

if ONE_FILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="字幕工坊",
        **_EXE_COMMON,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="字幕工坊",
        **_EXE_COMMON,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name="字幕工坊",
    )

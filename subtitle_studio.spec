# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：字幕工坊 Subtitle Studio（单文件窗口程序）。

构建：:

    build.bat    （或：pyinstaller subtitle_studio.spec --noconfirm --clean）

产物：``字幕工坊.exe``（项目根目录，单文件，双击直接打开原生窗口；
不再弹控制台——日志写入 ``<数据目录>/logs/subtitle-studio.log``；
build.bat 会自动把 dist 中间产物移到根目录，使 exe 与 start.bat 同级、
与源码运行共用同一个 ``data/`` 数据目录）。

界面用 PySide6（QWidget + QtMultimedia），**不包含浏览器内核**：
Qt WebEngine 被显式排除（内置 Chromium 约 150MB，且其 ffmpeg 构建不含
H.264/AAC，无法预览 mp4）。

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
    # opencc-python-reimplemented 自带简繁词典数据文件，需整体收集
    "opencc",
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
    # 桌面界面：app.py 里是函数内延迟导入，显式列出以免静态分析遗漏
    "desktop",
    "desktop.app",
    "desktop.bridge",
    "desktop.cue_table",
    "desktop.cues",
    "desktop.dialogs",
    "desktop.logging_setup",
    "desktop.main_window",
    "desktop.player",
    "desktop.theme",
    "desktop.timeline",
    # Qt 模块（QtMultimedia 的后端插件由 PyInstaller 的 PySide6 hook 收集）
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
]

#: PySide6 里与运行无关的大块模块：QtWebEngine 内置 Chromium（约 150MB）且
#: 其 ffmpeg 构建不含 H.264/AAC；QtQuick/QtQml/Qt3D/QtCharts/QtPdf/QtSql 等
#: 本应用均未使用，排除以控制体积。
QT_EXCLUDES = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickWidgets",
    "PySide6.QtQuickControls2",
    "PySide6.QtQml",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtSpatialAudio",
    "PySide6.QtTextToSpeech",
    "PySide6.QtHelp",
    "PySide6.QtDesigner",
    "PySide6.QtUiTools",
    "PySide6.QtNetworkAuth",
    "PySide6.QtDBus",
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
        # 桌面界面只用 PySide6 的 Core/Gui/Widgets/Multimedia；其余 Qt 模块
        # （含内置 Chromium 的 QtWebEngine）排除，详见 QT_EXCLUDES
        *QT_EXCLUDES,
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
    # 窗口程序：不显示控制台（更像桌面软件）；日志写入 data/logs/，
    # 启动与识别异常会以弹窗或界面提示呈现
    console=False,
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

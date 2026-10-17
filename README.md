# 🎬 字幕工坊 Subtitle Studio

**本地语音识别 + 原生桌面字幕编辑一体机。**

选择视频/音频 → 本地 Whisper 模型自动识别生成字幕 → 在原生窗口里边播边改、逐条对轴 → 导出 SRT / VTT / ASS / TXT / JSON。

- 🖥️ **原生桌面窗口**：PySide6（Qt Widgets + QtMultimedia）实现，**不打开浏览器、不内置浏览器内核**，双击即用
- 🔒 **完全本地**：识别在本机 CPU 完成，文件不上传云端；界面与后端同进程，连本地端口都不需要
- 🌐 **中文友好**：国内网络自动使用 hf-mirror 镜像下载模型
- ✂️ **边播边改**：原生播放器 + 自绘时间轴 + 字幕列表三联动，拖拽改时长、按标点拆分
- 💾 **自动保存**：编辑结果实时持久化，重启后自动恢复上次任务

---

## 一、快速开始

### 方式 A：打包版（推荐，无需安装 Python）

1. 获取 `字幕工坊.exe`（项目根目录，单文件，仓库 Release 或自行打包，见下文）
2. **双击 `字幕工坊.exe`** → 直接打开「字幕工坊」窗口（不再弹控制台、不再开浏览器）
3. 首次识别时自动下载所选模型（国内自动走 hf-mirror 镜像），之后离线可用

> - 关闭窗口即退出程序；运行日志写入 exe 同级 `data\logs\subtitle-studio.log`
> - 单文件版每次启动会解压到系统临时目录，首次启动约 2~5 秒（包内含 Qt 运行库）
> - 识别任务与字幕保存在 exe 同级 `data/` 目录（便携模式）。exe 默认与
>   `start.bat` 同在项目根目录，因此**双击 exe 与源码运行共用同一个
>   `data/`**，整个文件夹拷给别人行为一致，数据随文件夹一起带走；
>   若 exe 单独放在 `Program Files` 等不可写位置，会自动改用
>   `%LOCALAPPDATA%\SubtitleStudio\data`
>   （日志里会打印实际路径，也可用环境变量 `SUBTITLE_STUDIO_DATA` 指定）
> - 杀毒软件可能对 PyInstaller 单文件版误报，请添加信任
> - 想要「免解压、启动更快」的目录版：把 `subtitle_studio.spec` 顶部的
>   `ONE_FILE` 改为 `False` 重新构建即可
> - **服务模式**：需要浏览器界面/接口调试时，用
>   `字幕工坊.exe --server-only`（或 `SUBTITLE_STUDIO_NO_GUI=1`），
>   会启动本地 HTTP 服务并自动打开浏览器

### 方式 B：源码运行

#### 环境要求

- Python 3.10+（开发验证环境为 3.12）
- Windows / macOS / Linux 均可（Windows 上视频解码走 Media Foundation）
- 无需 ffmpeg（音频解码由内置 PyAV 完成）

#### 安装与启动

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动桌面应用
python app.py
```

Windows 用户也可直接双击 `start.bat`，效果同上：**打开原生窗口**，不开浏览器。

> 调试/自动化想用浏览器界面：`python app.py --server-only`
> （等价于 `set SUBTITLE_STUDIO_NO_GUI=1 && python app.py`），端口默认 8000、被占用自动顺延
> 指定数据目录：`set SUBTITLE_STUDIO_DATA=D:\SubtitleStudio\data && python app.py`

### 自行打包 exe

```bat
build.bat
```

或手动：

```bash
pip install pyinstaller
python -m PyInstaller subtitle_studio.spec --noconfirm --clean
```

产物为项目根目录 `字幕工坊.exe`（单文件窗口程序；脚本会自动把 dist 中间产物
移到根目录并清理，使 exe 与 `start.bat` 同级、共用同一个 `data/` 数据目录）。
打包配置见 `subtitle_studio.spec`：已收集 ctranslate2 / onnxruntime / av / tokenizers /
faster_whisper / PySide6(QtCore·Gui·Widgets·Multimedia) 的二进制与数据，
排除了 torch 等无关重依赖，并**排除内置 Chromium 的 QtWebEngine**（体积约 -150MB），
同时针对安全沙箱环境把 PyInstaller 末尾的「截断重写 exe」改为「原地重写」。

## 二、使用流程

### 1️⃣ 语音识别生成字幕

1. 点击右上角 **「🎙️ 语音识别」**（或把视频/音频文件直接拖入窗口）
2. 选择文件，设置 **模型 / 语言 / 任务 / 中文字形**
3. 点击 **开始识别**，弹窗与顶栏同时显示进度（可「后台运行」继续编辑）
4. 识别结果自动载入编辑器，视频同步加载可预览

### 2️⃣ 编辑与对轴

| 区域 | 操作 |
| --- | --- |
| 字幕列表 | 直接修改文本、开始/结束时间（格式 `时:分:秒.毫秒`，非法输入红框提示） |
| 时间轴 | **单击色块**跳转到点击处（播放中选点）；**按住拖动**（超过 5px）移动整条；拖色块边缘改时长；`Ctrl+滚轮` 缩放；双击复位 |
| 字幕样式 | 播放控制条的「字号」「位置」滑块实时调节**画面内字幕叠加层**，设置自动保存 |
| 播放器 | 点击 ▶ 播放 / 暂停，双击字幕行播放该段，空格键播放暂停，对照画面校对 |

工具栏功能：**添加 / 删除 / 合并下一条 / 播放处拆分 / 按标点拆分 / 整体平移**。

### 3️⃣ 导入与导出

- **导入字幕**：支持 SRT / VTT / ASS（自动判别编码，兼容 UTF-8 / GBK；ASS 剔除特效标签仅保留文本与时间轴）
- **导出**：SRT（通用）/ WebVTT / **ASS（必剪、剪映可直接导入）** / TXT 纯文本 / JSON 数据；
  导出时弹出系统「保存」对话框选择位置
- 没有媒体也能纯编辑字幕：导入字幕后点 **「🎞️ 本地视频」** 加载本地文件预览对轴（文件不复制、不上传）

## 三、快捷键

| 按键 | 功能 |
| --- | --- |
| `空格` | 播放 / 暂停 |
| `←` `→` | 快退 / 快进 2 秒（Shift 为 10 秒） |
| `Enter` | 播放选中字幕段 |
| `[` `]` | 选中条开始 / 结束时间对齐到播放处 |
| `Delete` | 删除选中条目 |
| `Ctrl+Z` / `Ctrl+Y` | 撤销 / 重做 |

> 在文本框内编辑时，快捷键会让位给文本编辑（含 `Ctrl+Z` 文本撤销）。

## 四、识别模型怎么选

| 模型 | 体积 | 速度 | 精度 | 适用场景 |
| --- | --- | --- | --- | --- |
| tiny | 75 MB | 极快 | 一般 | 快速预览、配置差的机器 |
| base | 145 MB | 快 | 尚可 | 短视频、清晰人声 |
| **small（默认）** | 460 MB | 中等 | 良好 | **大多数场景推荐** |
| medium | 1.5 GB | 较慢 | 较高 | 长视频、专业场景 |
| large-v3 | 3.1 GB | 慢 | 最高 | 追求极致精度 |

- 首次使用某个模型时自动下载；国内网络自动切换到 [hf-mirror](https://hf-mirror.com) 镜像
- 已下载模型缓存在 `~/.cache/huggingface`，换模型不用重复下载
- CPU 上 small 模型约为 1~3 倍实时速度（10 分钟视频约 10~30 分钟）

**语言**：默认自动检测，也可指定（中文对精度有小幅提升）。
**任务**：`识别为原语言字幕` 或 `翻译成英语`（Whisper 内置翻译）。
**中文字形**：Whisper 对中文的输出简繁不定，可在此强制 **简体中文** / **繁体中文**
（OpenCC 词汇级转换：軟體→软件、软件→軟體）；默认「自动」保持识别原样。

## 五、播放器兼容性说明

桌面版用 **QtMultimedia**（Windows 上走系统 Media Foundation 解码器）播放，
已实测可正常播放 **4K H.264/AAC 的 MP4**（含画面字幕叠加与时间轴联动）。

- 通常可直接预览：**MP4 / MOV / M4V / M4A / MP3 / WAV / AAC**（取决于系统解码器）
- **MKV / AVI / FLV / WebM** 等格式**识别不受影响**，但可能无法在窗口内预览，
  此时界面会提示「无法预览该媒体（识别与编辑不受影响）」，建议转成 MP4 后再对轴
- 需要画面内字幕叠加时，请保持「字幕」勾选（叠加层独立于视频窗口绘制，全屏播放同样显示）

## 六、项目结构

```
Subtitle Studio/
├── app.py                  # 启动入口：默认桌面窗口，--server-only 走服务模式
├── start.bat               # Windows 源码模式一键启动（打开桌面窗口）
├── build.bat               # Windows 一键打包 exe
├── subtitle_studio.spec    # PyInstaller 打包配置（窗口程序 / 排除 QtWebEngine）
├── requirements.txt        # 运行依赖（含 PySide6）
├── requirements-dev.txt    # 测试 / 打包依赖
├── backend/                # 后端（识别与任务，桌面与服务模式共用）
│   ├── __init__.py         #   包标识
│   ├── service.py          #   共用业务：导出序列化 / 导入解析 / 媒体落位与体积上限
│   ├── main.py             #   HTTP 路由（服务模式）：识别 / 导入导出 / 媒体流
│   ├── asr.py              #   faster-whisper 封装 + 按标点断句 + 中文字形
│   ├── srt.py              #   SRT/VTT/ASS/TXT/JSON 解析与序列化
│   └── jobs.py             #   任务管理（内存态 + 原子落盘）
├── desktop/                # 桌面界面（PySide6，无浏览器内核）
│   ├── __init__.py         #   包标识与延迟导入
│   ├── app.py              #   QApplication 入口 + 异常兜底
│   ├── logging_setup.py    #   日志落盘 + 接管缺失的 stdout/stderr
│   ├── theme.py            #   配色与全局 QSS
│   ├── cues.py             #   字幕模型与编辑操作（纯 Python，可单测）
│   ├── bridge.py           #   后端桥接：任务 / 后台识别线程 / 导入导出
│   ├── player.py           #   QtMultimedia 播放器 + 画面字幕叠加窗口
│   ├── timeline.py         #   自绘时间轴 + 滚动条
│   ├── cue_table.py        #   字幕列表编辑
│   ├── dialogs.py          #   识别 / 平移 / 帮助弹窗 + 浮动提示
│   └── main_window.py      #   主窗口装配、快捷键、拖放、进度与恢复
├── static/                 # 浏览器界面（仅服务模式使用，无构建步骤）
│   ├── index.html          #   页面结构
│   ├── style.css           #   浅色主题样式
│   └── app.js              #   编辑器 / 时间轴 / 播放器联动逻辑
├── tests/                  # 测试
│   ├── conftest.py         #   offscreen Qt 夹具 + 临时数据目录隔离
│   ├── gen_vectors.py      #   测试向量生成器（代码生成，可复现）
│   ├── test_formats.py     #   字幕格式解析 / 序列化
│   ├── test_asr.py         #   断句逻辑（合成词级时间戳）
│   ├── test_desktop_cues.py    # 桌面字幕模型（编辑语义 / 撤销 / 时间格式）
│   ├── test_desktop_bridge.py  # 桌面后端桥接（识别线程 / 导入导出 / 日志）
│   ├── test_desktop_ui.py      # 桌面控件（时间轴交互 / 列表 / 主窗口装配）
│   ├── test_consistency.py #   前后端一致性守卫（DOM id / 标点集 / 桌面标点集）
│   ├── test_e2e.py         #   端到端（真实服务 + TTS 语音）
│   ├── test_ui_logic.py    #   浏览器版前端交互逻辑（pytest 包装）
│   └── ui_logic_test.cjs   #   时间轴交互回归（Node + DOM 桩，浏览器版）
├── docs/                   # 项目文档
│   ├── 版本说明.md          #   版本变更记录
│   └── reviews/            #   代码 / 文档审查报告归档
├── 字幕工坊.exe            # 打包产物（build.bat 生成，已忽略）
├── data/                   # 运行时数据：媒体、字幕任务、日志（自动创建，已忽略）
└── LICENSE                 # MIT 许可证
```

**文档**：使用说明见本文件 · 版本历史见 [docs/版本说明.md](docs/版本说明.md) ·
每轮代码审查报告见 [docs/reviews/](docs/reviews/)。

## 七、开发与测试

```bash
pip install -r requirements-dev.txt

python -m pytest tests/ -q          # 全部测试（含桌面界面，offscreen 无窗口）
python -m pytest tests/ -q -k "not e2e"   # 跳过联网端到端
python -m pytest tests/test_e2e.py -v     # 端到端（需联网，首次下载模型）
node --check static/app.js          # 浏览器版前端语法检查
node tests/ui_logic_test.cjs        # 浏览器版时间轴交互回归（无需浏览器）
```

- 桌面界面测试用 `QT_QPA_PLATFORM=offscreen` 运行（`tests/conftest.py` 设置），
  不会真的弹窗；任务数据与 `QSettings` 都隔离到 pytest 的临时目录
- 端到端测试通过 HTTP 接口驱动服务模式（`SUBTITLE_STUDIO_NO_GUI=1`），
  用 edge-tts 合成中文语音，自动完成：
  上传识别 → 轮询进度 → 编辑保存 → 导出 SRT/VTT → 再导入校验 → Range 视频流 → 脏数据导入

## 八、数据与隐私

- 上传的媒体与字幕保存在 `<数据目录>/jobs/<任务ID>/`，日志在 `<数据目录>/logs/`
  （默认 `<程序或源码目录>/data/`），可随时删除整个 `data` 目录清理
- 桌面模式**不监听任何端口**；服务模式仅监听 `127.0.0.1`（可用 `PORT` 调整）
- 任务列表 API（仅服务模式）：`GET /api/jobs`；接口文档：`http://127.0.0.1:8000/api/docs`

## 九、常见问题

**Q: 双击 exe 没有反应 / 窗口没出来？**
A: 首次启动要解压运行库（约 2~5 秒），请稍等；仍无窗口时打开
`data\logs\subtitle-studio.log` 查看原因，启动失败会弹窗提示。

**Q: 识别失败/中断了怎么办？**
A: 失败原因会通过界面提示与日志给出（网络、模型下载、解码器等）。重新打开「语音识别」再传一次即可；识别过程中关闭弹窗会在后台继续，顶栏进度条可见，完成后自动加载。

**Q: 视频无法预览（提示"无法预览该媒体"）？**
A: 该格式系统解码器不支持（常见于 MKV / AVI / 部分 WebM）。识别与字幕编辑不受影响，
建议用转成 MP4（H.264）后对轴，或直接导出字幕给剪辑软件使用。

**Q: huggingface.co 连不上？**
A: 应用启动时探测不通会自动切换到 hf-mirror.com；也可手动设置环境变量 `HF_ENDPOINT=https://hf-mirror.com`。

**Q: 识别出来是一条很长字幕？**
A: 点击工具栏 **「按标点拆分」**；或调整 `backend/asr.py` 中的 `MIN_CUE_DURATION / HARD_DURATION` 阈值。

**Q: 想用浏览器界面 / 让局域网访问？**
A: 用 `python app.py --server-only` 启动服务模式，然后修改 `app.py` 中
`_run_server` 里 `host="127.0.0.1"` 为 `0.0.0.0` 即可。

**Q: 上传大小有限制吗？**
A: 识别媒体默认上限 10GB（环境变量 `MAX_UPLOAD_MB` 可调），字幕文件上限 20MB。

## 十、路线图（后续版本）

- [ ] 说话人分离（多人对话区分）
- [ ] 字幕翻译（中英互译，接入本地/云端翻译；区别于已实现的 Whisper 内置「翻译成英语」）
- [ ] 视频压制硬字幕（FFmpeg 烧录）
- [ ] 波形图显示与音频抽取
- [ ] 项目文件保存/恢复（.ssproj）
- [ ] 桌面版界面美化（图标 / 深色主题）

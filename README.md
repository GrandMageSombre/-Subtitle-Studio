# 🎬 字幕工坊 Subtitle Studio

**本地语音识别 + 在线字幕编辑一体化工具。**

上传视频/音频 → 本地 Whisper 模型自动识别生成字幕 → 在网页里边播边改、逐条对轴 → 导出 SRT / VTT。

- 🔒 **完全本地**：识别在本机 CPU 完成，文件不上传云端
- 🌐 **中文友好**：国内网络自动使用 hf-mirror 镜像下载模型
- ✂️ **在线编辑**：播放器 + 时间轴 + 字幕列表三联动，支持拖拽改时长
- 💾 **自动保存**：编辑结果实时持久化，刷新/重启不丢失

---

## 一、快速开始

### 方式 A：打包版（推荐，无需安装 Python）

1. 获取 `dist/字幕工坊.exe`（约 100MB 单文件，仓库 Release 或自行打包，见下文）
2. **双击 `字幕工坊.exe`**，浏览器会自动打开 **http://127.0.0.1:8000**
3. 首次识别时自动下载所选模型（国内自动走 hf-mirror 镜像），之后离线可用

> - 黑色控制台窗口显示服务日志，**关闭它即停止服务**，请勿关闭
> - 单文件版每次启动会解压到系统临时目录（实测冷启动约 1~2 秒）
> - 识别任务与字幕保存在 exe 同级 `data/` 目录（便携模式）；若 exe 放在
>   `Program Files` 等不可写位置，会自动改用 `%LOCALAPPDATA%\SubtitleStudio\data`
>   （启动横幅会打印实际路径，也可用环境变量 `SUBTITLE_STUDIO_DATA` 指定）
> - 端口被占用时自动顺延（8000 → 8013…），双击两次不会冲突
> - 杀毒软件可能对 PyInstaller 单文件版误报，请添加信任
> - 想要「免解压、启动更快」的目录版：把 `subtitle_studio.spec` 顶部的
>   `ONE_FILE` 改为 `False` 重新构建即可（产物为 `dist/字幕工坊/字幕工坊.exe`）

### 方式 B：源码运行

#### 环境要求

- Python 3.10+（开发验证环境为 3.13）
- Windows / macOS / Linux 均可
- 无需 ffmpeg（音频解码由内置 PyAV 完成）

#### 安装与启动

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动服务
python app.py
```

Windows 用户也可直接双击 `start.bat`。

启动后浏览器打开 **http://127.0.0.1:8000** 即可使用。

> 修改端口：`set PORT=9000 && python app.py`（Linux/macOS 用 `PORT=9000 python app.py`）
> 端口被占用会自动顺延；禁止自动开浏览器：`set SUBTITLE_STUDIO_NO_BROWSER=1 && python app.py`
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

产物为 `dist/字幕工坊.exe`（单文件，约 100MB）。
打包配置见 `subtitle_studio.spec`：已收集 ctranslate2 / onnxruntime / av / tokenizers /
faster_whisper 的二进制与 VAD 模型数据，排除了 torch 等无关重依赖，
并针对安全沙箱环境把 PyInstaller 末尾的「截断重写 exe」改为「原地重写」。

## 二、使用流程

### 1️⃣ 语音识别生成字幕

1. 点击右上角 **「🎙️ 语音识别」**（或把视频/音频文件直接拖入窗口）
2. 选择文件，设置 **模型 / 语言 / 任务**
3. 点击 **开始识别**，等待进度条完成
4. 识别结果自动载入编辑器，视频同步加载可预览

### 2️⃣ 在线编辑

| 区域 | 操作 |
| --- | --- |
| 字幕列表 | 直接修改文本、开始/结束时间（格式 `时:分:秒.毫秒`） |
| 时间轴 | **单击色块**跳转到点击处（播放中选点）；**按住拖动**（超过 5px）移动整条；拖色块边缘改时长；`Ctrl+滚轮` 缩放；双击复位 |
| 字幕样式 | 播放控制条的「字号」「位置」滑块实时调节画面叠加层，设置自动保存 |
| 播放器 | 点击 ▶ 按钮或双击字幕行播放该段，对照画面校对 |

工具栏功能：**添加 / 删除 / 合并下一条 / 播放处拆分 / 按标点拆分 / 整体平移**。

### 3️⃣ 导入与导出

- **导入字幕**：支持 SRT / VTT（自动判别编码，兼容 UTF-8 / GBK）
- **导出**：SRT（通用）/ WebVTT / TXT 纯文本 / JSON 数据
- 没有媒体也能纯编辑字幕：导入 SRT 后点 **「🎞️ 本地视频」** 加载本地文件预览对轴（文件仅在浏览器内打开，不上传）

## 三、快捷键

| 按键 | 功能 |
| --- | --- |
| `空格` | 播放 / 暂停 |
| `←` `→` | 快退 / 快进 2 秒（Shift 为 10 秒） |
| `Enter` | 播放选中字幕段 |
| `[` `]` | 选中条开始 / 结束时间对齐到播放处 |
| `Delete` | 删除选中条目 |
| `Ctrl+Z` / `Ctrl+Y` | 撤销 / 重做 |

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

## 五、播放器兼容性说明

- 浏览器可直接播放：**MP4 / WebM / MOV(H.264) / M4A / MP3 / WAV**
- MKV / AVI / FLV 等格式**识别不受影响**，但浏览器无法预览，建议转成 MP4 后再对轴
- 也可用「本地视频」按钮在浏览器里直接打开任意本地文件预览（不走服务器）

## 六、项目结构

```
subtitle-studio/
├── app.py                  # 启动入口（自动打开浏览器）
├── start.bat               # Windows 源码模式一键启动
├── build.bat               # Windows 一键打包 exe
├── subtitle_studio.spec    # PyInstaller 打包配置
├── requirements.txt        # 运行依赖
├── requirements-dev.txt    # 测试依赖
├── backend/
│   ├── main.py             # FastAPI 路由（识别/导入导出/媒体流）
│   ├── asr.py              # faster-whisper 封装 + 按标点断句
│   ├── srt.py              # SRT/VTT/TXT/JSON 解析与序列化
│   └── jobs.py             # 任务管理（内存态 + 磁盘持久化）
├── static/
│   ├── index.html          # 页面结构
│   ├── style.css           # 浅色主题样式
│   └── app.js              # 编辑器 / 时间轴 / 播放器联动逻辑
└── tests/
    ├── gen_vectors.py      # 测试向量生成器（代码生成，可复现）
    ├── test_formats.py     # 字幕格式解析/序列化测试
    ├── test_asr.py         # 断句逻辑测试（合成词级时间戳）
    └── test_e2e.py         # 端到端测试（真实服务 + TTS 音频）
```

## 七、开发与测试

```bash
pip install -r requirements-dev.txt

python -m pytest tests/ -q            # 单元测试（约 91 个，秒级）
python -m pytest tests/test_e2e.py -v # 端到端（需联网，首次下载模型）
node tests/ui_logic_test.cjs         # 时间轴交互回归（无需浏览器）
```

端到端测试会用 edge-tts 合成中文语音，自动完成：
上传识别 → 轮询进度 → 编辑保存 → 导出 SRT/VTT → 再导入校验 → Range 视频流 → 脏数据导入。

`ui_logic_test.cjs` 用轻量 DOM 桩直接驱动 `static/app.js`，验证
「点击色块跳转而不是移动字幕」「拖动阈值与时间有效性」「右键无副作用」「滑块预览与持久化」。

## 八、数据与隐私

- 上传的媒体与字幕保存在 `subtitle-studio/data/jobs/<任务ID>/`，可随时删除整个 `data` 目录清理
- 任务列表 API：`GET /api/jobs`；删除任务：`DELETE /api/jobs/<id>`
- 接口文档：启动后访问 http://127.0.0.1:8000/api/docs

## 九、常见问题

**Q: 识别失败/中断了怎么办？**
A: 失败原因会通过右下角提示显示（网络、模型下载、解码器等）。重新打开「语音识别」弹窗再传一次即可；识别过程中关闭弹窗会在后台继续，顶栏进度条可见，完成后自动加载。

**Q: huggingface.co 连不上？**
A: 应用启动时探测不通会自动切换到 hf-mirror.com；也可手动设置环境变量 `HF_ENDPOINT=https://hf-mirror.com`。

**Q: 识别出来是一条很长字幕？**
A: 点击工具栏 **「按标点拆分」**；或调整 `backend/asr.py` 中的 `MIN_CUE_DURATION / HARD_DURATION` 阈值。

**Q: 想换端口 / 让局域网访问？**
A: 修改 `app.py` 中 `uvicorn.run(..., host="127.0.0.1")` 的 host 为 `0.0.0.0`。

**Q: 上传大小有限制吗？**
A: 识别上传默认上限 10GB（环境变量 `MAX_UPLOAD_MB` 可调），字幕文件上限 20MB。

## 十、路线图（后续版本）

- [ ] 说话人分离（多人对话区分）
- [ ] 字幕翻译（中英互译，接入本地/云端翻译）
- [ ] 视频压制硬字幕（FFmpeg 烧录）
- [ ] 波形图显示与音频抽取
- [ ] 项目文件保存/恢复（.ssproj）

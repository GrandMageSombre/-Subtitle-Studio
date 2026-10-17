"""桌面界面配色与样式表（对齐 ``static/style.css`` 的浅色主题）。

集中定义颜色常量与 QSS：控件只负责结构，视觉统一从这里取，
避免各控件各写一套颜色造成界面不协调。
"""
from __future__ import annotations

#: 主题色（与 static/style.css 的 :root 变量一一对应）
BG = "#f3f5f9"          # 窗口背景
PANEL = "#ffffff"       # 面板
SURFACE = "#eef1f7"     # 画布/时间轴底色
BORDER = "#e3e7ef"
BORDER_STRONG = "#d3d9e4"
TEXT = "#1f2329"
TEXT_2 = "#5a6474"
TEXT_3 = "#8a93a5"
PRIMARY = "#3b6bf5"
PRIMARY_DARK = "#2f57d6"
PRIMARY_SOFT = "#e8efff"
DANGER = "#e5484d"
WARNING = "#f5a623"
SUCCESS = "#2fa84f"
ROW_HOVER = "#f6f8fc"
ROW_SELECTED = "#e8efff"
ROW_PLAYING = "#fff6e3"
ROW_OVERLAP = "#fdeeee"

#: 时间轴色块配色（与前端 drawTimeline 一致）
CUE_FILL = "#8fb0f9"
CUE_SELECTED = "#f5a623"
CUE_ACTIVE = "#37b26a"
CUE_OVERLAP = "#f0a9a4"
CUE_OVERLAP_SELECTED = "#e8873a"
PLAYHEAD = "#e5484d"

#: 等宽字体（时间显示、时间轴标尺）
MONO_FAMILY = 'ui-monospace, "Cascadia Mono", Consolas, "Courier New", monospace'
UI_FAMILY = 'system-ui, "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif'


def build_stylesheet() -> str:
    """返回全局 QSS。"""
    return f"""
    QWidget {{
        background: {BG};
        color: {TEXT};
        font-family: {UI_FAMILY};
        font-size: 13px;
    }}
    QMainWindow, QDialog {{ background: {BG}; }}

    QLabel[role="title"]  {{ font-size: 15px; font-weight: 600; }}
    QLabel[role="muted"]  {{ color: {TEXT_2}; }}
    QLabel[role="hint"]   {{ color: {TEXT_3}; font-size: 12px; }}
    QLabel[role="mono"]   {{ font-family: {MONO_FAMILY}; }}
    QLabel[role="stats"]  {{ color: {TEXT_2}; font-size: 12px; }}
    QLabel[role="danger"] {{ color: {DANGER}; }}

    /* ---------- 顶栏 / 工具栏 ---------- */
    #TopBar, #EditorToolbar, #Transport {{
        background: {PANEL};
        border-bottom: 1px solid {BORDER};
    }}
    #EditorToolbar, #Transport {{ border-top: 1px solid {BORDER}; border-bottom: none; }}
    #BrandName {{ font-size: 15px; font-weight: 600; }}
    #BrandSub  {{ color: {TEXT_3}; font-size: 11px; }}

    /* ---------- 按钮 ---------- */
    QPushButton {{
        background: {PANEL};
        border: 1px solid {BORDER_STRONG};
        border-radius: 6px;
        padding: 5px 12px;
        color: {TEXT};
    }}
    QPushButton:hover    {{ background: {ROW_HOVER}; }}
    QPushButton:pressed  {{ background: {SURFACE}; }}
    QPushButton:disabled {{ color: {TEXT_3}; background: {SURFACE}; }}
    QPushButton[variant="primary"] {{
        background: {PRIMARY}; border-color: {PRIMARY}; color: #ffffff;
    }}
    QPushButton[variant="primary"]:hover   {{ background: {PRIMARY_DARK}; }}
    QPushButton[variant="primary"]:disabled {{ background: #a9bdf8; border-color: #a9bdf8; color: #f0f3ff; }}
    QPushButton[size="small"] {{ padding: 3px 9px; font-size: 12px; }}
    QPushButton[flat="true"]  {{ border: none; background: transparent; }}
    QPushButton[flat="true"]:hover {{ background: {ROW_HOVER}; }}

    /* ---------- 输入控件 ---------- */
    QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {{
        background: {PANEL};
        border: 1px solid {BORDER_STRONG};
        border-radius: 5px;
        padding: 3px 6px;
        selection-background-color: {PRIMARY};
        selection-color: #ffffff;
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
    QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {PRIMARY}; }}
    QLineEdit[invalid="true"] {{ border-color: {DANGER}; background: {ROW_OVERLAP}; }}
    QComboBox {{
        background: {PANEL};
        border: 1px solid {BORDER_STRONG};
        border-radius: 5px;
        padding: 3px 8px;
    }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox QAbstractItemView {{
        background: {PANEL};
        border: 1px solid {BORDER_STRONG};
        selection-background-color: {PRIMARY_SOFT};
        selection-color: {TEXT};
        outline: none;
    }}
    QCheckBox {{ background: transparent; }}

    /* ---------- 滑块 ---------- */
    QSlider::groove:horizontal {{
        height: 4px; background: {BORDER}; border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{ background: {PRIMARY}; border-radius: 2px; }}
    QSlider::handle:horizontal {{
        width: 12px; margin: -5px 0; border-radius: 6px;
        background: {PANEL}; border: 1px solid {PRIMARY};
    }}

    /* ---------- 进度条 ---------- */
    QProgressBar {{
        background: {BORDER};
        border: none;
        border-radius: 3px;
        height: 6px;
        text-align: center;
        color: {TEXT_2};
    }}
    QProgressBar::chunk {{ background: {PRIMARY}; border-radius: 3px; }}

    /* ---------- 滚动条 ---------- */
    QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: #c6cedd; border-radius: 5px; min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{ background: #aab5c9; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
    QScrollBar::handle:horizontal {{
        background: #c6cedd; border-radius: 5px; min-width: 24px;
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

    /* ---------- 面板 ---------- */
    #Panel, #CueTable, #CueTable > QWidget {{ background: {PANEL}; }}
    #CueRow {{
        background: {PANEL};
        border-bottom: 1px solid #eef1f7;
    }}
    #CueRow[state="overlap"]  {{ background: {ROW_OVERLAP}; }}
    #CueRow[state="selected"] {{ background: {ROW_SELECTED}; }}
    #CueRow[state="playing"]  {{ background: {ROW_PLAYING}; }}
    #CueHeader {{ background: {PANEL}; }}
    QHeaderView::section {{
        background: {PANEL};
        border: none;
        border-bottom: 1px solid {BORDER};
        padding: 5px 6px;
        color: {TEXT_2};
        font-weight: 600;
    }}
    QScrollArea {{ border: none; background: {PANEL}; }}

    /* ---------- 菜单 ---------- */
    QMenu {{
        background: {PANEL};
        border: 1px solid {BORDER_STRONG};
        border-radius: 6px;
        padding: 4px;
    }}
    QMenu::item {{ padding: 5px 18px; border-radius: 4px; }}
    QMenu::item:selected {{ background: {PRIMARY_SOFT}; }}

    /* ---------- 提示 / 状态 ---------- */
    #Toast {{
        background: {TEXT};
        color: #ffffff;
        border-radius: 6px;
        padding: 7px 14px;
    }}
    #Toast[level="success"] {{ background: {SUCCESS}; }}
    #Toast[level="error"]   {{ background: {DANGER}; }}
    #GlobalProgressBar {{
        background: {PANEL};
        border-bottom: 1px solid {BORDER};
    }}
    QToolTip {{
        background: {TEXT}; color: #ffffff;
        border: none; padding: 4px 8px;
    }}
    """

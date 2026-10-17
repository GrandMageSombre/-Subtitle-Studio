"""前后端一致性守卫测试。

防止一类回归 bug：前端引用了不存在的 DOM id（如 toastEl 未声明）、
后端与前端的句末标点集各自漂移。
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASR_PY = os.path.join(ROOT, "backend", "asr.py")
HTML = os.path.join(ROOT, "static", "index.html")
JS = os.path.join(ROOT, "static", "app.js")
DESKTOP_CUES = os.path.join(ROOT, "desktop", "cues.py")


def test_punct_set_consistent():
    """后端 SENT_PUNCT、前端 splitTextAtPunct、桌面实现三者必须一致。"""
    asr_src = open(ASR_PY, encoding="utf-8").read()
    js_src = open(JS, encoding="utf-8").read()
    desktop_src = open(DESKTOP_CUES, encoding="utf-8").read()
    assert 'SENT_PUNCT = "。！？；!?;…."' in asr_src, "后端标点集已变更，请同步前端"
    assert "[^。！？；!?;….]+[。！？；!?;….]*" in js_src, "前端标点集已变更，请同步后端"
    assert 'SENT_PUNCT = "。！？；!?;…."' in desktop_src, "桌面标点集已变更，请同步前后端"


def test_js_dom_ids_exist():
    """app.js 中 $("id") 引用的元素必须都在 index.html 定义。"""
    html = open(HTML, encoding="utf-8").read()
    js = open(JS, encoding="utf-8").read()
    html_ids = set(re.findall(r'id="([^"]+)"', html))
    js_ids = set(re.findall(r'\$\("([A-Za-z][\w-]*)"\)', js))
    missing = js_ids - html_ids
    assert not missing, f"app.js 引用了 index.html 中不存在的 DOM id: {sorted(missing)}"


def test_no_dynamic_dollar_lookups():
    # app.js 中不允许出现模板字符串形式的 $() 动态 id 查询（静态检查覆盖不到）
    js = open(JS, encoding="utf-8").read()
    assert "$(`" not in js, "存在模板字符串形式的 $() 查询，请改为静态 id"

"""桌面后端桥接（desktop/bridge.py）与日志初始化测试。

识别本身用假的 ``asr.transcribe`` 替换，验证线程编排、进度信号与失败处理；
真实识别由端到端测试覆盖。
"""
from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import asr, service  # noqa: E402

SRT_SAMPLE = (
    "1\n00:00:01,000 --> 00:00:03,000\n第一条\n\n"
    "2\n00:00:04,000 --> 00:00:06,500\n第二条\n"
)


def _wait_for(qapp, events: list, timeout: float = 10.0) -> bool:
    """处理 Qt 事件直到有信号到达（跨线程信号需要事件循环）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        qapp.processEvents()
        if events:
            return True
        time.sleep(0.02)
    return bool(events)


@pytest.fixture
def media_file(tmp_path):
    path = tmp_path / "样本视频.mp4"
    path.write_bytes(b"\x00" * 4096)
    return str(path)


# ---------------------------------------------------------------- 导入 / 导出

def test_import_subtitle_creates_done_job(bridge, tmp_path):
    path = tmp_path / "导入.srt"
    path.write_text(SRT_SAMPLE, encoding="utf-8")
    job_id, count = bridge.import_subtitle(str(path))
    assert count == 2
    job = bridge.load_job(job_id)
    assert job["kind"] == "import"
    assert job["status"] == "done"
    assert [c["text"] for c in job["segments"]] == ["第一条", "第二条"]


def test_import_rejects_garbage(bridge, tmp_path):
    path = tmp_path / "垃圾.srt"
    path.write_bytes(os.urandom(256))
    with pytest.raises(service.SubtitleParseError):
        bridge.import_subtitle(str(path))


def test_import_rejects_oversize(bridge, tmp_path, monkeypatch):
    monkeypatch.setattr(service, "IMPORT_MAX_BYTES", 16)
    path = tmp_path / "大.srt"
    path.write_text(SRT_SAMPLE, encoding="utf-8")
    with pytest.raises(service.UploadTooLarge):
        bridge.import_subtitle(str(path))


@pytest.mark.parametrize("fmt", ["srt", "vtt", "ass", "txt", "json"])
def test_export_writes_expected_content(bridge, tmp_path, fmt):
    path = tmp_path / "导出.srt"
    path.write_text(SRT_SAMPLE, encoding="utf-8")
    job_id, _ = bridge.import_subtitle(str(path))
    target = tmp_path / f"out.{fmt}"
    name = bridge.export_to_file(job_id, fmt, str(target))
    assert name == f"out.{fmt}"
    assert target.exists()
    text = target.read_text(encoding="utf-8")
    assert service.export_bytes(bridge.load_job(job_id)["segments"], fmt) \
        == target.read_bytes()
    if fmt == "ass":
        assert text.startswith("\ufeff")     # 必剪/剪映需要 BOM
    if fmt == "srt":
        assert "-->" in text and "第一条" in text


def test_export_filename_uses_job_name(bridge, tmp_path):
    path = tmp_path / "视频.mp4"
    path.write_bytes(b"\x00" * 32)
    job_id = bridge.start_transcription(str(path), {"model": "tiny"})
    assert bridge.export_filename(job_id, "srt") == "视频.srt"


def test_export_missing_job_raises(bridge, tmp_path):
    with pytest.raises(ValueError):
        bridge.export_to_file("不存在", "srt", str(tmp_path / "x.srt"))


def test_save_segments_persists(bridge, tmp_path):
    path = tmp_path / "保存.srt"
    path.write_text(SRT_SAMPLE, encoding="utf-8")
    job_id, _ = bridge.import_subtitle(str(path))
    cues = [{"start": 0.5, "end": 1.5, "text": "改过"}]
    assert bridge.save_segments(job_id, cues) is True
    assert bridge.load_job(job_id)["segments"] == cues
    assert bridge.save_segments("不存在", cues) is False


# ---------------------------------------------------------------- 识别编排

def test_start_transcription_success(bridge, media_file, monkeypatch, qapp):
    calls = {}

    def fake_transcribe(path, **kwargs):
        calls["path"] = path
        calls["kwargs"] = kwargs
        progress = kwargs.get("progress_cb")
        if progress:
            progress(0.5, "识别中")
            progress(1.0, "识别完成，共 2 条")
        return [{"start": 0.0, "end": 1.0, "text": "你好"},
                {"start": 1.2, "end": 2.0, "text": "世界"}]

    monkeypatch.setattr(asr, "transcribe", fake_transcribe)
    states, progress_events = [], []
    bridge.jobStateChanged.connect(lambda *a: states.append(a))
    bridge.progressChanged.connect(lambda *a: progress_events.append(a))

    job_id = bridge.start_transcription(media_file, {"model": "tiny",
                                                     "language": "zh"})
    assert bridge.is_running(job_id) is True
    assert _wait_for(qapp, states) is True
    assert states[0][1] == "done"
    assert bridge.is_running(job_id) is False
    assert bridge.busy is False

    job = bridge.load_job(job_id)
    assert [c["text"] for c in job["segments"]] == ["你好", "世界"]
    assert job["status"] == "done"
    # 媒体被复制进任务目录（与 HTTP 上传同一套落位逻辑）
    assert job["has_media"] is True
    assert os.path.dirname(calls["path"]).endswith(job_id)
    assert progress_events and progress_events[-1][1] == 1.0
    assert calls["kwargs"]["language"] == "zh"


def test_start_transcription_failure_reports_error(bridge, media_file,
                                                   monkeypatch, qapp):
    def boom(*_args, **_kwargs):
        raise RuntimeError("模型下载失败")

    monkeypatch.setattr(asr, "transcribe", boom)
    states = []
    bridge.jobStateChanged.connect(lambda *a: states.append(a))
    job_id = bridge.start_transcription(media_file, {"model": "tiny"})
    assert _wait_for(qapp, states) is True
    assert states[0][0] == job_id
    assert states[0][1] == "error"
    assert "模型下载失败" in states[0][2]
    assert bridge.load_job(job_id)["status"] == "error"


def test_start_transcription_rejects_oversize_media(bridge, media_file,
                                                    monkeypatch, qapp):
    monkeypatch.setattr(service, "MAX_UPLOAD_BYTES", 128)
    states = []
    bridge.jobStateChanged.connect(lambda *a: states.append(a))
    job_id = bridge.start_transcription(media_file, {"model": "tiny"})
    assert _wait_for(qapp, states) is True
    assert states[0][1] == "error"
    assert "导入媒体失败" in states[0][2]
    assert bridge.load_job(job_id)["has_media"] is False


def test_media_path_returns_empty_for_deleted_file(bridge, media_file,
                                                   monkeypatch, qapp):
    monkeypatch.setattr(asr, "transcribe",
                        lambda *a, **k: [{"start": 0, "end": 1, "text": "x"}])
    states = []
    bridge.jobStateChanged.connect(lambda *a: states.append(a))
    job_id = bridge.start_transcription(media_file, {"model": "tiny"})
    assert _wait_for(qapp, states)
    path = bridge.media_path(job_id)
    assert path and os.path.exists(path)
    os.remove(path)
    assert bridge.media_path(job_id) == ""


def test_settings_roundtrip(bridge):
    assert bridge.last_job_id() == ""
    bridge.remember_job("abc123")
    assert bridge.last_job_id() == "abc123"
    bridge.forget_job()
    assert bridge.last_job_id() == ""
    bridge.set_setting("ui/sub_font_px", 24)
    assert str(bridge.get_setting("ui/sub_font_px")) == "24"


# ---------------------------------------------------------------- 日志初始化

def test_setup_logging_writes_file(tmp_path):
    import logging

    from desktop.logging_setup import setup_logging

    log_path = setup_logging(str(tmp_path))
    assert os.path.exists(log_path)
    logging.getLogger("subtitle-studio").info("测试日志行")
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert "测试日志行" in open(log_path, encoding="utf-8").read()


def test_setup_logging_takes_over_missing_streams(tmp_path, monkeypatch):
    """窗口程序（console=False）下 stdout/stderr 为 None：必须不崩且能落盘。"""
    import logging
    import sys as _sys

    from desktop import logging_setup

    monkeypatch.setattr(_sys, "stdout", None)
    monkeypatch.setattr(_sys, "stderr", None)
    log_path = logging_setup.setup_logging(str(tmp_path))

    assert _sys.stdout is not None and _sys.stderr is not None
    print("打印到 stdout 的内容")          # 不允许抛 AttributeError
    _sys.stderr.write("写 stderr 的内容\n")
    _sys.stderr.flush()
    for handler in logging.getLogger().handlers:
        handler.flush()
    content = open(log_path, encoding="utf-8").read()
    assert "打印到 stdout 的内容" in content
    assert "写 stderr 的内容" in content

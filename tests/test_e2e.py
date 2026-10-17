"""端到端测试：真实启动服务，用 edge-tts 生成中文语音，
走完 上传识别 -> 轮询 -> 编辑保存 -> 导出 -> 再导入 全流程。

跳过条件：未安装 edge-tts 或无法访问其服务时自动 skip（单元测试不受影响）。
运行：python -m pytest tests/test_e2e.py -v
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"

TEST_TEXT = (
    "大家好，欢迎使用字幕工坊。"
    "这是一个语音识别测试。"
    "我们希望每一句话都能被正确地识别出来。"
    "标点断句清晰，时间轴对齐准确。"
    "编辑起来非常方便。"
    "谢谢观看。"
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _generate_audio(path: str) -> None:
    import edge_tts

    comm = edge_tts.Communicate(TEST_TEXT, "zh-CN-XiaoxiaoNeural", rate="+0%")
    await comm.save(path)


@pytest.fixture(scope="module")
def server():
    """启动真实服务，yield (base, audio_path)，结束后退出。

    注意：服务 stdout/stderr 必须重定向到文件而不是 subprocess.PIPE——
    模型下载时 tqdm 进度条会写满管道缓冲区（64KB）导致服务死锁。
    """
    audio = os.path.join(PROJECT_ROOT, "data", "e2e_test_audio.mp3")
    os.makedirs(os.path.dirname(audio), exist_ok=True)
    try:
        asyncio.run(_generate_audio(audio))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"edge-tts 不可用，跳过 E2E: {exc}")

    port = _free_port()
    env = dict(os.environ, PORT=str(port), HF_HUB_DISABLE_PROGRESS_BARS="1",
               # 端到端测试只走 HTTP 接口，不开桌面窗口
               SUBTITLE_STUDIO_NO_GUI="1")
    log_path = os.path.join(PROJECT_ROOT, "data", "e2e_server.log")
    logf = open(log_path, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [sys.executable, "app.py"],
        cwd=PROJECT_ROOT,
        env=env,
        stdout=logf,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            if proc.poll() is not None:
                pytest.fail("服务提前退出，详见 data/e2e_server.log")
            try:
                urllib.request.urlopen(base + "/api/config", timeout=2)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        else:
            pytest.fail("服务启动超时")
        yield base, audio
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        logf.close()


# ---------------------------------------------------------------- 工具

def _post_file(url: str, filepath: str, fields: dict) -> dict:
    boundary = "----E2EBoundary" + uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += (f"--{boundary}\r\n"
                 f'Content-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'
                 ).encode("utf-8")
    filename = os.path.basename(filepath)
    with open(filepath, "rb") as f:
        content = f.read()
    body += (f"--{boundary}\r\n"
             f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
             f"Content-Type: application/octet-stream\r\n\r\n").encode("utf-8")
    body += content + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(url: str, headers: dict | None = None) -> tuple[int, bytes, dict]:
    """返回 (status, body, headers)；headers 键统一小写，便于大小写不敏感查找。"""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read(), {k.lower(): v for k, v in r.headers.items()}
    except urllib.error.HTTPError as e:
        return e.code, e.read(), {k.lower(): v for k, v in e.headers.items()}


def _post_json(url: str, payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


# ---------------------------------------------------------------- 用例

def test_static_pages(server):
    base, _ = server
    status, body, _ = _get(base + "/")
    assert status == 200 and "字幕工坊".encode("utf-8") in body
    status, body, headers = _get(base + "/static/app.js")
    assert status == 200 and len(body) > 1000
    status, body, _ = _get(base + "/static/style.css")
    assert status == 200 and b"cue-row" in body


def test_config(server):
    base, _ = server
    status, body, _ = _get(base + "/api/config")
    cfg = json.loads(body)
    ids = [m["id"] for m in cfg["models"]]
    assert {"tiny", "base", "small", "medium", "large-v3"} <= set(ids)
    assert any(l["id"] == "zh" for l in cfg["languages"])


def test_full_recognition_flow(server):
    base, audio = server
    # 1. 上传识别（base 模型：体积小、CPU 上够快）
    r = _post_file(base + "/api/transcribe", audio,
                   {"model": "base", "language": "zh", "task": "transcribe"})
    job_id = r["job_id"]

    # 2. 轮询直到完成
    deadline = time.time() + 600
    job = None
    while time.time() < deadline:
        status, body, _ = _get(f"{base}/api/jobs/{job_id}")
        job = json.loads(body)
        if job["status"] in ("done", "error"):
            break
        time.sleep(2)
    assert job["status"] == "done", f"识别失败: {job.get('message')}"

    segments = job["segments"]
    assert len(segments) >= 3, f"识别条目过少: {segments}"
    all_text = "".join(s["text"] for s in segments)
    assert any(k in all_text for k in ["字幕", "识别", "测试", "谢谢"]), all_text

    # 时间轴基本正确性
    for s in segments:
        assert s["end"] > s["start"]
        assert s["text"].strip()
    for a, b in zip(segments, segments[1:]):
        assert b["start"] >= a["start"] - 0.5  # 允许轻微回退

    # 3. 编辑保存
    segments[0]["text"] = "【已编辑】" + segments[0]["text"]
    segments[1]["start"] = round(segments[1]["start"] + 0.5, 3)
    status, saved = _post_json(f"{base}/api/jobs/{job_id}/segments",
                               {"segments": segments})
    assert status == 200 and saved["count"] == len(segments)
    status, body, _ = _get(f"{base}/api/jobs/{job_id}")
    reloaded = json.loads(body)["segments"]
    assert reloaded[0]["text"].startswith("【已编辑】")
    assert abs(reloaded[1]["start"] - segments[1]["start"]) < 0.002

    # 4. 导出 SRT / VTT
    status, srt_bytes, headers = _get(f"{base}/api/jobs/{job_id}/export?format=srt")
    assert status == 200
    assert "attachment" in headers.get("content-disposition", "")
    srt_text = srt_bytes.decode("utf-8")
    assert "-->" in srt_text and "【已编辑】" in srt_text

    status, vtt_bytes, _ = _get(f"{base}/api/jobs/{job_id}/export?format=vtt")
    assert vtt_bytes.decode("utf-8").startswith("WEBVTT")

    # 5. 再导入导出的 SRT，验证往返一致
    tmp_srt = os.path.join(PROJECT_ROOT, "data", "e2e_roundtrip.srt")
    with open(tmp_srt, "w", encoding="utf-8") as f:
        f.write(srt_text)
    r2 = _post_file(base + "/api/import", tmp_srt, {})
    assert r2["count"] == len(segments)
    status, body, _ = _get(f"{base}/api/jobs/{r2['job_id']}")
    imported = json.loads(body)["segments"]
    assert imported[0]["text"] == reloaded[0]["text"]
    assert abs(imported[1]["start"] - reloaded[1]["start"]) < 0.002


def test_media_range_request(server):
    base, audio = server
    r = _post_file(base + "/api/transcribe", audio,
                   {"model": "base", "language": "zh", "task": "transcribe"})
    job_id = r["job_id"]
    # 媒体应在识别完成前就可访问（用于预览）
    status, body, headers = _get(f"{base}/api/media/{job_id}",
                                 {"Range": "bytes=0-1023"})
    assert status == 206, f"Range 请求应返回 206，实际 {status}"
    assert headers.get("content-range", "").startswith("bytes 0-1023/")
    assert headers.get("accept-ranges") == "bytes"
    assert len(body) == 1024

    status, body, headers = _get(f"{base}/api/media/{job_id}")
    assert status == 200
    assert len(body) == os.path.getsize(audio)


def test_import_garbage_file(server):
    base, _ = server
    tmp = os.path.join(PROJECT_ROOT, "data", "e2e_garbage.bin")
    with open(tmp, "wb") as f:
        f.write(os.urandom(512))
    boundary = "----E2EBoundary" + uuid.uuid4().hex
    body = (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="garbage.srt"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n").encode("utf-8")
    body += open(tmp, "rb").read() + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        base + "/api/import", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        urllib.request.urlopen(req, timeout=30)
        raise AssertionError("垃圾文件应当返回 400")
    except urllib.error.HTTPError as e:
        assert e.code == 400

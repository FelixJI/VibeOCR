"""OCR HTTP Worker 端到端烟雾测试（真实子进程）。

启动真实 worker 子进程（ocr_worker_http.main），验证：
- 端口握手（首行 stdout VIBEOCR_OCR_WORKER_PORT=<port>）
- /health 就绪轮询
- OcrHttpClient.start() 拉起子进程并等待就绪

不调用真实 OCR（避免依赖 PaddleOCR 环境）。仅验证进程生命周期 + HTTP 接线。
标记 slow，默认跳过（需 -m "e2e" 显式运行）。
"""

import pytest

pytestmark = pytest.mark.slow


def test_worker_subprocess_health_handshake():
    """真实启动 worker 子进程，/health 返回 200。"""
    import subprocess
    import sys
    import time

    import httpx

    proc = subprocess.Popen(
        [sys.executable, "-m", "vibeocr.worker_host.ocr_worker_http", "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    try:
        # 读首行拿端口
        first_line = proc.stdout.readline().strip() if proc.stdout else ""
        assert first_line.startswith("VIBEOCR_OCR_WORKER_PORT="), (
            f"首行应为端口握手，实际: {first_line!r}"
        )
        port = int(first_line.split("=", 1)[1])
        base_url = f"http://127.0.0.1:{port}"

        # 轮询 /health（uvicorn 启动需 1-2s）
        deadline = time.monotonic() + 20
        last_err = None
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                pytest.fail(f"worker 进程提前退出，码 {proc.returncode}")
            try:
                resp = httpx.get(f"{base_url}/health", timeout=2.0)
                if resp.status_code == 200:
                    assert resp.json() == {"status": "ok"}
                    return
            except Exception as e:
                last_err = e
            time.sleep(0.3)
        pytest.fail(f"worker {base_url} 健康检查超时: {last_err}")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def test_client_starts_worker_and_waits_ready(monkeypatch):
    """OcrHttpClient.start() 拉起子进程并等到 /health 就绪。"""
    import sys

    # 真实启动会拉起 worker；用真实 _resolve_python_exe / _get_worker_env，
    # 但确保不依赖 PaddleOCR（仅 /health）。
    from vibeocr.worker_host.ocr_http_client import OcrHttpClient

    # 用当前解释器（dev 环境），绕过嵌入式 Python 解析
    monkeypatch.setattr(OcrHttpClient, "_resolve_python_exe", lambda self: sys.executable)

    client = OcrHttpClient()
    try:
        client.start(use_gpu=False)
        assert client.is_started
        # 直接打 /health 验证
        resp = client._client().get("/health")  # type: ignore[attr-defined]
        assert resp.status_code == 200
    finally:
        client.stop()

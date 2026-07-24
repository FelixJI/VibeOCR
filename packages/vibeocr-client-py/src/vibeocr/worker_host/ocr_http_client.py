"""OCR HTTP Worker 客户端（UI 侧）。

主进程经 httpx 调用 OCR HTTP Worker 子进程（ocr_worker_http.py）。替代旧 SHM
路径（SyncBackendClient + 命名管道 + shared_payload + SHM worker）。

设计仿 ``pdf_backend_client.py``：
- 单例，延迟启动 worker 子进程，首次请求时拉起。
- ``_wait_ready`` 轮询 ``/health`` 等就绪。
- httpx 同步 Client 非线程安全：按线程标识各持独立 client。
- ``JobObjectGuard`` 绑定进程生命周期（主进程崩溃时内核连带终止 worker）。
- 日志经 ``SubprocessLogForwarder`` 转发。

本文件先实现 ``recognize_sync`` 单端点闭环（阶段 1）。后续端点（batch/export/
qr/cache/settings）签名对齐 ``sync_client.SyncBackendClient``，逐步补齐。
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import threading
import time
from typing import Any

import httpx

from vibeocr.utils.job_object import JobObjectGuard
from vibeocr.utils.subprocess_log import SubprocessLogForwarder
from vibeocr.worker_host.sync_client import SyncBackendError, _reconstruct_ocr_result

logger = logging.getLogger(__name__)

# 启动后等待就绪的超时（秒）
_WORKER_START_TIMEOUT = 60.0
# 常规调用超时（单图 OCR）；长操作（批量/预加载）调用方显式传更长超时
_DEFAULT_TIMEOUT = httpx.Timeout(300.0, connect=5.0)


class OcrHttpError(SyncBackendError):
    """OCR HTTP worker 调用失败。

    继承 ``SyncBackendError`` 让既有 ``except SyncBackendError`` 重试逻辑
    （single_recognition_tab / qrcode_tab）对 HTTP 模式同样生效，无需改调用方。
    """


class OcrHttpClient:
    """OCR HTTP Worker 单例客户端。延迟启动子进程。"""

    _instance: OcrHttpClient | None = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._base_url: str = ""
        self._job_guard: JobObjectGuard | None = None
        self._lock = threading.RLock()
        self._started = False
        # httpx 同步 Client 非线程安全：按线程标识各持独立 client。
        self._http_clients: dict[int, httpx.Client] = {}
        self._log_thread: threading.Thread | None = None
        self._use_gpu: bool = True

    @classmethod
    def instance(cls) -> OcrHttpClient:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ---- 进程生命周期 ---------------------------------------------------

    def _resolve_python_exe(self) -> str:
        """选择 worker 子进程 Python 解释器（对齐 PDF 后端/MinerU 范式）。"""
        from vibeocr.env_manager import get_embedded_python, get_project_root

        project_root = get_project_root()
        embedded = get_embedded_python(project_root)
        if embedded.exists():
            return str(embedded)
        return sys.executable

    def _get_worker_env(self) -> dict[str, str]:
        """构造 worker 子进程环境变量（PYTHONPATH 指向 vibeocr 包父目录）。

        与 PDF 后端一致：便携式/嵌入式 Python 是独立解释器，vibeocr 源码不在
        其 site-packages，必须 PYTHONPATH 显式指向。
        """
        env = os.environ.copy()
        if getattr(sys, "frozen", False):
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                existing = env.get("PYTHONPATH", "")
                env["PYTHONPATH"] = (
                    f"{meipass};{existing}" if existing else str(meipass)
                )
        else:
            from vibeocr.env_manager import get_workspace_source_paths

            source_dirs = [str(path) for path in get_workspace_source_paths()]
            sep = os.pathsep
            existing = env.get("PYTHONPATH", "")
            existing_parts = existing.split(sep) if existing else []
            missing = [path for path in source_dirs if path not in existing_parts]
            if missing:
                env["PYTHONPATH"] = sep.join([*missing, *existing_parts])
        return env

    @staticmethod
    def _find_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def _start_log_reader(self, process: subprocess.Popen) -> None:
        """后台线程读 worker stdout，转发到项目日志。"""
        forwarder = SubprocessLogForwarder(
            logger_name="vibeocr.subprocess.ocr_worker_http",
            source_label="[OCR Worker HTTP]",
        )

        def _read() -> None:
            try:
                assert process.stdout is not None
                for raw in process.stdout:
                    text = raw.decode("utf-8", errors="replace")
                    if not text:
                        continue
                    for line in forwarder.split_mixed_lines(text):
                        forwarder.forward(line)
                forwarder.flush()
            except Exception:
                pass

        t = threading.Thread(target=_read, daemon=True, name="OcrWorkerHttpLogReader")
        t.start()
        self._log_thread = t

    def start(
        self,
        *,
        profile: str = "production",
        frontend_id: str = "pyside",
        working_dir: Any = None,
        use_gpu: bool = True,
    ) -> None:
        """启动 OCR HTTP Worker 子进程并等待就绪。线程安全，幂等。

        ``profile``/``frontend_id``/``working_dir`` 为与 ``SyncBackendClient.start``
        签名对齐而保留（HTTP worker 无 profile 概念，忽略）；这让本客户端可在
        ``get_backend_client`` 中与 SHM 客户端互换，UI 调用面零改动。
        """
        del profile, frontend_id, working_dir  # 兼容签名，HTTP worker 不使用
        with self._lock:
            if self._started and self._is_alive():
                return
            self._stop_locked()
            self._use_gpu = use_gpu

            python_exe = self._resolve_python_exe()
            port = self._find_free_port()
            self._base_url = f"http://127.0.0.1:{port}"

            cmd = [
                python_exe,
                "-m",
                "vibeocr.worker_host.ocr_worker_http",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "info",
                "--use-gpu" if use_gpu else "--no-gpu",
            ]
            logger.info("[ocr-http] 启动 worker 子进程 @ %s", self._base_url)
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=False,
                env=self._get_worker_env(),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            self._job_guard = JobObjectGuard(name="vibeocr_ocr_worker_http")
            self._job_guard.assign_from_popen(self._process)
            self._start_log_reader(self._process)

            self._wait_ready()
            self._started = True

    def _is_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + _WORKER_START_TIMEOUT
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                tail = self._drain_stdout_tail()
                msg = f"OCR worker 启动失败，退出码 {self._process.returncode}"
                if tail:
                    msg += f"\n子进程输出末尾:\n{tail}"
                raise OcrHttpError(msg)
            try:
                resp = httpx.get(f"{self._base_url}/health", timeout=2.0)
                if resp.status_code == 200:
                    logger.info("[ocr-http] worker 就绪")
                    return
            except Exception as e:
                last_err = e
            time.sleep(0.3)
        raise OcrHttpError(f"OCR worker {self._base_url} 启动超时({last_err})")

    def _drain_stdout_tail(self, max_lines: int = 30) -> str:
        if self._process is None or self._process.stdout is None:
            return ""
        try:
            lines: list[str] = []
            while len(lines) < max_lines:
                raw = self._process.stdout.readline()
                if not raw:
                    break
                lines.append(raw.decode("utf-8", errors="replace").rstrip())
            return "\n".join(lines)
        except Exception:
            return ""

    def _ensure_started(self) -> None:
        if not self._started or not self._is_alive():
            self.start(use_gpu=self._use_gpu)

    def _client(self) -> httpx.Client:
        """返回当前线程专属的 httpx.Client（线程安全）。"""
        tid = threading.get_ident()
        client = self._http_clients.get(tid)
        if client is None:
            client = httpx.Client(base_url=self._base_url, timeout=_DEFAULT_TIMEOUT)
            self._http_clients[tid] = client
        return client

    def _stop_locked(self) -> None:
        if self._process is not None and self._process.poll() is None:
            with __import__("contextlib").suppress(Exception):
                self._process.terminate()
                self._process.wait(timeout=5)
        self._process = None
        for client in self._http_clients.values():
            with __import__("contextlib").suppress(Exception):
                client.close()
        self._http_clients.clear()
        self._job_guard = None
        self._started = False

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def shutdown(self) -> None:
        """与 SyncBackendClient.shutdown 对齐（session.py 用统一 stop/shutdown）。"""
        self.stop()

    def cancel_active(self) -> None:
        """尽力取消在途请求（与 SyncBackendClient.cancel_active 对齐）。

        HTTP 模式下取消在途请求需关闭当前线程的 httpx.Client（中断 socket）。
        下一调用会重建 client。这是 best-effort：正在执行的 PaddleOCR predict
        无法中途打断（与 SHM 一致），但后续请求不再等待它。
        """
        tid = threading.get_ident()
        client = self._http_clients.pop(tid, None)
        if client is not None:
            with __import__("contextlib").suppress(Exception):
                client.close()

    @property
    def is_started(self) -> bool:
        return self._started and self._is_alive()

    # ---- OCR 端点 -------------------------------------------------------

    def recognize_sync(
        self,
        image_bytes: bytes,
        *,
        pipeline: str = "OCR",
        language: str | None = None,
        options: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """单图 OCR 识别。阻塞直到完成，返回重构后的 ``OCRResult``。

        与旧 ``SyncBackendClient.recognize_sync`` 同契约：返回经
        ``_reconstruct_ocr_result`` 重建的 ``vibeocr.models.ocr_result.OCRResult``
        （text_blocks 反序列化为 ``TextBlock``、text_with_scores 为 tuple 列表），
        使 UI 侧 ``result.has_content_list`` 等属性可用。MinerU 分流由调用方在
        上层处理（pipeline==MinerU 时不走本方法，调主进程 MinerUService）。
        """
        import json

        self._ensure_started()
        data: dict[str, str] = {"pipeline": pipeline}
        if language is not None:
            data["language"] = language
        if options:
            data["options_json"] = json.dumps(options)
        try:
            resp = self._client().post(
                "/ocr/recognize",
                files={"image": ("image.png", image_bytes, "image/png")},
                data=data,
                timeout=timeout,
            )
        except httpx.HTTPError as e:
            raise OcrHttpError(f"OCR recognize 请求失败: {e}") from e
        if resp.status_code != 200:
            raise OcrHttpError(
                f"OCR recognize 失败 ({resp.status_code}): {resp.text[:300]}"
            )
        return _reconstruct_ocr_result(resp.json())

    # ---- OCR ----

    def recognize_batch_sync(
        self,
        images: list[bytes],
        *,
        pipeline: str = "OCR",
        language: str | None = None,
        options: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> list[Any]:
        """批量 OCR。返回重构 ``OCRResult`` 列表（顺序与输入一致，失败项 None）。

        与 ``SyncBackendClient.recognize_batch_sync`` 同契约：每项经
        ``_reconstruct_ocr_result`` 重建为 ``OCRResult``。
        """
        import json

        self._ensure_started()
        data: dict[str, str] = {"pipeline": pipeline}
        if language is not None:
            data["language"] = language
        if options:
            data["options_json"] = json.dumps(options)
        files = [("images", (f"{i}.png", img, "image/png")) for i, img in enumerate(images)]
        try:
            resp = self._client().post(
                "/ocr/recognize_batch", files=files, data=data, timeout=timeout
            )
        except httpx.HTTPError as e:
            raise OcrHttpError(f"OCR recognize_batch 请求失败: {e}") from e
        if resp.status_code != 200:
            raise OcrHttpError(
                f"OCR recognize_batch 失败 ({resp.status_code}): {resp.text[:300]}"
            )
        return [
            _reconstruct_ocr_result(raw) if raw is not None else None
            for raw in resp.json()["results"]
        ]

    def export_ocr_sync(
        self,
        result: dict[str, Any],
        *,
        output_path: str,
        export_format: str,
        overwrite: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """导出 OCR 结果到文件。"""
        self._ensure_started()
        payload = {
            "raw_text": str(result.get("raw_text") or result.get("text") or ""),
            "markdown_text": str(result.get("markdown_text") or ""),
            "html_text": str(result.get("html_text") or ""),
            "raw_blocks": list(result.get("raw_blocks") or result.get("content_list") or []),
            "output_path": output_path,
            "format": export_format,
            "overwrite": overwrite,
        }
        try:
            resp = self._client().post("/ocr/export", json=payload, timeout=timeout)
        except httpx.HTTPError as e:
            raise OcrHttpError(f"OCR export 请求失败: {e}") from e
        if resp.status_code != 200:
            raise OcrHttpError(
                f"OCR export 失败 ({resp.status_code}): {resp.text[:300]}"
            )
        return resp.json()

    # ---- QR ----

    def generate_qrcode_sync(
        self, data: str, *, options: dict[str, Any] | None = None, timeout: float | None = None
    ) -> bytes:
        """生成 QR/条码 PNG bytes。"""
        self._ensure_started()
        payload = {"data": data, "format": "qrcode"}
        if options:
            payload.update(options)
        try:
            resp = self._client().post("/qrcode/generate", json=payload, timeout=timeout)
        except httpx.HTTPError as e:
            raise OcrHttpError(f"QR generate 请求失败: {e}") from e
        if resp.status_code != 200:
            raise OcrHttpError(
                f"QR generate 失败 ({resp.status_code}): {resp.text[:300]}"
            )
        return resp.content

    def generate_qrcode_svg_sync(
        self, data: str, *, options: dict[str, Any] | None = None, timeout: float | None = None
    ) -> str:
        """生成 QR SVG 字符串。"""
        self._ensure_started()
        payload: dict[str, Any] = {"data": data}
        if options:
            payload.update(options)
        try:
            resp = self._client().post("/qrcode/generate_svg", json=payload, timeout=timeout)
        except httpx.HTTPError as e:
            raise OcrHttpError(f"QR generate_svg 请求失败: {e}") from e
        if resp.status_code != 200:
            raise OcrHttpError(
                f"QR generate_svg 失败 ({resp.status_code}): {resp.text[:300]}"
            )
        return str(resp.json()["svg"])

    def decode_qrcode_sync(
        self, image_bytes: bytes, *, timeout: float | None = None
    ) -> list[dict[str, Any]]:
        """解码图像中的 QR/条码。返回 [{"data","format","is_url"}]。"""
        self._ensure_started()
        try:
            resp = self._client().post(
                "/qrcode/decode",
                files={"image": ("image.png", image_bytes, "image/png")},
                timeout=timeout,
            )
        except httpx.HTTPError as e:
            raise OcrHttpError(f"QR decode 请求失败: {e}") from e
        if resp.status_code != 200:
            raise OcrHttpError(
                f"QR decode 失败 ({resp.status_code}): {resp.text[:300]}"
            )
        return list(resp.json()["codes"])

    # ---- pipeline cache ----

    def pipeline_cache_status_sync(self, *, timeout: float | None = None) -> dict[str, Any]:
        self._ensure_started()
        return self._get_json("/pipeline_cache/status", timeout=timeout)

    def set_pipeline_cache_ttl_sync(
        self, pipeline_ttls: dict[str, int], *, timeout: float | None = None
    ) -> bool:
        """尽力而下发 TTL；worker 忙时返回 updated=False（不抛错）。"""
        self._ensure_started()
        body = self._post_json(
            "/pipeline_cache/set_ttl", {"pipeline_ttls": pipeline_ttls}, timeout=timeout
        )
        return bool(body["updated"])

    def release_pipeline_cache_sync(
        self, *, heavy_only: bool = True, timeout: float | None = None
    ) -> list[str]:
        self._ensure_started()
        body = self._post_json(
            "/pipeline_cache/release", {"heavy_only": heavy_only}, timeout=timeout
        )
        return [str(n) for n in body["released"]]

    def preload_pipeline_cache_sync(
        self, pipelines: list[str], *, timeout: float | None = None
    ) -> dict[str, bool]:
        self._ensure_started()
        body = self._post_json(
            "/pipeline_cache/preload", {"pipelines": pipelines}, timeout=timeout
        )
        return {str(k): bool(v) for k, v in body["results"].items()}

    def warmup_pipeline_cache_sync(
        self, pipelines: list[str], *, timeout: float | None = None
    ) -> dict[str, bool]:
        self._ensure_started()
        body = self._post_json(
            "/pipeline_cache/warmup", {"pipelines": pipelines}, timeout=timeout
        )
        return {str(k): bool(v) for k, v in body["results"].items()}

    # ---- settings ----

    def settings_snapshot_sync(self, *, timeout: float | None = None) -> dict[str, Any]:
        self._ensure_started()
        return self._get_json("/settings/snapshot", timeout=timeout)

    def switch_backend_sync(
        self, backend: str, *, timeout: float | None = None
    ) -> dict[str, Any]:
        self._ensure_started()
        return self._post_json(
            "/settings/switch_backend", {"backend": backend}, timeout=timeout
        )

    def install_dependency_sync(
        self, name: str, *, source: str | None = None, timeout: float | None = None
    ) -> dict[str, Any]:
        self._ensure_started()
        payload: dict[str, Any] = {"name": name}
        if source is not None:
            payload["source"] = source
        return self._post_json("/settings/install_dependency", payload, timeout=timeout)

    # ---- HTTP 小工具 ----

    def _get_json(self, path: str, *, timeout: float | None = None) -> dict[str, Any]:
        try:
            resp = self._client().get(path, timeout=timeout)
        except httpx.HTTPError as e:
            raise OcrHttpError(f"GET {path} 请求失败: {e}") from e
        if resp.status_code != 200:
            raise OcrHttpError(f"GET {path} 失败 ({resp.status_code}): {resp.text[:300]}")
        return resp.json()

    def _post_json(
        self, path: str, payload: dict[str, Any], *, timeout: float | None = None
    ) -> dict[str, Any]:
        try:
            resp = self._client().post(path, json=payload, timeout=timeout)
        except httpx.HTTPError as e:
            raise OcrHttpError(f"POST {path} 请求失败: {e}") from e
        if resp.status_code != 200:
            raise OcrHttpError(f"POST {path} 失败 ({resp.status_code}): {resp.text[:300]}")
        return resp.json()


__all__ = ["OcrHttpClient", "OcrHttpError"]

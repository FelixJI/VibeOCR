"""PDF 后端子进程的客户端 + 进程托管。

职责:
- 启动/停止 pdf_backend_process 子进程(端口探测 + JobObjectGuard 孤儿清理)
- 等待 /health 就绪
- 暴露全部 PDF 操作为 httpx 调用,返回 schema 对象
- 崩溃检测 + 自动重启(透明重连)
- 后台线程读子进程 stdout 转发到项目日志

主进程通过 PdfBackendClient 单例访问 PDF 后端,完全不碰 fitz。
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import threading
from typing import Iterator

import httpx

from vibeocr.ipc.schemas import (
    AddTextLayerRequest,
    DeletePagesRequest,
    DetectTextLayersRequest,
    DetectTextLayersResponse,
    HealthResponse,
    InsertBlankRequest,
    InsertFromRequest,
    MovePageRequest,
    MutateResponse,
    OpenRequest,
    OpenResponse,
    PageListRequest,
    PdfDocumentMirror,
    ProgressEvent,
    RenderPreviewRequest,
    RenderThumbnailRequest,
    ReorderRequest,
    RewriteTextLayerRequest,
    RotateRequest,
    SaveRequest,
    SaveResponse,
    UpdateBlockTextRequest,
)
from vibeocr.utils.job_object import JobObjectGuard

logger = logging.getLogger(__name__)

# 启动后等待就绪的超时(秒)
_BACKEND_START_TIMEOUT = 30.0
_HTTP_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
# 长操作(保存/摆正/删除文字层)用更长超时
_HTTP_LONG_TIMEOUT = httpx.Timeout(600.0, connect=5.0)


class PdfBackendError(RuntimeError):
    """PDF 后端调用失败。"""


class PdfBackendClient:
    """PDF 后端单例客户端。延迟启动子进程,主进程首次 open 时拉起。"""

    _instance: "PdfBackendClient | None" = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._base_url: str = ""
        self._job_guard: JobObjectGuard | None = None
        self._lock = threading.RLock()
        self._started = False
        self._http: httpx.Client | None = None
        self._log_thread: threading.Thread | None = None

    @classmethod
    def instance(cls) -> "PdfBackendClient":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ---- 进程生命周期 ---------------------------------------------------

    def _resolve_python_exe(self) -> str:
        """选择子进程 Python 解释器(对齐 MinerU 范式)。"""
        from vibeocr.env_manager import get_embedded_python, get_project_root

        project_root = get_project_root()
        embedded = get_embedded_python(project_root)
        if embedded.exists():
            return str(embedded)
        return sys.executable

    def _find_free_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _start_log_reader(self, process: subprocess.Popen) -> None:
        """后台线程读子进程 stdout,转发到项目日志 + 解析就绪端口。"""
        def _read() -> None:
            backend_logger = logging.getLogger("vibeocr.pdf_backend")
            try:
                assert process.stdout is not None
                for raw in process.stdout:
                    line = raw.decode("utf-8", errors="replace").rstrip()
                    if not line:
                        continue
                    # uvicorn 日志级别前缀粗解析
                    if line.startswith("INFO"):
                        backend_logger.info("%s", line)
                    elif line.startswith("WARNING") or line.startswith("WARN"):
                        backend_logger.warning("%s", line)
                    elif line.startswith("ERROR"):
                        backend_logger.error("%s", line)
                    elif line.startswith("DEBUG"):
                        backend_logger.debug("%s", line)
                    else:
                        backend_logger.info("%s", line)
            except Exception:
                pass

        t = threading.Thread(target=_read, daemon=True, name="PdfBackendLogReader")
        t.start()
        self._log_thread = t

    def start(self) -> None:
        """启动 PDF 后端子进程并等待就绪。线程安全,幂等。"""
        with self._lock:
            if self._started and self._is_alive():
                return
            self._stop_locked()

            python_exe = self._resolve_python_exe()
            port = self._find_free_port()
            self._base_url = f"http://127.0.0.1:{port}"

            cmd = [
                python_exe, "-m", "vibeocr.services.pdf_backend_process",
                "--host", "127.0.0.1",
                "--port", str(port),
                "--log-level", "info",
            ]
            logger.info("[pdf-backend] 启动子进程 @ %s", self._base_url)
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # 合并到 stdout 统一读
                text=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            # 绑定 Job Object:主进程崩溃时内核连带终止后端
            self._job_guard = JobObjectGuard(name="vibeocr_pdf_backend")
            self._job_guard.assign_from_popen(self._process)
            self._start_log_reader(self._process)

            # 等待就绪
            self._wait_ready()
            self._http = httpx.Client(base_url=self._base_url, timeout=_HTTP_TIMEOUT)
            self._started = True

    def _is_alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _wait_ready(self) -> None:
        import time

        deadline = time.monotonic() + _BACKEND_START_TIMEOUT
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                raise PdfBackendError(
                    f"PDF 后端启动失败,退出码 {self._process.returncode}"
                )
            try:
                resp = httpx.get(f"{self._base_url}/health", timeout=2.0)
                if resp.status_code == 200:
                    logger.info("[pdf-backend] 就绪")
                    return
            except Exception as e:  # noqa: BLE001
                last_err = e
            time.sleep(0.3)
        raise PdfBackendError(
            f"PDF 后端 {self._base_url} 启动超时({last_err})"
        )

    def _stop_locked(self) -> None:
        if self._job_guard is not None:
            try:
                self._job_guard.close()
            except Exception:
                pass
            self._job_guard = None
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None
        if self._http is not None:
            try:
                self._http.close()
            except Exception:
                pass
            self._http = None
        self._started = False

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _ensure_started(self) -> httpx.Client:
        """确保后端已启动,返回 http client。崩溃则重启。"""
        if not self._started or not self._is_alive():
            self.start()
        assert self._http is not None
        return self._http

    # ---- HTTP 调用辅助 ---------------------------------------------------

    def _post(self, path: str, payload: object | None = None, *, timeout=None) -> httpx.Response:
        client = self._ensure_started()
        try:
            resp = client.post(path, json=payload, timeout=timeout) if payload is not None else client.post(path, timeout=timeout)
        except httpx.HTTPError as e:
            raise PdfBackendError(f"后端调用失败 {path}: {e}") from e
        if resp.status_code >= 400:
            detail = resp.text
            try:
                detail = resp.json().get("detail", detail)
            except Exception:
                pass
            raise PdfBackendError(f"后端错误 {path} ({resp.status_code}): {detail}")
        return resp

    def _get(self, path: str, *, timeout=None) -> httpx.Response:
        client = self._ensure_started()
        try:
            resp = client.get(path, timeout=timeout)
        except httpx.HTTPError as e:
            raise PdfBackendError(f"后端调用失败 {path}: {e}") from e
        if resp.status_code >= 400:
            raise PdfBackendError(f"后端错误 {path} ({resp.status_code}): {resp.text}")
        return resp

    @staticmethod
    def _parse(resp: httpx.Response, model_cls):
        return model_cls.model_validate_json(resp.content)

    # ---- 业务 API -------------------------------------------------------

    def health(self) -> HealthResponse:
        return self._parse(self._get("/health"), HealthResponse)

    def open_session(self, path: str) -> OpenResponse:
        return self._parse(
            self._post("/session/open", OpenRequest(path=path).model_dump()),
            OpenResponse,
        )

    def close_session(self, sid: str) -> None:
        self._post(f"/session/{sid}/close")

    def get_model(self, sid: str) -> PdfDocumentMirror:
        return self._parse(self._post(f"/session/{sid}/model"), PdfDocumentMirror)

    def load_stream(self, sid: str) -> Iterator[ProgressEvent]:
        """打开后流式逐页文字层检测:每页 yield 一个 ProgressEvent。

        每个事件的 page_payload 是该页的 PdfPageInfoMirror dict。
        末行 message="done" 表示完成。
        """
        client = self._ensure_started()
        try:
            with client.stream(
                "POST",
                f"/session/{sid}/load",
                timeout=_HTTP_LONG_TIMEOUT,
            ) as resp:
                if resp.status_code >= 400:
                    raise PdfBackendError(f"load 失败 ({resp.status_code})")
                for line in resp.iter_lines():
                    if not line:
                        continue
                    yield ProgressEvent.model_validate_json(line)
        except httpx.HTTPError as e:
            raise PdfBackendError(f"load 流式调用失败: {e}") from e

    def render_thumbnail(self, sid: str, page: int, size: int = 160) -> bytes:
        """渲染缩略图,返回 PNG 字节。"""
        resp = self._post(
            f"/session/{sid}/render_thumbnail",
            RenderThumbnailRequest(page=page, size=size).model_dump(),
        )
        return resp.content

    def render_preview(self, sid: str, page: int, dpi: int = 150) -> bytes:
        """渲染预览页,返回 PNG 字节。"""
        resp = self._post(
            f"/session/{sid}/render_preview",
            RenderPreviewRequest(page=page, dpi=dpi).model_dump(),
            timeout=_HTTP_LONG_TIMEOUT,
        )
        return resp.content

    def detect_text_layers(self, sid: str, page: int) -> DetectTextLayersResponse:
        return self._parse(
            self._post(
                f"/session/{sid}/detect_text_layers",
                DetectTextLayersRequest(page=page).model_dump(),
            ),
            DetectTextLayersResponse,
        )

    def rotate(self, sid: str, pages: list[int], angle: int) -> MutateResponse:
        return self._parse(
            self._post(
                f"/session/{sid}/rotate",
                RotateRequest(pages=pages, angle=angle).model_dump(),
            ),
            MutateResponse,
        )

    def delete_pages(self, sid: str, pages: list[int]) -> MutateResponse:
        return self._parse(
            self._post(
                f"/session/{sid}/delete_pages",
                DeletePagesRequest(pages=pages).model_dump(),
            ),
            MutateResponse,
        )

    def insert_blank(self, sid: str, after_index: int, width: float = 612.0, height: float = 792.0) -> MutateResponse:
        return self._parse(
            self._post(
                f"/session/{sid}/insert_blank",
                InsertBlankRequest(after_index=after_index, width=width, height=height).model_dump(),
            ),
            MutateResponse,
        )

    def insert_from(self, sid: str, source_path: str, after_index: int) -> MutateResponse:
        return self._parse(
            self._post(
                f"/session/{sid}/insert_from",
                InsertFromRequest(source_path=source_path, after_index=after_index).model_dump(),
            ),
            MutateResponse,
        )

    def move_page(self, sid: str, from_index: int, to_index: int) -> MutateResponse:
        return self._parse(
            self._post(
                f"/session/{sid}/move_page",
                MovePageRequest(from_index=from_index, to_index=to_index).model_dump(),
            ),
            MutateResponse,
        )

    def reorder(self, sid: str, new_order: list[int]) -> MutateResponse:
        return self._parse(
            self._post(
                f"/session/{sid}/reorder",
                ReorderRequest(new_order=new_order).model_dump(),
            ),
            MutateResponse,
        )

    def add_text_layer(self, sid: str, page: int, ocr_result: dict, pdf_settings: dict | None = None, overwrite: bool = False) -> MutateResponse:
        return self._parse(
            self._post(
                f"/session/{sid}/add_text_layer",
                AddTextLayerRequest(page=page, ocr_result=ocr_result, pdf_settings=pdf_settings, overwrite=overwrite).model_dump(),
            ),
            MutateResponse,
        )

    def rewrite_text_layer(self, sid: str, page: int, text_blocks: list, preproc_angle: int = 0, pdf_settings: dict | None = None) -> MutateResponse:
        from vibeocr.ipc.schemas import TextBlockMirror
        blocks = [
            TextBlockMirror(
                text=b.text, score=b.score, bbox=b.bbox, page_idx=b.page_idx,
                is_manually_edited=b.is_manually_edited, label=b.label, order=b.order,
            )
            for b in text_blocks
        ]
        return self._parse(
            self._post(
                f"/session/{sid}/rewrite_text_layer",
                RewriteTextLayerRequest(page=page, text_blocks=blocks, preproc_angle=preproc_angle, pdf_settings=pdf_settings).model_dump(),
            ),
            MutateResponse,
        )

    def update_block_text(self, sid: str, page: int, block_index: int, new_text: str) -> MutateResponse:
        return self._parse(
            self._post(
                f"/session/{sid}/update_block_text",
                UpdateBlockTextRequest(page=page, block_index=block_index, new_text=new_text).model_dump(),
            ),
            MutateResponse,
        )

    def delete_text_layers_stream(self, sid: str, pages: list[int]) -> Iterator[ProgressEvent]:
        """逐页删除文字层,流式返回 ProgressEvent。"""
        client = self._ensure_started()
        try:
            with client.stream(
                "POST",
                f"/session/{sid}/delete_text_layers",
                json=PageListRequest(pages=pages).model_dump(),
                timeout=_HTTP_LONG_TIMEOUT,
            ) as resp:
                if resp.status_code >= 400:
                    raise PdfBackendError(f"删除文字层失败 ({resp.status_code})")
                for line in resp.iter_lines():
                    if not line:
                        continue
                    yield ProgressEvent.model_validate_json(line)
        except httpx.HTTPError as e:
            raise PdfBackendError(f"删除文字层流式调用失败: {e}") from e

    def save(self, sid: str, path: str | None = None, pdf_settings: dict | None = None) -> SaveResponse:
        return self._parse(
            self._post(
                f"/session/{sid}/save",
                SaveRequest(path=path, pdf_settings=pdf_settings).model_dump(),
                timeout=_HTTP_LONG_TIMEOUT,
            ),
            SaveResponse,
        )

    def cancel(self, sid: str) -> None:
        self._post(f"/session/{sid}/cancel")

    def reset_cancel(self, sid: str) -> None:
        self._post(f"/session/{sid}/reset_cancel")

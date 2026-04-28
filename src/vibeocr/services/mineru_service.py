"""MinerU 文档解析服务

通过 mineru-api FastAPI 服务进行文档解析。
自动管理 mineru-api 进程的生命周期。
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from vibeocr.core.singleton_meta import SingletonMeta
from vibeocr.models.ocr_result import OCRResult, TextBlock
from vibeocr.utils.markdown_converter import markdown_to_html
from vibeocr.utils.mime_types import mime_to_extension

if TYPE_CHECKING:
    from vibeocr.models.ocr_options import OCROptions

_logger = logging.getLogger(__name__)

# MinerU discarded block types — appended after layout blocks per page,
# should be excluded from text extraction and rendering.
DISCARDED_BLOCK_TYPES = frozenset({
    "header", "footer", "page_number", "page_footnote", "aside_text",
})


class MinerUService(metaclass=SingletonMeta):
    """MinerU 文档解析服务（单例）

    通过 mineru-api FastAPI 服务进行文档解析。
    自动管理 mineru-api 进程的生命周期。
    """

    _api_process: subprocess.Popen | None = None
    _api_url: str = ""
    _lock = threading.RLock()
    _initialized = False

    def __init__(self):
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    self._ensure_api_running()
                    self._initialized = True

    @classmethod
    def _reset(cls) -> None:
        """重置服务状态（供测试使用）"""
        with cls._lock:
            if cls._api_process is not None:
                cls._api_process.terminate()
                try:
                    cls._api_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    cls._api_process.kill()
                cls._api_process = None
            cls._api_url = ""
            cls._initialized = False

    def _check_api_running(self, url: str) -> bool:
        """检查 mineru-api 是否运行"""
        try:
            resp = httpx.get(f"{url}/health", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False

    def _find_free_port(self) -> int:
        """找一个可用端口"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def _resolve_python_executable(self) -> Path | None:
        """查找可用的 Python 解释器

        查找顺序:
        1. 嵌入式 Python（便携模式）
        2. 当前 Python 解释器（开发模式）
        """
        from vibeocr.env_manager import get_embedded_python, get_project_root

        project_root = get_project_root()
        embedded = get_embedded_python(project_root)
        if embedded.exists():
            return embedded

        return Path(sys.executable)

    def _start_api(self) -> None:
        """启动 mineru-api 进程"""
        python_exe = self._resolve_python_executable()
        if python_exe is None:
            raise RuntimeError(
                "找不到 Python 解释器。请确保已安装 Python 和 mineru[core]"
            )

        port = self._find_free_port()
        url = f"http://127.0.0.1:{port}"

        _logger.info(f"[MinerU] 启动 mineru-api 服务 @ {url}...")

        cmd = [
            str(python_exe),
            "-m", "mineru.cli.fast_api",
            "--host", "127.0.0.1",
            "--port", str(port),
        ]

        log_file = Path(__file__).resolve().parent.parent.parent / "mineru_api.log"

        env = os.environ.copy()
        from vibeocr.env_manager import get_project_root
        from vibeocr.network_detector import NetworkDetector
        detector = NetworkDetector(get_project_root())
        if detector.mineru_source == "modelscope" and not env.get("MINERU_MODEL_SOURCE"):
            env["MINERU_MODEL_SOURCE"] = "modelscope"

        self.__class__._api_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=log_file.open("w", encoding="utf-8"),
            env=env,
        )
        _logger.info(f"[MinerU] 日志输出到: {log_file}")

        # 等待 API 就绪
        for _ in range(120):
            if self.__class__._api_process.poll() is not None:
                raise RuntimeError(
                    f"mineru-api 启动失败，退出码: {self.__class__._api_process.returncode}"
                )
            if self._check_api_running(url):
                self.__class__._api_url = url
                _logger.info(f"[MinerU] mineru-api 服务已就绪 @ {url}")
                return
            time.sleep(1)

        raise RuntimeError("mineru-api 启动超时（120秒）")

    def _ensure_api_running(self) -> None:
        """确保 mineru-api 正在运行"""
        if self.__class__._api_url and self._check_api_running(self.__class__._api_url):
            return
        with self._lock:
            if self.__class__._api_url and self._check_api_running(self.__class__._api_url):
                return
            # 清理旧进程
            if self.__class__._api_process is not None:
                self.__class__._api_process.terminate()
                try:
                    self.__class__._api_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.__class__._api_process.kill()
                self.__class__._api_process = None
            self._start_api()

    def _call_api(self, data: bytes, filename: str, options: OCROptions | None = None) -> dict[str, Any]:
        """调用 mineru-api 的 /file_parse 端点

        Args:
            data: 文件数据（bytes）
            filename: 上传文件名（含扩展名）
            options: OCR 选项（含 backend 和 parse_method）

        Returns:
            API 响应字典
        """
        self._ensure_api_running()

        backend = options.backend if options else "vlm-auto-engine"
        parse_method = options.parse_method if options else "auto"

        files = {"files": (filename, data)}
        params = {
            "return_md": "true",
            "return_content_list": "true",
            "return_images": "true",
            "formula_enable": str(options.enable_formula if options else True).lower(),
            "table_enable": str(options.enable_table if options else True).lower(),
            "backend": backend,
            "parse_method": parse_method,
        }

        # 回退链: vlm-auto-engine → hybrid-auto-engine → pipeline
        fallback_chain = ["vlm-auto-engine", "hybrid-auto-engine", "pipeline"]
        # 从当前 backend 开始，构建回退链
        if backend in fallback_chain:
            start_idx = fallback_chain.index(backend)
            backends_to_try = fallback_chain[start_idx:]
        else:
            backends_to_try = [backend]

        last_error: Exception | None = None
        for current_backend in backends_to_try:
            params["backend"] = current_backend
            _logger.info(f"[MinerU] 使用后端: {current_backend}")
            try:
                resp = httpx.post(
                    f"{self.__class__._api_url}/file_parse",
                    files=files,
                    data=params,
                    timeout=httpx.Timeout(timeout=1800.0, connect=30.0),
                )
                if resp.status_code == 200:
                    return resp.json()

                # 解析错误信息
                try:
                    body = resp.json()
                    detail = body.get("message") or body.get("error") or resp.text[:200]
                except Exception:
                    detail = resp.text[:200]

                last_error = RuntimeError(
                    f"mineru-api 错误 ({resp.status_code}): {detail}"
                )
                _logger.warning(
                    f"[MinerU] 后端 {current_backend} 失败: {detail}，尝试回退..."
                )
            except httpx.TimeoutException as e:
                last_error = e
                _logger.warning(f"[MinerU] 后端 {current_backend} 超时，尝试回退...")
            except httpx.ConnectError as e:
                last_error = e
                _logger.warning(f"[MinerU] 后端 {current_backend} 连接失败，尝试回退...")

        raise last_error or RuntimeError("mineru-api 请求失败")

    def parse(
        self,
        data: bytes,
        mime_type: str,
        options: OCROptions | None = None,
    ) -> OCRResult:
        """解析文档

        Args:
            data: 文件数据（bytes）
            mime_type: MIME 类型
            options: OCR 选项

        Returns:
            OCRResult 对象
        """
        ext = self._get_extension(mime_type)
        filename = f"input{ext}"

        api_result = self._call_api(data, filename, options)
        return self._build_ocr_result(api_result, filename, data=data)

    def _get_extension(self, mime_type: str) -> str:
        return mime_to_extension(mime_type) or ".pdf"

    def _build_ocr_result(
        self,
        api_result: dict[str, Any],
        filename: str,
        data: bytes | None = None,
    ) -> OCRResult:
        """从 API 响应构建 OCRResult"""
        stem = Path(filename).stem
        results = api_result.get("results", {})
        file_result = results.get(stem, {})

        md_content = file_result.get("md_content") or ""

        content_list_raw = file_result.get("content_list")
        content_list: list[dict[str, Any]] = []
        if content_list_raw:
            try:
                content_list = json.loads(content_list_raw)
            except (json.JSONDecodeError, TypeError):
                content_list = []

        images: dict[str, bytes] = {}
        images_dict = file_result.get("images", {})
        for img_name, data_uri in images_dict.items():
            if data_uri and data_uri.startswith("data:"):
                b64_part = data_uri.split(",", 1)[-1]
                images[img_name] = base64.b64decode(b64_part)

        # 从 content_list 提取纯文本（非 Markdown）
        raw_text = self._extract_plain_text(content_list) if content_list else md_content

        # 从 content_list 构建 text_blocks（归一化 bbox + page_idx）
        text_blocks: list[TextBlock] = []
        for i, block in enumerate(content_list):
            if block.get("type", "") in DISCARDED_BLOCK_TYPES:
                continue
            bbox_raw = block.get("bbox")
            if not bbox_raw or len(bbox_raw) < 4:
                continue
            text = self._extract_block_text(block)
            if not text:
                continue
            text_blocks.append(TextBlock(
                text=text,
                score=1.0,  # MineRU content_list 不提供 confidence
                bbox=(float(bbox_raw[0]), float(bbox_raw[1]),
                      float(bbox_raw[2]), float(bbox_raw[3])),
                page_idx=block.get("page_idx"),
                content_index=i,
            ))

        text_with_scores = [(b.text, b.score) for b in text_blocks]
        avg_score = sum(s for _, s in text_with_scores) / len(text_with_scores) if text_with_scores else 0.0

        return OCRResult(
            raw_text=raw_text,
            markdown_text=md_content,
            html_text=markdown_to_html(md_content),
            text_with_scores=text_with_scores,
            avg_score=avg_score,
            low_confidence_items=[],
            pipeline_type="MinerU",
            images=images,
            content_list=content_list,
            text_blocks=text_blocks,
        )

    @staticmethod
    def _strip_html(html: str) -> str:
        """从 HTML 中提取纯文本，合并多余空白"""
        text = re.sub(r"<[^>]+>", " ", html)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _extract_plain_text(content_list: list[dict]) -> str:
        """从 content_list 提取纯文本"""
        parts: list[str] = []
        for block in content_list:
            block_type = block.get("type", "")
            if block_type in DISCARDED_BLOCK_TYPES:
                continue
            if block_type == "table":
                html = block.get("table_body", "")
                parts.append(MinerUService._strip_html(html))
            elif block_type in ("image", "chart"):
                captions = block.get("image_caption") or block.get("chart_caption") or []
                if captions:
                    parts.append(" ".join(captions))
            elif block_type == "list":
                items = block.get("list_items", [])
                parts.extend(items)
            elif block_type == "code":
                body = block.get("code_body", "")
                parts.append(body)
            else:
                text = block.get("text", "")
                if text:
                    parts.append(text)
        return "\n".join(p for p in parts if p)

    @staticmethod
    def _extract_block_text(block: dict) -> str:
        """从单个 content_list 块提取用于 TextBlock 的文本"""
        block_type = block.get("type", "")
        if block_type == "table":
            html = block.get("table_body", "")
            return MinerUService._strip_html(html)
        elif block_type in ("image", "chart"):
            captions = block.get("image_caption") or block.get("chart_caption") or []
            content = block.get("content", "")
            text = " ".join(captions)
            if content:
                text = f"{text} {content}".strip()
            return text or f"[{block_type}]"
        elif block_type == "list":
            items = block.get("list_items", [])
            return "; ".join(items)
        elif block_type == "code":
            return block.get("code_body", "")[:200]
        else:
            return block.get("text", "")

    def shutdown(self) -> None:
        """停止 mineru-api 进程"""
        if self.__class__._api_process is not None:
            _logger.info("[MinerU] 停止 mineru-api 服务...")
            self.__class__._api_process.terminate()
            try:
                self.__class__._api_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.__class__._api_process.kill()
            self.__class__._api_process = None
            self.__class__._api_url = ""
            _logger.info("[MinerU] mineru-api 服务已停止")

"""MinerU 文档解析服务

通过 mineru-api FastAPI 服务进行文档解析。
自动管理 mineru-api 进程的生命周期。
"""

from __future__ import annotations

import base64
import json
import logging
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from vibeocr.core.singleton_meta import SingletonMeta
from vibeocr.models.ocr_result import OCRResult
from vibeocr.utils.markdown_converter import markdown_to_html

if TYPE_CHECKING:
    from vibeocr.models.ocr_options import OCROptions

_logger = logging.getLogger(__name__)


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
                "找不到 Python 解释器。请确保已安装 Python 和 mineru[pipeline]"
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
        self.__class__._api_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=log_file.open("w", encoding="utf-8"),
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

    def _call_api(self, data: bytes, filename: str) -> dict[str, Any]:
        """调用 mineru-api 的 /file_parse 端点

        Args:
            data: 文件数据（bytes）
            filename: 上传文件名（含扩展名）

        Returns:
            API 响应字典
        """
        self._ensure_api_running()

        files = {"files": (filename, data)}
        params = {
            "return_md": "true",
            "return_content_list": "true",
            "return_images": "true",
            "formula_enable": "true",
            "table_enable": "true",
            "backend": "pipeline",
            "parse_method": "auto",
        }

        resp = httpx.post(
            f"{self.__class__._api_url}/file_parse",
            files=files,
            data=params,
            timeout=300,
        )
        if resp.status_code != 200:
            try:
                body = resp.json()
                detail = body.get("message") or body.get("error") or resp.text[:200]
            except Exception:
                detail = resp.text[:200]
            raise RuntimeError(
                f"mineru-api 错误 ({resp.status_code}): {detail}"
            )
        return resp.json()

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

        api_result = self._call_api(data, filename)
        return self._build_ocr_result(api_result, filename)

    def _get_extension(self, mime_type: str) -> str:
        """根据 MIME 类型获取文件扩展名"""
        ext_map = {
            "application/pdf": ".pdf",
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/bmp": ".bmp",
            "image/tiff": ".tiff",
            "image/webp": ".webp",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        }
        return ext_map.get(mime_type, ".pdf")

    def _build_ocr_result(
        self,
        api_result: dict[str, Any],
        filename: str,
    ) -> OCRResult:
        """从 API 响应构建 OCRResult"""
        stem = Path(filename).stem

        # 从 results 中提取当前文件的结果
        results = api_result.get("results", {})
        file_result = results.get(stem, {})

        # Markdown 内容
        md_content = file_result.get("md_content") or ""

        # Content list（API 返回的是 JSON 字符串）
        content_list_raw = file_result.get("content_list")
        content_list: list[dict[str, Any]] = []
        if content_list_raw:
            try:
                content_list = json.loads(content_list_raw)
            except (json.JSONDecodeError, TypeError):
                content_list = []

        # 图片（API 返回 base64 data URI）
        images: dict[str, bytes] = {}
        images_dict = file_result.get("images", {})
        for img_name, data_uri in images_dict.items():
            if data_uri and data_uri.startswith("data:"):
                b64_part = data_uri.split(",", 1)[-1]
                images[img_name] = base64.b64decode(b64_part)

        raw_text = md_content or ""
        return OCRResult(
            raw_text=raw_text,
            markdown_text=md_content,
            html_text=markdown_to_html(md_content),
            text_with_scores=[(raw_text, 1.0)] if raw_text else [],
            avg_score=1.0 if raw_text else 0.0,
            low_confidence_items=[],
            pipeline_type="MinerU",
            images=images,
        )

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

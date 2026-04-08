"""MinerU 文档解析服务

通过 mineru-api FastAPI 服务进行文档解析。
自动管理 mineru-api 进程的生命周期。
"""

from __future__ import annotations

import io
import json
import logging
import shutil
import subprocess
import tempfile
import threading
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vibeocr.core.singleton_meta import SingletonMeta
from vibeocr.models.ocr_result import OCRResult

if TYPE_CHECKING:
    from vibeocr.models.ocr_options import OCROptions

_logger = logging.getLogger(__name__)


class MinerUService(metaclass=SingletonMeta):
    """MinerU 文档解析服务（单例）

    通过 mineru-api FastAPI 服务进行文档解析。
    自动管理 mineru-api 进程的生命周期。
    """

    _api_process: subprocess.Popen | None = None
    _api_url: str = "http://127.0.0.1:8000"
    _output_dir: Path | None = None
    _lock = threading.Lock()
    _initialized = False

    def __init__(self):
        if not self._initialized:
            with self._lock:
                if not self._initialized:
                    self._output_dir = Path(tempfile.mkdtemp(prefix="mineru_"))
                    self._initialized = True

    @classmethod
    def _reset(cls) -> None:
        """重置服务状态（供测试使用）"""
        with cls._lock:
            if cls._api_process is not None:
                cls._api_process.terminate()
                cls._api_process = None
            cls._initialized = False

    def _check_api_running(self) -> bool:
        """检查 mineru-api 是否运行"""
        try:
            req = urllib.request.Request(f"{self._api_url}/health")
            resp = urllib.request.urlopen(req, timeout=3)
            return resp.status == 200
        except Exception:
            return False

    def _start_api(self) -> None:
        """启动 mineru-api 进程"""
        mineru_api = shutil.which("mineru-api")
        if mineru_api is None:
            raise RuntimeError(
                "mineru-api 未找到。请安装: pip install 'mineru[all]'"
            )

        _logger.info("[MinerU] 启动 mineru-api 服务...")

        cmd = [
            mineru_api,
            "--host", "127.0.0.1",
            "--port", "8000",
        ]

        self._api_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # 等待 API 就绪
        import time
        for _ in range(60):  # 最多等 60 秒
            if self._check_api_running():
                _logger.info("[MinerU] mineru-api 服务已就绪")
                return
            time.sleep(1)

        raise RuntimeError("mineru-api 启动超时（60秒）")

    def _ensure_api_running(self) -> None:
        """确保 mineru-api 正在运行"""
        if not self._check_api_running():
            with self._lock:
                if not self._check_api_running():
                    self._start_api()

    def _call_api(self, file_path: Path, options: OCROptions | None = None) -> dict:
        """调用 mineru-api 进行解析

        Args:
            file_path: 要解析的文件路径
            options: OCR 选项

        Returns:
            解析结果字典
        """
        # 构建请求
        boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
        body = b""

        # 添加文件
        with open(file_path, "rb") as f:
            file_data = f.read()

        filename = file_path.name
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'.encode()
        body += b"Content-Type: application/octet-stream\r\n\r\n"
        body += file_data
        body += b"\r\n"

        # 添加参数
        body += f"--{boundary}\r\n".encode()
        body += b'Content-Disposition: form-data; name="return_md"\r\n\r\n'
        body += b"true\r\n"

        body += f"--{boundary}--\r\n".encode()

        req = urllib.request.Request(
            f"{self._api_url}/file_parse",
            data=body,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )

        resp = urllib.request.urlopen(req, timeout=300)
        response_data = resp.read().decode("utf-8")

        # 解析响应
        result = json.loads(response_data)

        # 如果返回的是 ZIP（response_format_zip=true），需要处理
        # 简单情况下，直接取 md_content
        return result

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
        self._ensure_api_running()

        # 写入临时文件
        ext = self._get_extension(mime_type)
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
            f.write(data)
            temp_path = Path(f.name)

        try:
            api_result = self._call_api(temp_path, options)
            return self._build_ocr_result(api_result)
        finally:
            temp_path.unlink(missing_ok=True)

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

    def _build_ocr_result(self, api_result: dict) -> OCRResult:
        """从 API 结果构建 OCRResult"""
        # 提取 markdown 内容
        md_content = ""
        if isinstance(api_result, dict):
            # 可能是直接的响应或包装的响应
            if "md_content" in api_result:
                md_content = api_result["md_content"]
            elif "markdown" in api_result:
                md_content = api_result["markdown"]
            elif "content_list" in api_result:
                # 从 content_list 组装
                parts = []
                for item in api_result["content_list"]:
                    if isinstance(item, dict) and "text" in item:
                        parts.append(item["text"])
                md_content = "\n\n".join(parts)

        # 提取纯文本
        raw_text = md_content
        if not raw_text:
            raw_text = ""

        return OCRResult(
            raw_text=raw_text,
            markdown_text=md_content,
            html_text=md_content,
            text_with_scores=[(raw_text, 1.0)] if raw_text else [],
            avg_score=1.0 if raw_text else 0.0,
            low_confidence_items=[],
            pipeline_type="MinerU",
            images={},
        )

    def shutdown(self) -> None:
        """停止 mineru-api 进程"""
        if self._api_process is not None:
            _logger.info("[MinerU] 停止 mineru-api 服务...")
            self._api_process.terminate()
            try:
                self._api_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._api_process.kill()
            self._api_process = None
            _logger.info("[MinerU] mineru-api 服务已停止")

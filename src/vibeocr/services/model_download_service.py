"""模型下载服务

封装 PaddleX 和 MinerU 模型下载逻辑，供 UI 层和首次使用自动触发调用。
纯 Python 类，不依赖 Qt。
"""

import logging
import os
import subprocess
import threading
from collections.abc import Callable
from enum import Enum
from pathlib import Path

from vibeocr.env_manager import (
    detect_network_source,
    get_embedded_python_executable,
    setup_paddlex_model_source,
)
from vibeocr.model_cache_manager import update_cache

logger = logging.getLogger(__name__)

# PaddleX 管道名称列表（需要下载模型的管道）
PADDLEX_PIPELINES = ["OCR", "table_recognition", "formula_recognition"]

# 默认超时（秒）：单个管道下载
DEFAULT_PIPELINE_TIMEOUT = 600.0
# 默认超时（秒）：MinerU 模型下载
DEFAULT_MINERU_TIMEOUT = 600.0


class DownloadStatus(str, Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


def _download_paddlex_pipeline(
    python_exe: Path,
    pipeline_name: str,
    timeout: float = DEFAULT_PIPELINE_TIMEOUT,
) -> bool:
    """通过调用 create_pipeline() 触发 PaddleX 模型下载"""
    logger.info(f"[模型下载] 开始下载 PaddleX 管道: {pipeline_name}")
    try:
        setup_paddlex_model_source()

        cmd = [
            str(python_exe),
            "-c",
            (
                "from paddlex import create_pipeline; "
                f"create_pipeline(pipeline='{pipeline_name}')"
            ),
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode == 0:
            logger.info(f"[模型下载] PaddleX 管道 {pipeline_name} 下载完成")
            return True
        error = result.stderr or result.stdout or "未知错误"
        logger.error(f"[模型下载] PaddleX 管道 {pipeline_name} 下载失败: {error[:300]}")
        return False
    except subprocess.TimeoutExpired:
        logger.error(f"[模型下载] PaddleX 管道 {pipeline_name} 下载超时 ({timeout}s)")
        return False
    except Exception as e:
        logger.error(f"[模型下载] PaddleX 管道 {pipeline_name} 下载异常: {e}")
        return False


def _download_mineru_models(
    python_exe: Path,
    timeout: float = DEFAULT_MINERU_TIMEOUT,
) -> bool:
    """下载 MinerU 模型"""
    logger.info("[模型下载] 开始下载 MinerU 模型")
    try:
        network = detect_network_source()
        source = "modelscope" if network == "domestic" else "huggingface"
        logger.info(f"[模型下载] 使用模型源: {source}")

        result = subprocess.run(
            [str(python_exe), "-m", "mineru.cli.models_download", "-s", source],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode == 0:
            logger.info("[模型下载] MinerU 模型下载完成")
            return True
        error = result.stderr or result.stdout or "未知错误"
        logger.error(f"[模型下载] MinerU 模型下载失败: {error[:300]}")
        return False
    except subprocess.TimeoutExpired:
        logger.error(f"[模型下载] MinerU 模型下载超时 ({timeout}s)")
        return False
    except Exception as e:
        logger.error(f"[模型下载] MinerU 模型下载异常: {e}")
        return False


class ModelDownloadService:
    """模型下载服务

    封装 PaddleX 和 MinerU 模型下载逻辑，支持三个入口：
    1. 安装流程（InstallDialog 完成后）
    2. 主窗口"下载模型"按钮
    3. Worker 首次使用自动触发
    """

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root
        self._python_exe = get_embedded_python_executable(project_root)
        self._statuses: dict[str, DownloadStatus] = {}
        self._lock = threading.Lock()
        self._init_statuses()

    def _init_statuses(self) -> None:
        """初始化各管道的下载状态"""
        with self._lock:
            self._statuses = {}
            for name in PADDLEX_PIPELINES:
                self._statuses[name] = DownloadStatus.PENDING
            self._statuses["MinerU"] = DownloadStatus.PENDING

    def get_status(self) -> dict[str, DownloadStatus]:
        """获取各管道下载状态"""
        with self._lock:
            return dict(self._statuses)

    def download_all(
        self,
        progress_callback: Callable[[str, str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, DownloadStatus]:
        """下载所有管道模型"""
        def report(stage: str, message: str) -> None:
            if progress_callback:
                progress_callback(stage, message)

        for pipeline_name in PADDLEX_PIPELINES:
            if cancel_event and cancel_event.is_set():
                with self._lock:
                    for remaining in PADDLEX_PIPELINES:
                        if self._statuses.get(remaining) == DownloadStatus.PENDING:
                            self._statuses[remaining] = DownloadStatus.SKIPPED
                    if self._statuses.get("MinerU") == DownloadStatus.PENDING:
                        self._statuses["MinerU"] = DownloadStatus.SKIPPED
                report("模型下载", "已取消")
                return self.get_status()

            report("模型下载", f"正在下载 PaddleX {pipeline_name} 模型...")
            with self._lock:
                self._statuses[pipeline_name] = DownloadStatus.DOWNLOADING

            success = _download_paddlex_pipeline(self._python_exe, pipeline_name)
            with self._lock:
                self._statuses[pipeline_name] = (
                    DownloadStatus.COMPLETED if success else DownloadStatus.FAILED
                )

            if success:
                report("模型下载", f"PaddleX {pipeline_name} 模型下载完成")
            else:
                report("模型下载", f"PaddleX {pipeline_name} 模型下载失败")

        if cancel_event and cancel_event.is_set():
            with self._lock:
                if self._statuses.get("MinerU") == DownloadStatus.PENDING:
                    self._statuses["MinerU"] = DownloadStatus.SKIPPED
            report("模型下载", "已取消")
            return self.get_status()

        report("模型下载", "正在下载 MinerU 模型...")
        with self._lock:
            self._statuses["MinerU"] = DownloadStatus.DOWNLOADING

        success = _download_mineru_models(self._python_exe)
        with self._lock:
            self._statuses["MinerU"] = (
                DownloadStatus.COMPLETED if success else DownloadStatus.FAILED
            )

        if success:
            report("模型下载", "MinerU 模型下载完成")
        else:
            report("模型下载", "MinerU 模型下载失败")

        try:
            update_cache(self._project_root)
        except Exception as e:
            logger.warning(f"更新模型缓存失败: {e}")

        report("模型下载", "模型下载流程结束")
        return self.get_status()

    def download_pipeline(
        self,
        pipeline_name: str,
        progress_callback: Callable[[str, str], None] | None = None,
        timeout: float = DEFAULT_PIPELINE_TIMEOUT,
    ) -> bool:
        """下载单个管道模型（首次使用时调用）"""
        with self._lock:
            self._statuses[pipeline_name] = DownloadStatus.DOWNLOADING

        if pipeline_name == "MinerU":
            if progress_callback:
                progress_callback("模型下载", "正在下载 MinerU 模型...")
            success = _download_mineru_models(self._python_exe, timeout=timeout)
            with self._lock:
                self._statuses[pipeline_name] = (
                    DownloadStatus.COMPLETED if success else DownloadStatus.FAILED
                )
            if success:
                try:
                    update_cache(self._project_root)
                except Exception:
                    pass
            return success

        if progress_callback:
            progress_callback("模型下载", f"正在下载 PaddleX {pipeline_name} 模型...")
        success = _download_paddlex_pipeline(
            self._python_exe, pipeline_name, timeout=timeout,
        )
        with self._lock:
            self._statuses[pipeline_name] = (
                DownloadStatus.COMPLETED if success else DownloadStatus.FAILED
            )
        if success:
            try:
                update_cache(self._project_root)
            except Exception:
                pass
        return success

    def download_mineru_models(
        self,
        progress_callback: Callable[[str, str], None] | None = None,
        timeout: float = DEFAULT_MINERU_TIMEOUT,
    ) -> bool:
        """下载 MinerU 模型"""
        return self.download_pipeline("MinerU", progress_callback, timeout)

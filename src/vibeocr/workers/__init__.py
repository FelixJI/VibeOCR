"""
Workers 包 - 子进程 Worker 模块

提供独立运行的子进程 worker，用于隔离重型依赖
"""

from .ocr_worker import run_worker, OCRWorkerError, SharedMemoryProtocol

__all__ = ['run_worker', 'OCRWorkerError', 'SharedMemoryProtocol']

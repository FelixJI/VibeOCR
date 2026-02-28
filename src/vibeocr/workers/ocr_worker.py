#!/usr/bin/env python
"""OCR Worker 子进程脚本

作为独立子进程运行，通过共享内存与主进程通信。
负责执行 OCR 识别和预加载任务。
支持双共享内存设计：数据通道（OCR请求/结果）和日志通道。

使用方式:
    python -m vibeocr.workers.ocr_worker --shm-name <name> --shm-size <size> --use-gpu
"""

import argparse
import logging
import os
import sys
import threading
import time
import traceback
from typing import Optional

# 配置基本日志（在添加共享内存处理器之前）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


class OCRWorkerError(Exception):
    """OCR Worker 错误"""
    pass


class SharedMemoryLogHandler(logging.Handler):
    """将所有级别日志发送到共享内存"""
    
    def __init__(self, log_protocol):
        super().__init__()
        self.log_protocol = log_protocol
        self._log_queue = []
        self._lock = threading.Lock()
        self._flush_interval = 0.1  # 100ms 批量发送
        self._last_flush = time.time()
        self.setLevel(logging.DEBUG)  # 发送所有级别
    
    def emit(self, record: logging.LogRecord) -> None:
        try:
            log_entry = {
                "level": record.levelname,
                "name": record.name,
                "message": self.format(record),
                "time": record.created
            }
            
            with self._lock:
                self._log_queue.append(log_entry)
                
                # 批量发送：缓冲区满或时间间隔到
                if len(self._log_queue) >= 10 or (time.time() - self._last_flush) > self._flush_interval:
                    self._flush_locked()
        except Exception:
            pass
    
    def _flush_locked(self) -> None:
        """发送日志（必须持有锁）"""
        if not self._log_queue or not self.log_protocol:
            return
        
        try:
            from vibeocr.utils.shared_memory import MSG_LOG, serialize_log_entries
            data = serialize_log_entries(self._log_queue)
            self.log_protocol.write_message(MSG_LOG, data, timeout=0.5)
            self._log_queue.clear()
            self._last_flush = time.time()
        except Exception:
            # 发送失败时丢弃日志，避免阻塞
            self._log_queue.clear()
    
    def flush(self) -> None:
        """强制刷新日志"""
        with self._lock:
            self._flush_locked()
    
    def close(self) -> None:
        """关闭处理器"""
        self.flush()
        super().close()


def run_worker(
    shm_name: str,
    shm_size: int,
    use_gpu: bool,
    log_shm_name: Optional[str] = None,
    log_shm_size: int = 1 * 1024 * 1024
) -> None:
    """运行 Worker 主循环

    Args:
        shm_name: 数据共享内存名称
        shm_size: 数据共享内存大小（字节）
        use_gpu: 是否使用 GPU
        log_shm_name: 日志共享内存名称（可选）
        log_shm_size: 日志共享内存大小（字节）
    """
    # 设置 GPU 环境变量（OCRService 会读取此变量）
    if use_gpu:
        os.environ["VIBEOCR_USE_GPU"] = "true"
    else:
        os.environ["VIBEOCR_USE_GPU"] = "false"

    from vibeocr.utils.shared_memory import (
        SharedMemoryProtocol,
        SharedMemoryProtocolError,
        MSG_RECOGNIZE,
        MSG_RESULT,
        MSG_ERROR,
        MSG_SHUTDOWN,
        MSG_ACK,
        MSG_PRELOAD,
        MSG_PRELOAD_DONE,
        deserialize_request,
        serialize_result,
        deserialize_preload_request,
        serialize_preload_result,
    )

    # 连接数据共享内存
    logger.info(f"Worker 正在连接数据共享内存: {shm_name}")
    protocol = SharedMemoryProtocol(shm_name, shm_size)
    log_protocol = None
    log_handler = None

    try:
        protocol.connect()
    except Exception as e:
        logger.error(f"连接数据共享内存失败: {e}")
        raise OCRWorkerError(f"连接数据共享内存失败: {e}")

    # 连接日志共享内存（如果提供）
    if log_shm_name:
        try:
            logger.info(f"Worker 正在连接日志共享内存: {log_shm_name}")
            log_protocol = SharedMemoryProtocol(log_shm_name, log_shm_size)
            log_protocol.connect()
            
            # 添加共享内存日志处理器
            log_handler = SharedMemoryLogHandler(log_protocol)
            log_handler.setFormatter(logging.Formatter("%(message)s"))
            logging.getLogger().addHandler(log_handler)
            logger.info("日志共享内存已连接")
        except Exception as e:
            logger.warning(f"连接日志共享内存失败: {e}，将使用本地日志")
            log_protocol = None

    # 初始化 OCR 服务（延迟导入以避免启动时的重型依赖）
    logger.info("正在初始化 OCR 服务...")
    try:
        from vibeocr.services.ocr_service import OCRService, OCROptions
        ocr_service = OCRService()
        logger.info("OCR 服务初始化完成")
    except Exception as e:
        logger.error(f"OCR 服务初始化失败: {e}")
        protocol.close()
        raise OCRWorkerError(f"OCR 服务初始化失败: {e}")

    # 发送就绪信号
    try:
        protocol.write_message(MSG_ACK, b"READY", timeout=5.0)
        logger.info("Worker 已就绪，等待任务...")
    except SharedMemoryProtocolError as e:
        logger.error(f"发送就绪信号失败: {e}")
        protocol.close()
        raise

    # 主循环
    try:
        while True:
            try:
                # 等待消息（长超时，因为可能需要等待用户操作）
                msg_type, data = protocol.read_message(timeout=300.0)

                if msg_type == MSG_RECOGNIZE:
                    # 处理识别请求
                    logger.debug("收到识别请求")
                    try:
                        # 反序列化请求
                        image_data, options_dict = deserialize_request(data)
                        options = OCROptions(**options_dict)

                        # 执行识别
                        result = ocr_service.recognize(image_data, options)

                        # 发送结果
                        result_bytes = serialize_result(result)
                        protocol.write_message(MSG_RESULT, result_bytes)
                        logger.debug("识别完成，结果已发送")

                    except Exception as e:
                        # 发送错误
                        error_msg = f"{type(e).__name__}: {str(e)}"
                        logger.error(f"识别失败: {error_msg}")
                        protocol.write_message(MSG_ERROR, error_msg.encode("utf-8"))

                elif msg_type == MSG_SHUTDOWN:
                    # 收到关闭信号
                    logger.info("收到关闭信号，退出")
                    break

                elif msg_type == MSG_PRELOAD:
                    # 处理预加载请求
                    logger.info("收到预加载请求")
                    try:
                        # 反序列化请求
                        pipelines = deserialize_preload_request(data)
                        logger.info(f"预加载管道: {pipelines}")
                        
                        results = {}
                        for pipeline_name in pipelines:
                            try:
                                # 预加载管道
                                success = ocr_service.preload_pipeline(pipeline_name)
                                results[pipeline_name] = success
                                logger.info(f"预加载 {pipeline_name}: {'成功' if success else '失败'}")
                            except Exception as e:
                                results[pipeline_name] = False
                                logger.error(f"预加载 {pipeline_name} 失败: {e}")
                        
                        # 发送预加载结果
                        result_bytes = serialize_preload_result(results)
                        protocol.write_message(MSG_PRELOAD_DONE, result_bytes)
                        logger.info(f"预加载完成: {results}")

                    except Exception as e:
                        # 发送错误
                        error_msg = f"{type(e).__name__}: {str(e)}"
                        logger.error(f"预加载失败: {error_msg}")
                        protocol.write_message(MSG_ERROR, error_msg.encode("utf-8"))

                elif msg_type == MSG_ACK:
                    # 心跳/确认，忽略
                    pass

                else:
                    logger.warning(f"未知消息类型: {msg_type}")

            except SharedMemoryProtocolError as e:
                if "超时" in str(e):
                    # 读取超时，继续等待
                    continue
                else:
                    logger.error(f"通信错误: {e}")
                    break

    except KeyboardInterrupt:
        logger.info("收到中断信号，退出")

    finally:
        # 关闭日志处理器
        if log_handler:
            try:
                log_handler.flush()
                logging.getLogger().removeHandler(log_handler)
                log_handler.close()
            except Exception:
                pass
        
        # 关闭日志共享内存
        if log_protocol:
            try:
                log_protocol.close()
            except Exception:
                pass
        
        # 关闭数据共享内存
        protocol.close()
        logger.info("Worker 已退出")


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="OCR Worker 子进程",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--shm-name",
        required=True,
        help="数据共享内存名称"
    )
    parser.add_argument(
        "--shm-size",
        type=int,
        default=10 * 1024 * 1024,
        help="数据共享内存大小（字节），默认 10MB"
    )
    parser.add_argument(
        "--log-shm-name",
        default=None,
        help="日志共享内存名称（可选）"
    )
    parser.add_argument(
        "--log-shm-size",
        type=int,
        default=1 * 1024 * 1024,
        help="日志共享内存大小（字节），默认 1MB"
    )
    parser.add_argument(
        "--use-gpu",
        action="store_true",
        default=False,
        help="使用 GPU 加速"
    )
    parser.add_argument(
        "--no-gpu",
        dest="use_gpu",
        action="store_false",
        help="不使用 GPU"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="启用调试日志"
    )

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info(f"启动 OCR Worker: shm_name={args.shm_name}, shm_size={args.shm_size}, use_gpu={args.use_gpu}")
    if args.log_shm_name:
        logger.info(f"日志共享内存: {args.log_shm_name}, size={args.log_shm_size}")

    try:
        run_worker(
            args.shm_name,
            args.shm_size,
            args.use_gpu,
            args.log_shm_name,
            args.log_shm_size
        )
    except OCRWorkerError as e:
        logger.error(f"Worker 错误: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"未预期的错误: {e}\n{traceback.format_exc()}")
        sys.exit(1)


if __name__ == "__main__":
    main()

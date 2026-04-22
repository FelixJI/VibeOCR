#!/usr/bin/env python
"""MinerU Worker 子进程脚本

轻量子进程，只负责文档解析（DOCUMENT_PARSING）。
不加载 paddle，不拦截 torch，通过 httpx 调用 mineru-api 孙进程。

使用方式:
    python -m vibeocr.workers.mineru_worker --shm-name <name> --shm-size <size>
"""

import argparse
import logging
import sys
import time
import traceback

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


class MinerUWorkerError(Exception):
    """MinerU Worker 错误"""


def run_worker(shm_name: str, shm_size: int) -> None:
    """运行 MinerU Worker 主循环

    Args:
        shm_name: 数据共享内存名称
        shm_size: 数据共享内存大小（字节）
    """
    from vibeocr.utils.shared_memory_v2 import (
        MessageType,
        SharedMemoryProtocolError,
        deserialize_batch_commit,
        deserialize_batch_request,
        deserialize_request,
        serialize_batch_progress,
        serialize_batch_result,
        serialize_result,
    )
    from vibeocr.utils.shared_memory_v2 import (
        SharedMemoryProtocolV2 as SharedMemoryProtocol,
    )

    MSG_RECOGNIZE = MessageType.RECOGNIZE
    MSG_RESULT = MessageType.RESULT
    MSG_ERROR = MessageType.ERROR
    MSG_SHUTDOWN = MessageType.SHUTDOWN
    MSG_ACK = MessageType.ACK
    MSG_READY = MessageType.READY
    MSG_BATCH_ADD = MessageType.BATCH_ADD
    MSG_BATCH_COMMIT = MessageType.BATCH_COMMIT
    MSG_BATCH_RESULT = MessageType.BATCH_RESULT
    MSG_BATCH_CANCEL = MessageType.BATCH_CANCEL
    MSG_BATCH_PROGRESS = MessageType.BATCH_PROGRESS

    logger.info(f"MinerU Worker 正在连接数据共享内存: {shm_name}")
    protocol = SharedMemoryProtocol(shm_name, shm_size)

    try:
        protocol.connect()
    except Exception as e:
        logger.error(f"连接数据共享内存失败: {e}")
        raise MinerUWorkerError(f"连接数据共享内存失败: {e}") from None

    logger.info("正在初始化 MinerU 服务...")
    try:
        from vibeocr.services.mineru_service import MinerUService
        from vibeocr.models.ocr_options import OCROptions

        mineru_service = MinerUService()
        logger.info("MinerU 服务初始化完成")
    except Exception as e:
        logger.error(f"MinerU 服务初始化失败: {e}")
        protocol.close()
        raise MinerUWorkerError(f"MinerU 服务初始化失败: {e}") from None

    from vibeocr.services.mineru_batch_service import MinerUBatchService

    batch_service: MinerUBatchService | None = None

    def get_batch_service() -> MinerUBatchService:
        nonlocal batch_service
        if batch_service is None:
            batch_service = MinerUBatchService()
        return batch_service

    logger.info("[MinerU Worker] 发送 READY 信号...")
    try:
        protocol.write_message(MSG_READY, b"READY", timeout=5.0)
        logger.info("[MinerU Worker] READY 信号已发送")
        time.sleep(0.1)
    except SharedMemoryProtocolError as e:
        logger.error(f"[MinerU Worker] 发送就绪信号失败: {e}")
        protocol.close()
        raise

    try:
        while True:
            try:
                msg_type, data = protocol.read_message(
                    timeout=300.0, expected_sender="main"
                )

                if msg_type == MSG_RECOGNIZE:
                    logger.info("[MinerU Worker] 收到识别请求")
                    try:
                        image_data, options_dict = deserialize_request(data)
                        logger.info(
                            f"[MinerU Worker] 数据大小: {len(image_data)} 字节, 选项: {options_dict}"
                        )
                        options = OCROptions.from_dict(options_dict)

                        mime_type = options_dict.get("mime_type", "application/pdf")
                        if not mime_type or mime_type == "image/png":
                            file_path = options_dict.get("file_path", "")
                            if file_path:
                                from pathlib import Path

                                suffix = Path(file_path).suffix.lower()
                                mime_map = {
                                    ".pdf": "application/pdf",
                                    ".png": "image/png",
                                    ".jpg": "image/jpeg",
                                    ".jpeg": "image/jpeg",
                                    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                }
                                mime_type = mime_map.get(suffix, "application/pdf")
                            else:
                                mime_type = "application/pdf"

                        result = mineru_service.parse(image_data, mime_type, options)
                        logger.info(
                            f"[MinerU Worker] 解析完成，结果字符数: {len(result.raw_text) if hasattr(result, 'raw_text') else 'N/A'}"
                        )

                        result_bytes = serialize_result(result)
                        protocol.write_message(
                            MSG_RESULT, result_bytes, sender="worker"
                        )
                        logger.info("[MinerU Worker] 结果已发送")
                        protocol.wait_for_read(timeout=5.0)

                    except Exception as e:
                        error_msg = f"{type(e).__name__}: {e!s}"
                        logger.error(f"文档解析失败: {error_msg}")
                        protocol.write_message(
                            MSG_ERROR, error_msg.encode("utf-8"), sender="worker"
                        )

                elif msg_type == MSG_BATCH_ADD:
                    logger.info("[MinerU Worker] 收到批量添加请求")
                    try:
                        request_id, image_data, options_dict = (
                            deserialize_batch_request(data)
                        )
                        logger.info(f"[MinerU Worker] 批量添加: {request_id}")

                        svc = get_batch_service()
                        file_name = options_dict.get("file_name", "unknown")
                        svc.batch_add(image_data, options=None, file_name=file_name)
                        protocol.write_message(
                            MSG_ACK, request_id.encode(), sender="worker"
                        )
                    except Exception as e:
                        error_msg = f"批量添加失败: {e}"
                        logger.error(error_msg)
                        protocol.write_message(
                            MSG_ERROR, error_msg.encode("utf-8"), sender="worker"
                        )

                elif msg_type == MSG_BATCH_COMMIT:
                    logger.info("[MinerU Worker] 收到批量提交请求")
                    try:
                        preprocess_dict = deserialize_batch_commit(data)
                        from vibeocr.models.batch_request import PreprocessOptions
                        from vibeocr.models.ocr_options import OCROptions

                        preprocess_options = PreprocessOptions.from_dict(
                            preprocess_dict
                        )

                        svc = get_batch_service()

                        def progress_callback(completed, total, current_file):
                            try:
                                progress_data = serialize_batch_progress(
                                    completed=completed,
                                    total=total,
                                    current_file=current_file,
                                )
                                protocol.write_message(
                                    MSG_BATCH_PROGRESS,
                                    progress_data,
                                    sender="worker",
                                )
                            except Exception as e:
                                logger.warning(f"发送进度失败: {e}")

                        ocr_options = None
                        if hasattr(preprocess_options, "pipeline"):
                            ocr_options = OCROptions(
                                pipeline=preprocess_options.pipeline
                            )

                        results = svc.batch_commit(
                            preprocess_options=ocr_options,
                            timeout=300.0,
                            progress_callback=progress_callback,
                        )

                        results_data = serialize_batch_result(results)
                        protocol.write_message(
                            MSG_BATCH_RESULT, results_data, sender="worker"
                        )
                        logger.info(
                            f"[MinerU Worker] 批量处理完成，返回 {len(results)} 个结果"
                        )
                    except Exception as e:
                        error_msg = f"批量处理失败: {e}"
                        logger.error(error_msg)
                        protocol.write_message(
                            MSG_ERROR, error_msg.encode("utf-8"), sender="worker"
                        )

                elif msg_type == MSG_BATCH_CANCEL:
                    logger.info("[MinerU Worker] 收到取消请求")
                    if batch_service:
                        batch_service.batch_cancel()
                    protocol.write_message(MSG_ACK, b"cancelled", sender="worker")

                elif msg_type == MSG_SHUTDOWN:
                    logger.info("收到关闭信号，退出")
                    break

                elif msg_type == MSG_READY:
                    logger.debug("[MinerU Worker] 读取到自己的 READY 消息，跳过")
                    continue

                elif msg_type in (MSG_RESULT,):
                    logger.debug(
                        f"[MinerU Worker] 读取到响应类型消息 {msg_type.decode('ascii', errors='replace')}，跳过"
                    )
                    continue

                else:
                    logger.warning(
                        f"未知消息类型: {msg_type.decode('ascii', errors='replace')}"
                    )

            except SharedMemoryProtocolError as e:
                if "超时" in str(e):
                    continue
                logger.error(f"通信错误: {e}")
                break

    except KeyboardInterrupt:
        logger.info("收到中断信号，退出")

    finally:
        protocol.close()
        logger.info("MinerU Worker 已退出")


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="MinerU Worker 子进程",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--shm-name", required=True, help="数据共享内存名称")
    parser.add_argument(
        "--shm-size",
        type=int,
        default=10 * 1024 * 1024,
        help="数据共享内存大小（字节），默认 10MB",
    )
    parser.add_argument(
        "--use-gpu", action="store_true", default=False, help="兼容参数，MinerU Worker 不使用"
    )
    parser.add_argument(
        "--no-gpu", dest="use_gpu", action="store_false", help="兼容参数，MinerU Worker 不使用"
    )
    parser.add_argument("--debug", action="store_true", help="启用调试日志")

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info(
        f"启动 MinerU Worker: shm_name={args.shm_name}, shm_size={args.shm_size}"
    )

    try:
        run_worker(args.shm_name, args.shm_size)
    except MinerUWorkerError as e:
        logger.error(f"MinerU Worker 错误: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"未预期的错误: {e}\n{traceback.format_exc()}")
        sys.exit(1)


if __name__ == "__main__":
    main()

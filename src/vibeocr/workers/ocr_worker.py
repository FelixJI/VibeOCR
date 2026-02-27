#!/usr/bin/env python
"""OCR Worker 子进程脚本

作为独立子进程运行，通过共享内存与主进程通信。
负责执行 OCR 识别任务。

使用方式:
    python -m vibeocr.workers.ocr_worker --shm-name <name> --shm-size <size> --use-gpu
"""

import argparse
import logging
import os
import sys
import traceback

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


class OCRWorkerError(Exception):
    """OCR Worker 错误"""
    pass


def run_worker(shm_name: str, shm_size: int, use_gpu: bool) -> None:
    """运行 Worker 主循环

    Args:
        shm_name: 共享内存名称
        shm_size: 共享内存大小（字节）
        use_gpu: 是否使用 GPU
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
        deserialize_request,
        serialize_result,
    )

    # 连接共享内存
    logger.info(f"Worker 正在连接共享内存: {shm_name}")
    protocol = SharedMemoryProtocol(shm_name, shm_size)

    try:
        protocol.connect()
    except Exception as e:
        logger.error(f"连接共享内存失败: {e}")
        raise OCRWorkerError(f"连接共享内存失败: {e}")

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
        help="共享内存名称"
    )
    parser.add_argument(
        "--shm-size",
        type=int,
        default=10 * 1024 * 1024,
        help="共享内存大小（字节），默认 10MB"
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

    try:
        run_worker(args.shm_name, args.shm_size, args.use_gpu)
    except OCRWorkerError as e:
        logger.error(f"Worker 错误: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"未预期的错误: {e}\n{traceback.format_exc()}")
        sys.exit(1)


if __name__ == "__main__":
    main()

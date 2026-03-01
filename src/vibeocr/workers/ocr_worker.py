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
import time
import traceback

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


def run_worker(
    shm_name: str,
    shm_size: int,
    use_gpu: bool
) -> None:
    """运行 Worker 主循环

    Args:
        shm_name: 数据共享内存名称
        shm_size: 数据共享内存大小（字节）
        use_gpu: 是否使用 GPU
    """
    # 设置 GPU 环境变量（OCRService 会读取此变量）
    if use_gpu:
        os.environ["VIBEOCR_USE_GPU"] = "true"
    else:
        os.environ["VIBEOCR_USE_GPU"] = "false"

    from vibeocr.utils.shared_memory_v2 import (
        SharedMemoryProtocolV2 as SharedMemoryProtocol,
        SharedMemoryProtocolError,
        MessageType,
        SharedMemoryConfig,
        deserialize_request,
        serialize_result,
        deserialize_preload_request,
        serialize_preload_result,
    )
    
    # 消息类型别名（保持兼容）
    MSG_RECOGNIZE = MessageType.RECOGNIZE
    MSG_RESULT = MessageType.RESULT
    MSG_ERROR = MessageType.ERROR
    MSG_SHUTDOWN = MessageType.SHUTDOWN
    MSG_ACK = MessageType.ACK
    MSG_PRELOAD = MessageType.PRELOAD
    MSG_PRELOAD_DONE = MessageType.PRELOAD_DONE
    MSG_READY = MessageType.READY

    # 连接数据共享内存
    logger.info(f"Worker 正在连接数据共享内存: {shm_name}")
    protocol = SharedMemoryProtocol(shm_name, shm_size)

    try:
        protocol.connect()
    except Exception as e:
        logger.error(f"连接数据共享内存失败: {e}")
        raise OCRWorkerError(f"连接数据共享内存失败: {e}")

    # 初始化 OCR 服务（延迟导入以避免启动时的重型依赖）
    logger.info("正在初始化 OCR 服务...")
    try:
        from vibeocr.services.ocr_service import OCRService, OCROptions, OCRPipeline
        ocr_service = OCRService()
        logger.info("OCR 服务初始化完成")
    except Exception as e:
        logger.error(f"OCR 服务初始化失败: {e}")
        protocol.close()
        raise OCRWorkerError(f"OCR 服务初始化失败: {e}")

    # 发送就绪信号（简化握手：单次发送 READY）
    logger.info("[Worker] 发送 READY 信号...")
    try:
        protocol.write_message(MSG_READY, b"READY", timeout=5.0)
        logger.info("[Worker] READY 信号已发送")
        
        # V2 版本：发送后直接进入主循环
        # 主进程会在 read_message 中自动清除状态
        time.sleep(0.1)  # 短暂等待确保消息写入
            
    except SharedMemoryProtocolError as e:
        logger.error(f"[Worker] 发送就绪信号失败: {e}")
        protocol.close()
        raise

    # 主循环
    try:
        while True:
            try:
                # 等待消息（长超时，因为可能需要等待用户操作）
                # Worker 只读取主进程发送的消息
                msg_type, data = protocol.read_message(timeout=300.0, expected_sender='main')

                if msg_type == MSG_RECOGNIZE:
                    # 处理识别请求
                    logger.info("[Worker] 收到识别请求")
                    try:
                        # 反序列化请求
                        image_data, options_dict = deserialize_request(data)
                        logger.info(f"[Worker] 图像大小: {len(image_data)} 字节, 选项: {options_dict}")
                        options = OCROptions(**options_dict)

                        # 执行识别
                        logger.info("[Worker] 开始执行 OCR 识别...")
                        result = ocr_service.recognize(image_data, options)
                        logger.info(f"[Worker] OCR 识别完成，结果字符数: {len(result.raw_text) if hasattr(result, 'raw_text') else 'N/A'}")

                        # 发送结果
                        result_bytes = serialize_result(result)
                        protocol.write_message(MSG_RESULT, result_bytes, sender='worker')
                        logger.info("[Worker] 识别结果已发送")
                        # 等待主进程读取响应
                        protocol.wait_for_read(timeout=5.0)

                    except Exception as e:
                        # 发送错误
                        error_msg = f"{type(e).__name__}: {str(e)}"
                        logger.error(f"识别失败: {error_msg}")
                        protocol.write_message(MSG_ERROR, error_msg.encode("utf-8"), sender='worker')

                elif msg_type == MSG_SHUTDOWN:
                    # 收到关闭信号
                    logger.info("收到关闭信号，退出")
                    break

                elif msg_type == MSG_PRELOAD:
                    # 处理预加载请求
                    logger.info("收到预加载请求")
                    try:
                        # 反序列化请求
                        pipeline_names = deserialize_preload_request(data)
                        logger.info(f"预加载管道: {pipeline_names}")

                        results = {}
                        for pipeline_name in pipeline_names:
                            try:
                                # 将字符串转换为 OCRPipeline 枚举
                                pipeline_enum = None
                                for p in OCRPipeline:
                                    if p.value == pipeline_name:
                                        pipeline_enum = p
                                        break

                                if pipeline_enum is None:
                                    logger.error(f"未知的管道名称: {pipeline_name}")
                                    results[pipeline_name] = False
                                    continue

                                # 预加载管道
                                success = ocr_service.preload_pipeline(pipeline_enum)
                                results[pipeline_name] = success
                                logger.info(f"预加载 {pipeline_name}: {'成功' if success else '失败'}")
                            except Exception as e:
                                results[pipeline_name] = False
                                logger.error(f"预加载 {pipeline_name} 失败: {e}")

                        # 发送预加载结果
                        logger.info(f"[Worker] 准备发送预加载结果: {results}")
                        result_bytes = serialize_preload_result(results)
                        logger.info(f"[Worker] 序列化完成，大小: {len(result_bytes)} 字节")
                        protocol.write_message(MSG_PRELOAD_DONE, result_bytes, sender='worker')
                        logger.info(f"[Worker] 预加载结果已发送: {results}")
                        # 等待主进程读取响应，避免自己读取到刚发送的消息
                        protocol.wait_for_read(timeout=5.0)

                    except Exception as e:
                        # 发送错误
                        error_msg = f"{type(e).__name__}: {str(e)}"
                        logger.error(f"预加载失败: {error_msg}")
                        protocol.write_message(MSG_ERROR, error_msg.encode("utf-8"), sender='worker')

                elif msg_type == MSG_READY:
                    # Worker 不应该读取到自己的 READY 消息
                    # 如果读取到，说明是之前残留的消息，跳过
                    logger.debug("[Worker] 读取到自己的 READY 消息，跳过")
                    continue

                elif msg_type == MSG_ACK:
                    # 心跳/确认，忽略
                    pass

                elif msg_type in (MSG_RESULT, MSG_PRELOAD_DONE):
                    # 这些是响应消息，Worker 不应该读取到
                    # 如果读取到，说明是自己刚发送的响应，跳过
                    logger.debug(f"[Worker] 读取到响应类型消息 {msg_type}，跳过")
                    continue

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
        run_worker(
            args.shm_name,
            args.shm_size,
            args.use_gpu
        )
    except OCRWorkerError as e:
        logger.error(f"Worker 错误: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"未预期的错误: {e}\n{traceback.format_exc()}")
        sys.exit(1)


if __name__ == "__main__":
    main()

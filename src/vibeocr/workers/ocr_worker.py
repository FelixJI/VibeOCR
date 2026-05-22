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

# 跳过模型源网络检测，避免推理时网络超时
os.environ["PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK"] = "True"

# Worker 只使用 PaddlePaddle 推理，不需要 PyTorch。
# 但 paddlex 的依赖链 (modelscope) 会在导入时探测 torch，
# 而 torch 与 paddle 的 CUDA 运行时 DLL 在 Windows 上互相冲突。
# 双重拦截：
#   1. sys.meta_path hook 阻止 import torch 加载实际 DLL
#   2. patch importlib.util.find_spec 让探测调用返回 None，
#      否则 find_spec 内部触发 hook 的 raise ImportError 会向上传播，
#      导致 modelscope 的探测代码崩溃（而非走 no-torch 降级路径）
import importlib.abc
import importlib.util


class _TorchBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname == "torch" or fullname.startswith("torch."):
            raise ImportError(f"torch blocked in PaddleX worker: {fullname}")


sys.meta_path.insert(0, _TorchBlocker())

_orig_find_spec = importlib.util.find_spec


def _patched_find_spec(name, package=None):
    if name == "torch" or name.startswith("torch."):
        return None
    return _orig_find_spec(name, package)


importlib.util.find_spec = _patched_find_spec

# 配置基本日志（在添加共享内存处理器之前）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


class OCRWorkerError(Exception):
    """OCR Worker 错误"""


def run_worker(shm_name: str, shm_size: int, use_gpu: bool) -> None:
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
        MessageType,
        SharedMemoryProtocolError,
        deserialize_batch_commit,
        deserialize_batch_request,
        deserialize_preload_request,
        deserialize_request,
        serialize_batch_progress,
        serialize_batch_result,
        serialize_preload_result,
        serialize_result,
    )
    from vibeocr.utils.shared_memory_v2 import (
        SharedMemoryProtocolV2 as SharedMemoryProtocol,  # 批量消息序列化函数
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
    # 批量消息类型别名
    MSG_BATCH_ADD = MessageType.BATCH_ADD
    MSG_BATCH_COMMIT = MessageType.BATCH_COMMIT
    MSG_BATCH_RESULT = MessageType.BATCH_RESULT
    MSG_BATCH_CANCEL = MessageType.BATCH_CANCEL
    MSG_BATCH_PROGRESS = MessageType.BATCH_PROGRESS
    MSG_BATCH_FILE_DONE = MessageType.BATCH_FILE_DONE

    # 连接数据共享内存
    logger.info(f"Worker 正在连接数据共享内存: {shm_name}")
    protocol = SharedMemoryProtocol(shm_name, shm_size)

    try:
        protocol.connect()
    except Exception as e:
        logger.error(f"连接数据共享内存失败: {e}")
        raise OCRWorkerError(f"连接数据共享内存失败: {e}") from None

    # 初始化 OCR 服务（延迟导入以避免启动时的重型依赖）
    logger.info("正在初始化 OCR 服务...")
    try:
        from vibeocr.services.ocr_service import OCROptions, OCRPipeline, OCRService

        ocr_service = OCRService()
        logger.info("OCR 服务初始化完成")
    except Exception as e:
        logger.error(f"OCR 服务初始化失败: {e}")
        protocol.close()
        raise OCRWorkerError(f"OCR 服务初始化失败: {e}") from None

    # 批量队列管理器（延迟初始化，仅在首次使用时创建）
    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from vibeocr.workers.batch_queue_manager import BatchQueueManager

    batch_managers: dict[
        str, BatchQueueManager | None
    ] = {}  # 管道名称 -> BatchQueueManager
    PreprocessOptions = None  # 预先定义，避免未绑定错误
    batch_manager_initialized = False  # 标记是否已尝试初始化

    def get_batch_manager(pipeline_name: str = "OCR"):
        """获取批量队列管理器（延迟初始化）

        Args:
            pipeline_name: 管道名称
        """
        nonlocal batch_managers, batch_manager_initialized

        from enum import Enum

        if isinstance(pipeline_name, Enum):
            pipeline_name = pipeline_name.value

        if pipeline_name in batch_managers:
            return batch_managers[pipeline_name]

        if not batch_manager_initialized:
            batch_manager_initialized = True
            try:
                from vibeocr.models.batch_request import (
                    PreprocessOptions as _PreprocessOptions,
                )

                nonlocal PreprocessOptions
                PreprocessOptions = _PreprocessOptions
            except Exception as e:
                logger.warning(f"[Worker] 加载 PreprocessOptions 失败: {e}")

        try:
            from vibeocr.workers.batch_queue_manager import BatchQueueManager

            logger.debug(f"[Worker] 正在初始化批量队列管理器（{pipeline_name}）...")
            pipeline = ocr_service.get_pipeline(
                OCRPipeline(pipeline_name)
                if any(p.value == pipeline_name for p in OCRPipeline)
                else OCRPipeline.OCR
            )
            batch_managers[pipeline_name] = BatchQueueManager(
                pipeline, max_batch_size=4
            )
            logger.debug(
                f"[Worker] BatchQueueManager 初始化完成（使用 {pipeline_name}）"
            )

        except Exception as e:
            logger.warning(
                f"[Worker] BatchQueueManager 初始化失败: {e}，批量功能将不可用"
            )
            batch_managers[pipeline_name] = None

        return batch_managers.get(pipeline_name)

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
                msg_type, data = protocol.read_message(
                    timeout=300.0, expected_sender="main"
                )

                if msg_type == MSG_RECOGNIZE:
                    # 处理识别请求
                    logger.debug("[Worker] 收到识别请求")
                    try:
                        # 反序列化请求
                        image_data, options_dict = deserialize_request(data)
                        logger.debug(
                            f"[Worker] 图像大小: {len(image_data)} 字节, 选项: {options_dict}"
                        )
                        # 使用 from_dict 正确处理 pipeline 字符串到枚举的转换
                        options = OCROptions.from_dict(options_dict)

                        # 执行识别
                        logger.debug("[Worker] 开始执行 OCR 识别...")
                        result = ocr_service.recognize(image_data, options)
                        logger.debug(
                            f"[Worker] OCR 识别完成，结果字符数: {len(result.raw_text) if hasattr(result, 'raw_text') else 'N/A'}"
                        )

                        # 发送结果
                        result_bytes = serialize_result(result)
                        protocol.write_message(
                            MSG_RESULT, result_bytes, sender="worker"
                        )
                        logger.debug("[Worker] 识别结果已发送")
                        # 等待主进程读取响应
                        protocol.wait_for_read(timeout=5.0)

                    except Exception as e:
                        # 发送错误
                        error_msg = f"{type(e).__name__}: {e!s}"
                        logger.error(f"识别失败: {error_msg}")
                        protocol.write_message(
                            MSG_ERROR, error_msg.encode("utf-8"), sender="worker"
                        )
                        # 等待主进程读取错误响应，避免读回自己的消息
                        protocol.wait_for_read(timeout=5.0)

                elif msg_type == MSG_SHUTDOWN:
                    # 收到关闭信号
                    logger.info("收到关闭信号，退出")
                    break

                elif msg_type == MSG_PRELOAD:
                    # 处理预加载请求
                    logger.debug("收到预加载请求")
                    try:
                        # 反序列化请求
                        pipeline_names = deserialize_preload_request(data)
                        logger.debug(f"预加载管道: {pipeline_names}")

                        results = {}
                        for pipeline_name in pipeline_names:
                            try:
                                # 将字符串转换为 OCRPipeline 枚举（支持大小写不敏感匹配）
                                pipeline_enum = None
                                pipeline_name_lower = pipeline_name.lower()
                                for p in OCRPipeline:
                                    if p.value.lower() == pipeline_name_lower:
                                        pipeline_enum = p
                                        break

                                if pipeline_enum is None:
                                    logger.error(f"未知的管道名称: {pipeline_name}")
                                    results[pipeline_name] = False
                                    continue

                                # 预加载管道
                                logger.debug(
                                    f"开始预加载模型: {pipeline_name} ({pipeline_enum.value})"
                                )
                                success = ocr_service.preload_pipeline(pipeline_enum)
                                results[pipeline_name] = success
                                logger.debug(
                                    f"预加载 {pipeline_name}: {'成功' if success else '失败'}"
                                )
                            except Exception as e:
                                results[pipeline_name] = False
                                logger.error(f"预加载 {pipeline_name} 失败: {e}")

                        # 发送预加载结果
                        logger.debug(f"[Worker] 准备发送预加载结果: {results}")
                        result_bytes = serialize_preload_result(results)
                        logger.debug(
                            f"[Worker] 序列化完成，大小: {len(result_bytes)} 字节"
                        )
                        protocol.write_message(
                            MSG_PRELOAD_DONE, result_bytes, sender="worker"
                        )
                        logger.debug(f"[Worker] 预加载结果已发送: {results}")
                        # 等待主进程读取响应，避免自己读取到刚发送的消息
                        protocol.wait_for_read(timeout=5.0)

                    except Exception as e:
                        # 发送错误
                        error_msg = f"{type(e).__name__}: {e!s}"
                        logger.error(f"预加载失败: {error_msg}")
                        protocol.write_message(
                            MSG_ERROR, error_msg.encode("utf-8"), sender="worker"
                        )

                elif msg_type == MSG_BATCH_ADD:
                    # 处理批量添加请求
                    logger.debug("[Worker] 收到批量添加请求")
                    try:
                        request_id, image_data, options_dict = (
                            deserialize_batch_request(data)
                        )
                        logger.debug(f"[Worker] 批量添加: {request_id}")

                        # 从选项中获取管道名称
                        pipeline_name = options_dict.get("pipeline", "OCR")

                        # 延迟初始化批量管理器
                        mgr = get_batch_manager(pipeline_name)
                        if mgr:
                            # 添加到队列
                            mgr.add_request(
                                image_data=image_data,
                                options=options_dict,
                                file_name=options_dict.get("file_name", "unknown"),
                            )
                            # 发送确认
                            protocol.write_message(
                                MSG_ACK, request_id.encode(), sender="worker"
                            )
                        else:
                            protocol.write_message(
                                MSG_ERROR,
                                b"BatchQueueManager not available",
                                sender="worker",
                            )
                    except Exception as e:
                        error_msg = f"批量添加失败: {e}"
                        logger.error(error_msg)
                        protocol.write_message(
                            MSG_ERROR, error_msg.encode(), sender="worker"
                        )

                elif msg_type == MSG_BATCH_COMMIT:
                    # 处理批量提交
                    logger.debug("[Worker] 收到批量提交请求")
                    try:
                        preprocess_dict = deserialize_batch_commit(data)
                        # 确保 PreprocessOptions 已加载
                        if PreprocessOptions is None:
                            from vibeocr.models.batch_request import (
                                PreprocessOptions as _PreprocessOptions,
                            )

                            PreprocessOptions = _PreprocessOptions

                        preprocess_options = PreprocessOptions.from_dict(
                            preprocess_dict
                        )

                        # 获取管道名称
                        pipeline_name = preprocess_options.pipeline.value

                        # 延迟初始化批量管理器
                        mgr = get_batch_manager(pipeline_name)
                        if mgr:
                            # 定义进度回调
                            def progress_callback(progress):
                                try:
                                    progress_data = serialize_batch_progress(
                                        completed=progress.completed,
                                        total=progress.total,
                                        current_file=progress.current_file,
                                    )
                                    protocol.write_message(
                                        MSG_BATCH_PROGRESS,
                                        progress_data,
                                        sender="worker",
                                    )
                                except Exception as e:
                                    logger.warning(f"发送进度失败: {e}")

                            # 定义单文件完成回调（流式返回结果）
                            def on_file_done(request_id, result):
                                try:
                                    file_data = serialize_batch_result(
                                        {request_id: result}
                                    )
                                    protocol.write_message(
                                        MSG_BATCH_FILE_DONE,
                                        file_data,
                                        sender="worker",
                                        timeout=30.0,
                                    )
                                except Exception as e:
                                    logger.warning(f"发送单文件结果失败: {e}")

                            mgr.progress_callback = progress_callback

                            # 执行批量处理（带流式回调）
                            results = mgr.commit(
                                preprocess_options,
                                file_completed_callback=on_file_done,
                            )

                            # 发送最终汇总结果
                            results_data = serialize_batch_result(results)
                            protocol.write_message(
                                MSG_BATCH_RESULT, results_data, sender="worker"
                            )
                            logger.debug(
                                f"[Worker] 批量处理完成，返回 {len(results)} 个结果"
                            )
                        else:
                            protocol.write_message(
                                MSG_ERROR,
                                b"BatchQueueManager not available",
                                sender="worker",
                            )
                    except Exception as e:
                        error_msg = f"批量处理失败: {e}"
                        logger.error(error_msg)
                        protocol.write_message(
                            MSG_ERROR, error_msg.encode(), sender="worker"
                        )

                elif msg_type == MSG_BATCH_CANCEL:
                    # 取消批量处理
                    logger.debug("[Worker] 收到取消请求")
                    # 取消所有批量管理器
                    for pipeline_name, mgr in batch_managers.items():
                        if mgr:
                            try:
                                mgr.cancel()
                                logger.debug(
                                    f"[Worker] 已取消 {pipeline_name} 的批量处理"
                                )
                            except Exception as e:
                                logger.warning(
                                    f"[Worker] 取消 {pipeline_name} 批量处理失败: {e}"
                                )
                    protocol.write_message(MSG_ACK, b"cancelled", sender="worker")

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
                    logger.debug(
                        f"[Worker] 读取到响应类型消息 {msg_type.decode('ascii', errors='replace')}，跳过"
                    )
                    continue

                else:
                    logger.warning(
                        f"未知消息类型: {msg_type.decode('ascii', errors='replace')}"
                    )

            except SharedMemoryProtocolError as e:
                if "超时" in str(e):
                    # 读取超时，继续等待
                    continue
                logger.error(f"通信错误: {e}")
                break

    except KeyboardInterrupt:
        logger.info("收到中断信号，退出")

    finally:
        # 清理所有批量管理器
        for pipeline_name, mgr in batch_managers.items():
            if mgr:
                try:
                    mgr.close()
                    logger.debug(f"[Worker] BatchQueueManager ({pipeline_name}) 已关闭")
                except Exception as e:
                    logger.warning(
                        f"[Worker] 关闭 BatchQueueManager ({pipeline_name}) 失败: {e}"
                    )
        # 关闭数据共享内存
        protocol.close()
        logger.info("Worker 已退出")


def main():
    """命令行入口"""
    parser = argparse.ArgumentParser(
        description="OCR Worker 子进程",
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
        "--use-gpu", action="store_true", default=False, help="使用 GPU 加速"
    )
    parser.add_argument(
        "--no-gpu", dest="use_gpu", action="store_false", help="不使用 GPU"
    )
    parser.add_argument("--debug", action="store_true", help="启用调试日志")

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    logger.info(
        f"启动 OCR Worker: shm_name={args.shm_name}, shm_size={args.shm_size}, use_gpu={args.use_gpu}"
    )

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

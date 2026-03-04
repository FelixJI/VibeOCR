"""OCR Worker 进程管理器

管理单个 OCR Worker 子进程的生命周期。
支持双共享内存设计：数据通道（OCR请求/结果）和日志通道。
"""

import contextlib
import logging
import subprocess
import threading
import time
import uuid
from collections.abc import Callable

from vibeocr.utils.shared_memory_v2 import (
    MessageType,
    SharedMemoryConfig,
    SharedMemoryProtocolError,
    deserialize_batch_result,
    deserialize_preload_result,
    deserialize_result,
    serialize_preload_request,
    serialize_request,
)
from vibeocr.utils.shared_memory_v2 import (
    SharedMemoryProtocolV2 as SharedMemoryProtocol,  # 批量消息序列化函数
)

# 消息类型别名（保持兼容）
MSG_RECOGNIZE = MessageType.RECOGNIZE
MSG_RESULT = MessageType.RESULT
MSG_ERROR = MessageType.ERROR
MSG_ACK = MessageType.ACK
MSG_READY = MessageType.READY
MSG_PRELOAD = MessageType.PRELOAD
MSG_PRELOAD_DONE = MessageType.PRELOAD_DONE
MSG_SHUTDOWN = MessageType.SHUTDOWN
# 批量消息类型别名
MSG_BATCH_ADD = MessageType.BATCH_ADD
MSG_BATCH_COMMIT = MessageType.BATCH_COMMIT
MSG_BATCH_RESULT = MessageType.BATCH_RESULT
MSG_BATCH_CANCEL = MessageType.BATCH_CANCEL
MSG_BATCH_PROGRESS = MessageType.BATCH_PROGRESS

logger = logging.getLogger(__name__)


class OCRWorkerProcessError(Exception):
    """OCR Worker 进程错误"""

    pass


class OCRWorkerProcess:
    """单个 OCR Worker 进程管理器

    负责启动、停止和与 Worker 子进程通信。

    使用示例:
        worker = OCRWorkerProcess(worker_id=0, use_gpu=True)
        worker.start()

        # 执行识别
        result = worker.recognize(image_data, options_dict)

        # 停止 Worker
        worker.stop()
    """

    def __init__(
        self, worker_id: int, use_gpu: bool = True, shm_size: int = 10 * 1024 * 1024
    ):
        """初始化 Worker 进程管理器

        Args:
            worker_id: Worker 标识符
            use_gpu: 是否使用 GPU
            shm_size: 数据共享内存大小（字节）
        """
        self.worker_id = worker_id
        self.use_gpu = use_gpu
        self.shm_size = shm_size

        # 生成唯一的共享内存名称
        unique_id = uuid.uuid4().hex[:16]
        self.data_shm_name = f"vibeocr_data_{unique_id}_{worker_id}"

        # 保留旧属性用于兼容
        self.shm_name = self.data_shm_name

        # 进程和通信
        self.process: subprocess.Popen | None = None
        self.protocol: SharedMemoryProtocol | None = None

        # stdout 读取线程（统一的日志通道）
        self._stdout_thread: threading.Thread | None = None

        # 状态
        self.busy = False
        self._ready = False

    @property
    def is_running(self) -> bool:
        """检查 Worker 进程是否在运行"""
        return self.process is not None and self.process.poll() is None

    @property
    def is_ready(self) -> bool:
        """检查 Worker 是否就绪"""
        return self._ready and self.is_running

    def _get_python_executable(self) -> str:
        """获取 Python 可执行文件路径"""
        # 优先使用当前 Python 解释器
        import sys

        return sys.executable

    def _get_worker_script(self) -> str:
        """获取 Worker 脚本路径"""
        # 使用模块方式运行
        return "-m"

    def start(
        self,
        timeout: float = 60.0,
        progress_callback: Callable[[str, int], None] | None = None,
    ) -> None:
        """启动 Worker 进程

        Args:
            timeout: 等待就绪的超时时间（秒）
            progress_callback: 进度回调函数，接收 (stage, percent) 参数
                stage: 启动阶段描述
                percent: 进度百分比 (0-100)

        Raises:
            OCRWorkerProcessError: 启动失败
        """
        if self.is_running:
            logger.warning(f"Worker {self.worker_id} 已在运行")
            return

        logger.info(f"启动 Worker {self.worker_id}...")
        start_time = time.time()

        def report_progress(stage: str, percent: int):
            """报告进度"""
            if progress_callback:
                with contextlib.suppress(Exception):
                    progress_callback(stage, percent)
            else:
                # 仅在没有回调时输出日志，避免重复
                logger.info(f"[Worker {self.worker_id}] {stage} ({percent}%)")

        # 阶段1: 创建共享内存 (0-20%)
        report_progress("创建共享内存", 10)
        try:
            config = SharedMemoryConfig(name=self.data_shm_name, size=self.shm_size)
            self.protocol = SharedMemoryProtocol(config)
            self.protocol.create()
            logger.debug(f"创建数据共享内存: {self.data_shm_name}")
            report_progress("共享内存已创建", 20)
        except Exception as e:
            raise OCRWorkerProcessError(f"创建数据共享内存失败: {e}") from None

        # 阶段2: 启动子进程 (20-40%)
        report_progress("启动子进程", 30)
        python_exe = self._get_python_executable()
        cmd = [
            python_exe,
            "-m",
            "vibeocr.workers.ocr_worker",
            "--shm-name",
            self.data_shm_name,
            "--shm-size",
            str(self.shm_size),
            "--use-gpu" if self.use_gpu else "--no-gpu",
        ]

        logger.debug(f"启动命令: {' '.join(cmd)}")

        try:
            # text=False 避免 Windows GBK 编码问题
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # 合并 stderr 到 stdout
                text=False,
            )

            # 启动一个线程读取子进程的 stdout（统一的日志通道）
            def read_stdout():
                try:
                    while self.process and self.process.poll() is None:
                        line = self.process.stdout.readline()
                        if line:
                            try:
                                text = line.decode("utf-8", errors="replace").strip()
                                if text:
                                    logger.info(f"[Worker {self.worker_id}] {text}")
                            except Exception:
                                pass
                except Exception as e:
                    logger.debug(f"stdout reader 错误: {e}")

            self._stdout_thread = threading.Thread(target=read_stdout, daemon=True)
            self._stdout_thread.start()
            report_progress("子进程已启动", 40)

        except Exception as e:
            self.protocol.close()
            self.protocol.unlink()
            raise OCRWorkerProcessError(f"启动子进程失败: {e}") from None

        # 阶段3: 等待 Worker 就绪 (40-100%)
        report_progress("等待 Worker 初始化...", 50)
        logger.info(f"[主进程] 等待 Worker {self.worker_id} 就绪信号...")
        wait_start_time = time.time()
        check_count = 0
        last_progress_time = wait_start_time

        while time.time() - wait_start_time < timeout:
            elapsed = time.time() - wait_start_time

            if not self.is_running:
                # 进程已退出，读取错误信息（使用 UTF-8 解码）
                stdout_bytes, stderr_bytes = self.process.communicate(timeout=5)
                stdout = (
                    stdout_bytes.decode("utf-8", errors="replace")
                    if stdout_bytes
                    else ""
                )
                stderr = (
                    stderr_bytes.decode("utf-8", errors="replace")
                    if stderr_bytes
                    else ""
                )
                error_msg = stderr or stdout or "未知错误"
                logger.error(f"[主进程] Worker 进程退出，错误: {error_msg[:500]}")
                raise OCRWorkerProcessError(
                    f"Worker 进程启动失败 (等待 {elapsed:.1f}秒): {error_msg[:200]}"
                )

            try:
                check_count += 1
                # 每 5 秒更新一次进度
                if time.time() - last_progress_time > 5:
                    progress = min(40 + int(elapsed / timeout * 60), 95)
                    report_progress(f"初始化中... ({elapsed:.0f}s)", progress)
                    last_progress_time = time.time()

                # 尝试读取就绪信号
                msg_type, data = self.protocol.read_message(
                    timeout=1.0, expected_sender="worker"
                )
                logger.info(
                    f"[主进程] 收到消息: type={msg_type}, data={data[:50] if data else b''}"
                )
                if msg_type == MSG_READY:
                    # 收到 Worker 的 READY 信号
                    logger.info(f"[主进程] 收到 Worker {self.worker_id} READY 信号")
                    report_progress("Worker 就绪", 100)

                    self._ready = True
                    total_time = time.time() - start_time
                    logger.info(
                        f"[主进程] Worker {self.worker_id} 已就绪! (总耗时: {total_time:.1f}s)"
                    )
                    return
            except SharedMemoryProtocolError:
                # 超时，继续等待
                pass

        # 超时 - 提供详细的诊断信息
        elapsed = time.time() - wait_start_time
        logger.error(
            f"[主进程] 等待 Worker 就绪超时 ({elapsed:.1f}s/{timeout}s)，共检查 {check_count} 次"
        )

        # 收集诊断信息
        diagnostics = []
        diagnostics.append(f"超时: {elapsed:.1f}s / {timeout}s")
        diagnostics.append(f"检查次数: {check_count}")

        # 检查进程状态
        if self.process:
            returncode = self.process.poll()
            diagnostics.append(
                f"进程状态: {'运行中' if returncode is None else f'已退出 ({returncode})'}"
            )

        # 检查共享内存
        if self.protocol and self.protocol.shm:
            diagnostics.append(f"共享内存: 已创建 ({self.data_shm_name})")
        else:
            diagnostics.append("共享内存: 未创建")

        # 尝试获取最后的日志输出
        try:
            time.sleep(0.5)  # 等待日志刷新
        except Exception:
            pass

        self.stop()

        # 构建详细的错误信息
        error_msg = (
            f"Worker 启动超时 ({elapsed:.1f}s)\n"
            f"诊断信息:\n" + "\n".join(f"  - {d}" for d in diagnostics) + "\n"
            f"\n可能原因:\n"
            f"  1. 首次启动需要下载/加载模型（可能需要 60-120 秒）\n"
            f"  2. GPU 初始化较慢\n"
            f"  3. 系统资源不足\n"
            f"\n建议:\n"
            f"  - 增加超时时间（当前 {timeout} 秒）\n"
            f"  - 检查 GPU 驱动和 CUDA 版本\n"
            f"  - 查看日志了解详细进度"
        )
        raise OCRWorkerProcessError(error_msg)

    def recognize(
        self,
        image_data: bytes,
        options_dict: dict,
        timeout: float = 60.0,
        auto_restart: bool = True,
    ):
        """执行 OCR 识别

        Args:
            image_data: 图像数据（bytes）
            options_dict: OCR 选项字典
            timeout: 超时时间（秒）
            auto_restart: Worker 崩溃时是否自动重启

        Returns:
            OCRResult 对象

        Raises:
            OCRWorkerProcessError: 识别失败
        """
        # 检查 Worker 状态，必要时自动重启
        if not self.is_ready:
            if auto_restart and self._try_restart():
                logger.info(f"[主进程] Worker {self.worker_id} 已自动重启")
            else:
                raise OCRWorkerProcessError(f"Worker {self.worker_id} 未就绪")

        self.busy = True
        logger.info(
            f"[主进程] Worker {self.worker_id} 开始识别，图像大小: {len(image_data)} 字节"
        )

        try:
            # 序列化并发送请求
            request_data = serialize_request(image_data, options_dict)
            logger.info(
                f"[主进程] 发送识别请求到 Worker {self.worker_id}，数据大小: {len(request_data)} 字节"
            )
            self.protocol.write_message(
                MSG_RECOGNIZE, request_data, timeout=timeout, sender="main"
            )
            logger.info(
                f"[主进程] 请求已发送，等待 Worker {self.worker_id} 返回结果..."
            )

            # 等待 Worker 读取请求（等待 _is_data_ready 变为 False）
            wait_start = time.time()
            while self.protocol._is_data_ready():
                if time.time() - wait_start > 5.0:
                    logger.warning(f"Worker {self.worker_id} 未及时读取识别请求")
                    break
                time.sleep(0.01)

            # 等待结果
            msg_type, data = self.protocol.read_message(
                timeout=timeout, expected_sender="worker"
            )
            logger.info(f"[主进程] 收到响应，消息类型: {msg_type}")

            if msg_type == MSG_RESULT:
                # 反序列化结果
                result = deserialize_result(data)
                logger.debug(f"Worker {self.worker_id} 识别完成")
                return result

            elif msg_type == MSG_ERROR:
                error_msg = data.decode("utf-8", errors="replace")
                raise OCRWorkerProcessError(f"OCR 识别失败: {error_msg}")

            else:
                raise OCRWorkerProcessError(f"未知响应类型: {msg_type}")

        except SharedMemoryProtocolError as e:
            # 检查 Worker 是否崩溃
            if not self.is_running and auto_restart:
                logger.warning(
                    f"[主进程] Worker {self.worker_id} 似乎已崩溃，尝试重启..."
                )
                if self._try_restart():
                    # 重新尝试识别
                    return self.recognize(
                        image_data, options_dict, timeout, auto_restart=False
                    )
            raise OCRWorkerProcessError(f"通信错误: {e}") from None

        finally:
            self.busy = False

    def _try_restart(self, timeout: float = 60.0) -> bool:
        """尝试重启 Worker

        Args:
            timeout: 启动超时时间（秒）

        Returns:
            重启是否成功
        """
        try:
            logger.info(f"[主进程] 尝试重启 Worker {self.worker_id}...")
            self.stop()
            # 重新生成共享内存名称（避免与旧内存冲突）
            unique_id = uuid.uuid4().hex[:16]
            self.data_shm_name = f"vibeocr_data_{unique_id}_{self.worker_id}"
            self.shm_name = self.data_shm_name

            self.start(timeout=timeout)
            logger.info(f"[主进程] Worker {self.worker_id} 重启成功")
            return True
        except Exception as e:
            logger.error(f"[主进程] Worker {self.worker_id} 重启失败: {e}")
            return False

    def preload_pipelines(
        self, pipelines: list[str], timeout: float = 180.0
    ) -> dict[str, bool]:
        """预加载指定管道

        Args:
            pipelines: 管道名称列表 ["ocr", "table_recognition", ...]
            timeout: 超时时间（秒）

        Returns:
            {pipeline_name: success} 结果字典

        Raises:
            OCRWorkerProcessError: 预加载失败
        """
        if not self.is_ready:
            raise OCRWorkerProcessError(f"Worker {self.worker_id} 未就绪")

        self.busy = True

        try:
            # 序列化并发送预加载请求
            request_data = serialize_preload_request(pipelines)
            self.protocol.write_message(
                MSG_PRELOAD, request_data, timeout=timeout, sender="main"
            )

            # 给 Worker 一点时间读取请求
            time.sleep(0.1)

            # 等待预加载结果
            start_time = time.time()
            while True:
                remaining_timeout = timeout - (time.time() - start_time)
                if remaining_timeout <= 0:
                    raise OCRWorkerProcessError(f"预加载超时 ({timeout}s)")

                try:
                    # 使用较短的单次读取超时，但整体受 remaining_timeout 控制
                    msg_type, data = self.protocol.read_message(
                        timeout=min(remaining_timeout, 10.0), expected_sender="worker"
                    )
                except SharedMemoryProtocolError as e:
                    # 如果是读取超时，继续等待
                    if "读取超时" in str(e):
                        logger.debug(f"Worker {self.worker_id} 等待预加载响应中...")
                        continue
                    raise

                if msg_type == MSG_PRELOAD_DONE:
                    # 反序列化结果
                    results = deserialize_preload_result(data)
                    logger.info(f"Worker {self.worker_id} 预加载完成: {results}")
                    return results

                elif msg_type == MSG_ERROR:
                    error_msg = data.decode("utf-8", errors="replace")
                    raise OCRWorkerProcessError(f"预加载失败: {error_msg}")

                elif msg_type == MSG_PRELOAD:
                    # 读到自己发送的预加载请求，Worker 还未处理，继续等待
                    logger.debug(
                        f"Worker {self.worker_id} 读到自己的预加载请求，继续等待响应..."
                    )
                    continue

                else:
                    logger.warning(
                        f"Worker {self.worker_id} 收到意外消息类型: {msg_type}"
                    )
                    continue

        except SharedMemoryProtocolError as e:
            raise OCRWorkerProcessError(f"通信错误: {e}") from None

        finally:
            self.busy = False

    def warmup_pipelines(
        self, pipelines: list[str], timeout: float = 180.0
    ) -> dict[str, bool]:
        """使用测试图片预热指定管道

        预热是真正的模型初始化，通过执行一次虚拟识别
        来触发模型加载到 GPU 内存和 CUDA 上下文创建。

        Args:
            pipelines: 管道名称列表
            timeout: 超时时间（秒）

        Returns:
            {pipeline_name: success} 结果字典
        """
        from vibeocr.utils.warmup_utils import warmup_worker_process

        if not self.is_ready:
            raise OCRWorkerProcessError(f"Worker {self.worker_id} 未就绪")

        results = {}
        for pipeline_name in pipelines:
            try:
                logger.info(f"[预热] Worker {self.worker_id} 预热管道: {pipeline_name}")
                success = warmup_worker_process(self, timeout=timeout)
                results[pipeline_name] = success
            except Exception as e:
                logger.error(
                    f"[预热] Worker {self.worker_id} 预热 {pipeline_name} 失败: {e}"
                )
                results[pipeline_name] = False

        return results

    # =========================================================================
    # 批量处理方法
    # =========================================================================

    def _send_batch_add(self, request_data: bytes, timeout: float = 30.0) -> bool:
        """发送批量添加请求

        Args:
            request_data: 序列化的批量请求数据
            timeout: 超时时间（秒）

        Returns:
            是否成功
        """
        if not self.is_ready:
            raise OCRWorkerProcessError(f"Worker {self.worker_id} 未就绪")

        try:
            self.protocol.write_message(
                MSG_BATCH_ADD, request_data, timeout=timeout, sender="main"
            )
            logger.debug(f"Worker {self.worker_id} 批量添加请求已发送")

            # 等待确认
            msg_type, data = self.protocol.read_message(
                timeout=timeout, expected_sender="worker"
            )
            if msg_type == MSG_ACK:
                return True
            elif msg_type == MSG_ERROR:
                error_msg = data.decode("utf-8", errors="replace")
                raise OCRWorkerProcessError(f"批量添加失败: {error_msg}")
            else:
                raise OCRWorkerProcessError(f"意外响应类型: {msg_type}")

        except SharedMemoryProtocolError as e:
            raise OCRWorkerProcessError(f"通信错误: {e}") from None

    def _send_batch_commit(self, commit_data: bytes, timeout: float = 300.0) -> dict:
        """发送批量提交请求并等待结果

        Args:
            commit_data: 序列化的批量提交数据
            timeout: 超时时间（秒）

        Returns:
            {request_id: result} 结果字典
        """
        if not self.is_ready:
            raise OCRWorkerProcessError(f"Worker {self.worker_id} 未就绪")

        self.busy = True
        try:
            self.protocol.write_message(
                MSG_BATCH_COMMIT, commit_data, timeout=timeout, sender="main"
            )
            logger.info(f"Worker {self.worker_id} 批量提交请求已发送，等待结果...")

            # 等待批量结果（可能需要较长时间）
            start_time = time.time()
            while True:
                remaining_timeout = timeout - (time.time() - start_time)
                if remaining_timeout <= 0:
                    raise OCRWorkerProcessError(f"批量处理超时 ({timeout}s)")

                try:
                    msg_type, data = self.protocol.read_message(
                        timeout=min(remaining_timeout, 60.0), expected_sender="worker"
                    )
                except SharedMemoryProtocolError as e:
                    if "读取超时" in str(e):
                        logger.debug(f"Worker {self.worker_id} 等待批量结果中...")
                        continue
                    raise

                if msg_type == MSG_BATCH_RESULT:
                    # 反序列化结果
                    results = deserialize_batch_result(data)
                    logger.info(
                        f"Worker {self.worker_id} 批量处理完成，返回 {len(results)} 个结果"
                    )
                    return results

                elif msg_type == MSG_BATCH_PROGRESS:
                    # 进度更新，继续等待
                    from vibeocr.utils.shared_memory_v2 import (
                        deserialize_batch_progress,
                    )

                    progress = deserialize_batch_progress(data)
                    logger.debug(
                        f"Worker {self.worker_id} 批量进度: {progress['completed']}/{progress['total']}"
                    )
                    continue

                elif msg_type == MSG_ERROR:
                    error_msg = data.decode("utf-8", errors="replace")
                    raise OCRWorkerProcessError(f"批量处理失败: {error_msg}")

                else:
                    logger.warning(
                        f"Worker {self.worker_id} 收到意外消息类型: {msg_type}"
                    )
                    continue

        except SharedMemoryProtocolError as e:
            raise OCRWorkerProcessError(f"通信错误: {e}") from None

        finally:
            self.busy = False

    def _send_batch_cancel(self, timeout: float = 5.0) -> bool:
        """发送批量取消请求

        Args:
            timeout: 超时时间（秒）

        Returns:
            是否成功
        """
        if not self.is_ready:
            return False

        try:
            self.protocol.write_message(
                MSG_BATCH_CANCEL, b"", timeout=timeout, sender="main"
            )
            logger.info(f"Worker {self.worker_id} 批量取消请求已发送")

            # 等待确认
            msg_type, _data = self.protocol.read_message(
                timeout=timeout, expected_sender="worker"
            )
            return msg_type == MSG_ACK

        except SharedMemoryProtocolError as e:
            logger.warning(f"发送批量取消请求失败: {e}")
            return False

    def stop(self, timeout: float = 5.0) -> None:
        """停止 Worker 进程

        Args:
            timeout: 等待进程退出的超时时间（秒）
        """
        if self.process is None:
            return

        logger.info(f"停止 Worker {self.worker_id}...")

        # 尝试发送关闭信号
        if self.protocol and self.is_running:
            with contextlib.suppress(SharedMemoryProtocolError):
                self.protocol.write_message(MSG_SHUTDOWN, b"", timeout=1.0)

        # 等待进程退出
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # 强制终止
            logger.warning(f"Worker {self.worker_id} 未响应，强制终止")
            self.process.kill()
            self.process.wait(timeout=1.0)

        # 关闭数据共享内存
        if self.protocol:
            self.protocol.close()
            self.protocol.unlink()
            self.protocol = None

        self.process = None
        self._ready = False
        self.busy = False

        logger.info(f"Worker {self.worker_id} 已停止")

    def restart(self, timeout: float = 60.0) -> None:
        """重启 Worker 进程

        Args:
            timeout: 等待就绪的超时时间（秒）
        """
        logger.info(f"重启 Worker {self.worker_id}...")
        self.stop()
        self.start(timeout)

    def __enter__(self) -> "OCRWorkerProcess":
        """上下文管理器入口"""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """上下文管理器退出"""
        self.stop()

    def __repr__(self) -> str:
        status = "running" if self.is_running else "stopped"
        ready = "ready" if self._ready else "not ready"
        busy = "busy" if self.busy else "idle"
        return f"<OCRWorkerProcess id={self.worker_id} {status} {ready} {busy}>"

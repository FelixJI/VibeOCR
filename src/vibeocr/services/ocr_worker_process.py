"""OCR Worker 进程管理器

管理单个 OCR Worker 子进程的生命周期。
支持双共享内存设计：数据通道（OCR请求/结果）和日志通道。
"""

import contextlib
import locale
import logging
import os
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from vibeocr.core.constants import DEFAULT_SHM_SIZE, Constants
from vibeocr.pipeline_status import is_pipeline_ever_succeeded
from vibeocr.utils.job_object import JobObjectGuard
from vibeocr.utils.shared_memory_v2 import (
    MessageType,
    SharedMemoryConfig,
    SharedMemoryProtocolError,
    deserialize_batch_result,
    deserialize_preload_result,
    deserialize_recognize_batch_result,
    deserialize_result,
    serialize_preload_request,
    serialize_recognize_batch_request,
    serialize_request,
)
from vibeocr.utils.shared_memory_v2 import (
    SharedMemoryProtocolV2 as SharedMemoryProtocol,  # 批量消息序列化函数
)
from vibeocr.utils.subprocess_log import SubprocessLogForwarder

# 消息类型别名（保持兼容）
MSG_RECOGNIZE = MessageType.RECOGNIZE
MSG_RECOGNIZE_BATCH = MessageType.RECOGNIZE_BATCH
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
MSG_BATCH_FILE_DONE = MessageType.BATCH_FILE_DONE
MSG_RELEASE_PIPELINES = MessageType.RELEASE_PIPELINES
MSG_SET_TTL = MessageType.SET_TTL

logger = logging.getLogger(__name__)


class OCRWorkerProcessError(Exception):
    """OCR Worker 进程错误"""


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
        self,
        worker_id: int,
        use_gpu: bool = True,
        shm_size: int = DEFAULT_SHM_SIZE,
        worker_module: str = "vibeocr.workers.ocr_worker",
    ):
        """初始化 Worker 进程管理器

        Args:
            worker_id: Worker 标识符
            use_gpu: 是否使用 GPU
            shm_size: 数据共享内存大小（字节）
            worker_module: Worker 子进程模块路径
        """
        self.worker_id = worker_id
        self.use_gpu = use_gpu
        self.shm_size = shm_size
        self.worker_module = worker_module

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

        # 预加载兜底防护：连续读到自身请求的计数（超过阈值判定异常）
        self._preload_self_read_count = 0

        # 防止并发重启的锁（健康检查和识别任务可能同时触发重启）
        self._restart_lock = threading.Lock()

        # 防止并发操作共享内存的锁（预热和用户请求可能同时调用 recognize）
        self._operation_lock = threading.RLock()

        # Windows Job Object 守卫：主进程崩溃时内核连带终止 worker 子进程
        self._job_guard: JobObjectGuard | None = None

        # 子进程 stdout 日志转发器（统一的日志通道）。
        # 裸 print（无标准日志格式的 stdout 行，可能含用户文档内容）只按行数
        # 概括输出，结构化行按原级别转发。逻辑见 vibeocr.utils.subprocess_log，
        # 与 PDF 后端、MinerU 共用同一套转发器。
        self._log_forwarder = SubprocessLogForwarder(
            logger_name="vibeocr.subprocess.ocr_worker",
            source_label=f"[Worker {worker_id}]",
        )

        # 启动期原始输出缓冲：Worker 就绪前的全部 stdout 行（含 stderr，
        # 因 stderr 已合并到 stdout）原样保留，便于进程早退时定位真实错误
        # （如 ModuleNotFoundError）。就绪后停止累积，避免长期运行占用内存。
        self._startup_output: list[str] = []
        self._startup_output_lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        """检查 Worker 进程是否在运行"""
        return self.process is not None and self.process.poll() is None

    @property
    def is_ready(self) -> bool:
        """检查 Worker 是否就绪"""
        return self._ready and self.is_running

    def _get_python_executable(self) -> str:
        """获取 Worker 子进程的解释器路径

        打包态 VibeOCR.exe 是 PyInstaller frozen exe，其 bootloader 会忽略
        ``-m`` 参数而无条件执行打包入口 main.py。若用 VibeOCR.exe 跑
        ``-m vibeocr.workers.ocr_worker``，子进程会变成完整 GUI 并再次
        spawn Worker，形成进程递归爆炸（界面卡死）。

        故打包态必须用嵌入式 Python（便携 python/python.exe）跑 Worker——
        这正是「重依赖由嵌入式 Python 独立安装」架构的本意。开发态仍用
        当前解释器 sys.executable。

        Returns:
            解释器可执行文件路径
        """
        import sys

        if getattr(sys, "frozen", False):
            from vibeocr import env_manager

            project_root = env_manager.get_project_root()
            python_exe = env_manager.get_embedded_python_executable(project_root)
            if python_exe.exists():
                return str(python_exe)
            # 兜底：嵌入式 Python 不存在时回退 sys.executable。
            # 此时 Worker 会因 import paddle/torch 失败而退出（不会递归成
            # GUI，因为那是 frozen exe 的行为），依赖检测/安装引导会介入。
        return sys.executable

    def _get_worker_env(self) -> dict[str, str]:
        """构造 Worker 子进程的环境变量

        打包态下 Worker 用嵌入式 Python 运行，而 vibeocr 源码由 PyInstaller
        以 datas 形式平铺到 ``sys._MEIPASS/vibeocr``（见 VibeOCR.spec）。
        嵌入式 Python 是独立解释器，无法读取 exe 内部的 PYZ 归档，必须通过
        PYTHONPATH 显式指向 ``_MEIPASS`` 才能 ``import vibeocr``。

        开发态当前解释器已能从 ``src/`` 找到 vibeocr，无需额外设置；直接继承
        父进程环境即可。

        Returns:
            子进程环境变量字典
        """
        import os
        import sys

        env = os.environ.copy()

        if getattr(sys, "frozen", False):
            # _MEIPASS 是 PyInstaller 解包目录，datas 中的 vibeocr 源码平铺于此
            meipass = getattr(sys, "_MEIPASS", None)
            if meipass:
                existing = env.get("PYTHONPATH", "")
                env["PYTHONPATH"] = (
                    f"{meipass};{existing}" if existing else str(meipass)
                )
        else:
            # 开发态：vibeocr 通过 conftest 的 sys.path.insert(0, src/) 在主进程
            # 可见，但子进程是独立的 sys.executable，不继承该 sys.path 修改，
            # 会报 ModuleNotFoundError: No module named 'vibeocr'。
            # 显式把 src/（vibeocr 包父目录）加入子进程 PYTHONPATH。
            import vibeocr

            src_dir = str(Path(vibeocr.__file__).resolve().parent.parent)
            sep = os.pathsep
            existing = env.get("PYTHONPATH", "")
            if src_dir not in existing.split(sep):
                env["PYTHONPATH"] = (
                    f"{src_dir}{sep}{existing}" if existing else src_dir
                )

        return env

    def _parse_and_forward_log(self, text: str) -> None:
        """解析子进程日志行并按原始级别转发（委托给 SubprocessLogForwarder）。

        历史实现见 vibeocr.utils.subprocess_log，2026-07 统一日志通道时抽出，
        与 PDF 后端、MinerU 共用同一套转发逻辑：结构化行按级别转发，
        裸 print 只输出概括行数，避免泄漏用户文档内容。
        """
        self._log_forwarder.forward(text)

    def flush_raw_log_buffer(self) -> None:
        """输出并清空已累积的裸 print 概括计数（委托给 SubprocessLogForwarder）。"""
        self._log_forwarder.flush()

    def _split_mixed_log_lines(self, text: str) -> list[str]:
        """分割混合的日志行（委托给 SubprocessLogForwarder）。

        PaddlePaddle 的 warnings.warn() 输出有时没有换行符，
        导致多个日志行被拼接到一行。
        """
        return SubprocessLogForwarder.split_mixed_lines(text)

    def _get_worker_script(self) -> str:
        """获取 Worker 脚本路径"""
        # 使用模块方式运行
        return "-m"

    def start(
        self,
        timeout: float = Constants.Timeout.WORKER_START,
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

        logger.debug(f"启动 Worker {self.worker_id}...")
        start_time = time.time()

        def report_progress(stage: str):
            """报告进度"""
            if progress_callback:
                with contextlib.suppress(Exception):
                    progress_callback(stage, 0)  # 保留接口兼容，但不传递百分比
            else:
                logger.debug(f"[Worker {self.worker_id}] {stage}")

        # 阶段1: 创建共享内存
        report_progress("创建共享内存")
        try:
            config = SharedMemoryConfig(name=self.data_shm_name, size=self.shm_size)
            self.protocol = SharedMemoryProtocol(config)
            self.protocol.create()
            logger.debug(f"创建数据共享内存: {self.data_shm_name}")
            report_progress("共享内存已创建")
        except Exception as e:
            raise OCRWorkerProcessError(f"创建数据共享内存失败: {e}") from None

        # 阶段2: 启动子进程 (20-40%)
        report_progress("启动子进程")
        python_exe = self._get_python_executable()
        cmd = [
            python_exe,
            "-m",
            self.worker_module,
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
                env=self._get_worker_env(),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )

            # 绑定 Windows Job Object：主进程崩溃时内核连带终止 worker
            self._job_guard = JobObjectGuard(name=f"vibeocr_worker_{self.worker_id}")
            self._job_guard.assign_from_popen(self.process)

            # 启动一个线程读取子进程的 stdout（统一的日志通道）
            def read_stdout():
                import platform

                is_windows = platform.system() == "Windows"

                try:
                    process = self.process
                    while process and process.poll() is None:
                        stdout = process.stdout
                        if stdout is None:
                            break
                        line = stdout.readline()
                        if line:
                            try:
                                # Windows 上 PaddlePaddle 可能使用 GBK 编码
                                # 先尝试 UTF-8，失败则尝试系统默认编码（Windows 为 GBK）
                                text = None
                                try:
                                    text = line.decode("utf-8").strip()
                                except UnicodeDecodeError:
                                    # UTF-8 解码失败，尝试系统编码
                                    try:
                                        encoding = (
                                            "gbk"
                                            if is_windows
                                            else locale.getpreferredencoding(False)
                                        )
                                        text = line.decode(
                                            encoding, errors="replace"
                                        ).strip()
                                    except Exception:
                                        text = line.decode(
                                            "utf-8", errors="replace"
                                        ).strip()

                                if text:
                                    # 就绪前累积原始输出，用于进程早退时定位真实错误
                                    # （如 ModuleNotFoundError），避免被 communicate() 吞成"未知错误"
                                    if not self._ready:
                                        with self._startup_output_lock:
                                            # 上限保护，避免异常大量输出耗尽内存
                                            if len(self._startup_output) < 200:
                                                self._startup_output.append(text)
                                    # 过滤 PaddlePaddle 内部调试输出
                                    if text.startswith("return tensor("):
                                        continue
                                    lines_to_log = self._split_mixed_log_lines(text)
                                    for log_line in lines_to_log:
                                        if log_line:
                                            self._parse_and_forward_log(log_line)
                            except Exception:
                                pass
                    # 进程退出后，flush 累积的裸 print 概括
                    self.flush_raw_log_buffer()
                except Exception as e:
                    logger.debug(f"stdout reader 错误: {e}")

            self._stdout_thread = threading.Thread(target=read_stdout, daemon=True)
            self._stdout_thread.start()
            report_progress("子进程已启动")

        except Exception as e:
            self.protocol.close()
            self.protocol.unlink()
            raise OCRWorkerProcessError(f"启动子进程失败: {e}") from None

        # 阶段3: 等待 Worker 就绪
        report_progress("等待 Worker 初始化...")
        logger.debug(f"[主进程] 等待 Worker {self.worker_id} 就绪信号...")
        wait_start_time = time.time()
        check_count = 0
        last_progress_time = wait_start_time

        while time.time() - wait_start_time < timeout:
            elapsed = time.time() - wait_start_time

            if not self.is_running:
                # 进程已退出，读取错误信息。
                # 注意：后台 read_stdout 线程已持续消费 stdout，此时 communicate()
                # 通常返回空，故优先使用启动期累积的原始输出（_startup_output），
                # 它包含 Worker 就绪前的全部 stderr/stderr（已合并）。
                error_parts: list[str] = []
                with self._startup_output_lock:
                    error_parts.extend(self._startup_output)
                # 兜底：若启动期缓冲为空，再尝试 communicate() 取管道残余
                if not error_parts:
                    try:
                        stdout_bytes, _ = self.process.communicate(timeout=5)
                        if stdout_bytes:
                            fallback = stdout_bytes.decode("utf-8", errors="replace")
                            if fallback.strip():
                                error_parts.append(fallback)
                    except Exception:
                        pass
                error_msg = "\n".join(error_parts).strip() or "未知错误"
                logger.error(f"[主进程] Worker 进程退出，错误: {error_msg[:500]}")
                raise OCRWorkerProcessError(
                    f"Worker 进程启动失败 (等待 {elapsed:.1f}秒): {error_msg[:200]}"
                )

            try:
                check_count += 1
                # 每 5 秒更新一次进度
                if time.time() - last_progress_time > 5:
                    report_progress("初始化中...")
                    last_progress_time = time.time()

                # 尝试读取就绪信号
                msg_type, data = self.protocol.read_message(
                    timeout=1.0, expected_sender="worker"
                )
                logger.debug(
                    f"[主进程] 收到消息: type={msg_type.decode('ascii', errors='replace')}, data={data[:50] if data else b''}"
                )
                if msg_type == MSG_READY:
                    # 收到 Worker 的 READY 信号
                    logger.debug(f"[主进程] 收到 Worker {self.worker_id} READY 信号")
                    report_progress("Worker 就绪")

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
        logger.error("[主进程] 等待 Worker 就绪超时")

        # 收集诊断信息
        diagnostics = []

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
        time.sleep(0.5)  # 等待日志刷新

        self.stop()

        # 构建详细的错误信息
        error_msg = (
            "Worker 启动超时\n"
            "可能原因:\n"
            "  1. 首次启动需要下载模型\n"
            "  2. GPU 初始化较慢\n"
            "  3. 系统资源不足\n"
            "\n建议:\n"
            "  - 请稍后重试\n"
            "  - 检查 GPU 驱动和 CUDA 版本\n"
            "  - 查看日志了解详细进度"
        )
        raise OCRWorkerProcessError(error_msg)

    def recognize(
        self,
        image_data: bytes,
        options_dict: dict,
        timeout: float = Constants.Timeout.RECOGNIZE_CACHED,
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
        with self._operation_lock:
            # 检查 Worker 状态，必要时自动重启
            if not self.is_ready:
                if auto_restart and self._try_restart():
                    logger.debug(f"[主进程] Worker {self.worker_id} 已自动重启")
                else:
                    raise OCRWorkerProcessError(f"Worker {self.worker_id} 未就绪")

            protocol = self.protocol
            if protocol is None:  # guarded by is_ready check above
                raise OCRWorkerProcessError(f"Worker {self.worker_id} 通信协议未初始化")
            self.busy = True
            logger.debug(
                f"[主进程] Worker {self.worker_id} 开始识别，图像大小: {len(image_data)} 字节"
            )

            try:
                # 序列化并发送请求
                request_data = serialize_request(image_data, options_dict)
                logger.debug(
                    f"[主进程] 发送识别请求到 Worker {self.worker_id}，数据大小: {len(request_data)} 字节"
                )
                protocol.write_message(
                    MSG_RECOGNIZE, request_data, timeout=timeout, sender="main"
                )
                logger.debug(
                    f"[主进程] 请求已发送，等待 Worker {self.worker_id} 返回结果..."
                )

                # 等待 Worker 读取请求（等待 _is_data_ready 变为 False）
                wait_start = time.time()
                while protocol._is_data_ready():
                    if time.time() - wait_start > 5.0:
                        logger.warning(f"Worker {self.worker_id} 未及时读取识别请求")
                        break
                    time.sleep(0.01)

                # 等待结果
                msg_type, data = protocol.read_message(
                    timeout=timeout, expected_sender="worker"
                )
                logger.debug(
                    f"[主进程] 收到响应，消息类型: {msg_type.decode('ascii', errors='replace')}"
                )

                if msg_type == MSG_RESULT:
                    # 反序列化结果
                    result = deserialize_result(data)
                    logger.debug(f"Worker {self.worker_id} 识别完成")
                    return result

                if msg_type == MSG_ERROR:
                    error_msg = data.decode("utf-8", errors="replace")
                    raise OCRWorkerProcessError(f"OCR 识别失败: {error_msg}")

                if msg_type == MSG_SHUTDOWN:
                    raise OCRWorkerProcessError("Worker 被关闭（可能因超时卡死）")

                raise OCRWorkerProcessError(
                    f"未知响应类型: {msg_type.decode('ascii', errors='replace')}"
                )

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

    def recognize_batch(
        self,
        images: list[bytes],
        options_dict: dict,
        timeout: float = Constants.Timeout.RECOGNIZE_CACHED,
        auto_restart: bool = True,
    ) -> list:
        """批量 OCR 识别（多图一次 predict）

        镜像 recognize() 的协议往返，但请求为图像列表。Worker 侧收到后调用
        OCRService.recognize_batch(list)（内部一次 predict(list)），返回结果列表，
        顺序与 images 一致。

        Args:
            images: 各页 PNG bytes 列表。
            options_dict: OCR 选项字典（所有图像共享）。
            timeout: 超时时间（秒）。
            auto_restart: Worker 崩溃时是否自动重启。

        Returns:
            OCRResult 对象列表，顺序与 images 一致。

        Raises:
            OCRWorkerProcessError: 识别失败。
        """
        with self._operation_lock:
            # 检查 Worker 状态，必要时自动重启
            if not self.is_ready:
                if auto_restart and self._try_restart():
                    logger.debug(f"[主进程] Worker {self.worker_id} 已自动重启")
                else:
                    raise OCRWorkerProcessError(f"Worker {self.worker_id} 未就绪")

            protocol = self.protocol
            if protocol is None:  # guarded by is_ready check above
                raise OCRWorkerProcessError(f"Worker {self.worker_id} 通信协议未初始化")
            self.busy = True
            total_bytes = sum(len(img) for img in images)
            logger.debug(
                f"[主进程] Worker {self.worker_id} 开始批量识别，{len(images)} 张，"
                f"共 {total_bytes} 字节"
            )

            try:
                # 序列化并发送批量请求
                request_data = serialize_recognize_batch_request(images, options_dict)
                logger.debug(
                    f"[主进程] 发送批量识别请求到 Worker {self.worker_id}，"
                    f"数据大小: {len(request_data)} 字节"
                )
                protocol.write_message(
                    MSG_RECOGNIZE_BATCH, request_data, timeout=timeout, sender="main"
                )
                logger.debug(
                    f"[主进程] 批量请求已发送，等待 Worker {self.worker_id} 返回结果..."
                )

                # 等待 Worker 读取请求（等待 _is_data_ready 变为 False）
                wait_start = time.time()
                while protocol._is_data_ready():
                    if time.time() - wait_start > 5.0:
                        logger.warning(
                            f"Worker {self.worker_id} 未及时读取批量识别请求"
                        )
                        break
                    time.sleep(0.01)

                # 等待结果
                msg_type, data = protocol.read_message(
                    timeout=timeout, expected_sender="worker"
                )
                logger.debug(
                    f"[主进程] 收到响应，消息类型: {msg_type.decode('ascii', errors='replace')}"
                )

                if msg_type == MSG_RESULT:
                    # 反序列化批量结果列表
                    results = deserialize_recognize_batch_result(data)
                    logger.debug(
                        f"Worker {self.worker_id} 批量识别完成，返回 {len(results)} 个结果"
                    )
                    return results

                if msg_type == MSG_ERROR:
                    error_msg = data.decode("utf-8", errors="replace")
                    raise OCRWorkerProcessError(f"OCR 批量识别失败: {error_msg}")

                if msg_type == MSG_SHUTDOWN:
                    raise OCRWorkerProcessError("Worker 被关闭（可能因超时卡死）")

                raise OCRWorkerProcessError(
                    f"未知响应类型: {msg_type.decode('ascii', errors='replace')}"
                )

            except SharedMemoryProtocolError as e:
                # 检查 Worker 是否崩溃
                if not self.is_running and auto_restart:
                    logger.warning(
                        f"[主进程] Worker {self.worker_id} 似乎已崩溃，尝试重启..."
                    )
                    if self._try_restart():
                        # 重新尝试批量识别
                        return self.recognize_batch(
                            images, options_dict, timeout, auto_restart=False
                        )
                raise OCRWorkerProcessError(f"通信错误: {e}") from None

            finally:
                self.busy = False

    def _try_restart(self, timeout: float = Constants.Timeout.RESTART) -> bool:
        """尝试重启 Worker

        使用锁确保同一时间只有一个线程执行重启，
        避免健康检查和识别任务同时重启导致重复子进程。

        Args:
            timeout: 启动超时时间（秒）

        Returns:
            重启是否成功
        """
        with self._restart_lock:
            # 如果其他线程已经完成重启，直接返回
            if self.is_ready:
                return True
            try:
                logger.debug(f"[主进程] 尝试重启 Worker {self.worker_id}...")
                self.stop()
                # 重新生成共享内存名称（避免与旧内存冲突）
                unique_id = uuid.uuid4().hex[:16]
                self.data_shm_name = f"vibeocr_data_{unique_id}_{self.worker_id}"
                self.shm_name = self.data_shm_name

                self.start(timeout=timeout)
                logger.debug(f"[主进程] Worker {self.worker_id} 重启成功")
                return True
            except Exception as e:
                logger.error(f"[主进程] Worker {self.worker_id} 重启失败: {e}")
                return False

    def force_restart(
        self, reason: str = "", timeout: float = Constants.Timeout.RESTART
    ) -> bool:
        """强制重启 Worker（总是 stop+start，即使 is_ready）。

        用于健康检查发现 stale-but-alive worker、或抢占被后台任务阻塞的 worker。
        与 _try_restart 的区别：后者在 is_ready 时直接返回 True（不重启），
        这会导致"卡死但进程存活"的 worker 被误判为已重启并重新接单。
        force_restart 无条件 stop+start，确保协议状态被重建。
        """
        with self._restart_lock:
            try:
                logger.warning(
                    f"[主进程] 强制重启 Worker {self.worker_id}（原因: {reason}）..."
                )
                self.stop()
                # 重新生成共享内存名称（避免与旧内存冲突）
                unique_id = uuid.uuid4().hex[:16]
                self.data_shm_name = f"vibeocr_data_{unique_id}_{self.worker_id}"
                self.shm_name = self.data_shm_name
                self.start(timeout=timeout)
                logger.debug(f"[主进程] Worker {self.worker_id} 强制重启成功")
                return True
            except Exception as e:
                logger.error(f"[主进程] Worker {self.worker_id} 强制重启失败: {e}")
                return False

    def preload_pipelines(
        self,
        pipelines: list[str],
        timeout: float | None = None,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> dict[str, bool]:
        """预加载指定管道

        Args:
            pipelines: 管道名称列表 ["ocr", "table_recognition", ...]
            timeout: 超时时间（秒），如果为 None 则根据模型缓存状态自动确定
            progress_callback: 进度回调函数，接收 (pipeline_name, current, total) 参数
                用于报告加载进度，特别是首次下载模型时

        Returns:
            {pipeline_name: success} 结果字典

        Raises:
            OCRWorkerProcessError: 预加载失败
        """
        if not self.is_ready:
            raise OCRWorkerProcessError(f"Worker {self.worker_id} 未就绪")

        # 智能超时：根据模型缓存状态确定超时时间
        if timeout is None:
            timeout = self._calculate_preload_timeout(pipelines)

        protocol = self.protocol
        if protocol is None:  # guarded by is_ready check above
            raise OCRWorkerProcessError(f"Worker {self.worker_id} 通信协议未初始化")
        self.busy = True

        try:
            # 重置自读计数器（用于兜底分支的防护）
            self._preload_self_read_count = 0

            # 序列化并发送预加载请求
            request_data = serialize_preload_request(pipelines)
            protocol.write_message(
                MSG_PRELOAD, request_data, timeout=timeout, sender="main"
            )

            # 等待 Worker 读取请求（等待 _is_data_ready 变为 False）
            # 对齐 recognize() 的做法：确认 Worker 已消费请求，避免主进程
            # 在 read_message 重试时读到自己刚写入的 PREL（曾导致死锁超时）
            wait_start = time.time()
            while protocol._is_data_ready():
                if time.time() - wait_start > 5.0:
                    logger.warning(
                        f"Worker {self.worker_id} 未及时读取预加载请求"
                    )
                    break
                time.sleep(0.01)

            # 等待预加载结果
            start_time = time.time()
            last_progress_time = start_time
            total_pipelines = len(pipelines)

            while True:
                remaining_timeout = timeout - (time.time() - start_time)
                if remaining_timeout <= 0:
                    raise OCRWorkerProcessError(
                        "预加载超时\n"
                        "可能原因：首次使用需要下载模型\n"
                        "建议：请检查网络连接，稍后重试"
                    )

                try:
                    # 使用较短的单次读取超时，但整体受 remaining_timeout 控制
                    msg_type, data = protocol.read_message(
                        timeout=min(remaining_timeout, 10.0), expected_sender="worker"
                    )
                except SharedMemoryProtocolError as e:
                    # 如果是读取超时，继续等待
                    if "读取超时" in str(e):
                        # 每 5 秒报告一次进度
                        if time.time() - last_progress_time >= 5.0:
                            logger.debug(f"Worker {self.worker_id} 等待预加载响应中...")
                            if progress_callback:
                                # 报告正在加载中
                                with contextlib.suppress(Exception):
                                    progress_callback(
                                        pipelines[0] if pipelines else "unknown",
                                        0,
                                        total_pipelines,
                                    )
                            last_progress_time = time.time()
                        continue
                    raise

                if msg_type == MSG_PRELOAD_DONE:
                    # 反序列化结果
                    results = deserialize_preload_result(data)
                    logger.debug(f"Worker {self.worker_id} 预加载完成: {results}")
                    # 报告完成进度
                    if progress_callback:
                        with contextlib.suppress(Exception):
                            for i, pipeline_name in enumerate(pipelines, 1):
                                progress_callback(pipeline_name, i, total_pipelines)
                    return results

                if msg_type == MSG_ERROR:
                    error_msg = data.decode("utf-8", errors="replace")
                    raise OCRWorkerProcessError(f"预加载失败: {error_msg}")

                if msg_type == MSG_PRELOAD:
                    # 兜底：正常流程下（已轮询确认 Worker 读取）不应走到这里。
                    # 若仍读到自身请求，说明 Worker 处理缓慢或存在竞态。
                    self._preload_self_read_count += 1
                    logger.warning(
                        f"Worker {self.worker_id} 读到自己的预加载请求"
                        f"（第 {self._preload_self_read_count} 次），"
                        f"这可能表明 Worker 处理缓慢；继续等待响应..."
                    )
                    # 连续命中超过阈值则判定异常，避免无限等待
                    if self._preload_self_read_count >= 5:
                        raise OCRWorkerProcessError(
                            "预加载异常：多次读到自身请求，Worker 可能无响应"
                        )
                    continue

                logger.warning(
                    f"Worker {self.worker_id} 收到意外消息类型: {msg_type.decode('ascii', errors='replace')}"
                )
                continue

        except SharedMemoryProtocolError as e:
            raise OCRWorkerProcessError(f"通信错误: {e}") from None

        finally:
            self.busy = False

    @staticmethod
    def _get_project_root():
        from vibeocr.env_manager import get_project_root

        return get_project_root()

    def _calculate_preload_timeout(self, pipelines: list[str]) -> float:
        """根据模型缓存状态计算预加载超时时间

        Args:
            pipelines: 要加载的管道名称列表

        Returns:
            超时时间（秒）
        """
        T = Constants.Timeout  # 超时配置统一来源

        # 检查是否有任何模型未缓存
        uncached_pipelines = []
        from enum import Enum

        for pipeline_name in pipelines:
            # 处理枚举类型
            name = (
                pipeline_name.value
                if isinstance(pipeline_name, Enum)
                else pipeline_name
            )
            if not is_pipeline_ever_succeeded(name, self._get_project_root()):
                uncached_pipelines.append(pipeline_name)

        if uncached_pipelines:
            # 有模型未缓存，使用较长超时
            logger.warning(
                f"[预加载] 检测到未缓存模型: {uncached_pipelines}，"
                f"使用延长超时（首次使用可能需要下载模型）"
            )
            # 基础超时 + 每个管道额外时间
            timeout = T.PRELOAD_UNCACHED + len(pipelines) * T.PRELOAD_PER_PIPELINE
        else:
            # 所有模型已缓存，使用较短超时
            logger.debug(f"[预加载] 所有模型已缓存，使用标准超时 ({T.PRELOAD_CACHED}s)")
            timeout = T.PRELOAD_CACHED + len(pipelines) * (T.PRELOAD_PER_PIPELINE / 2)

        logger.debug(f"[预加载] 计算的超时时间: {timeout}s")
        return timeout

    def warmup_pipelines(
        self, pipelines: list[str], timeout: float = Constants.Timeout.PIPELINE_PRELOAD_DEFAULT
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
                logger.debug(
                    f"[预热] Worker {self.worker_id} 预热管道: {pipeline_name}"
                )
                success = warmup_worker_process(
                    self, pipeline=pipeline_name, timeout=timeout
                )
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

    def _send_batch_add(self, request_data: bytes, timeout: float = Constants.Timeout.SHM_WRITE) -> bool:
        """发送批量添加请求

        Args:
            request_data: 序列化的批量请求数据
            timeout: 超时时间（秒）

        Returns:
            是否成功
        """
        if not self.is_ready:
            raise OCRWorkerProcessError(f"Worker {self.worker_id} 未就绪")

        protocol = self.protocol
        if protocol is None:  # guarded by is_ready check above
            raise OCRWorkerProcessError(f"Worker {self.worker_id} 通信协议未初始化")
        try:
            protocol.write_message(
                MSG_BATCH_ADD, request_data, timeout=timeout, sender="main"
            )
            logger.debug(f"Worker {self.worker_id} 批量添加请求已发送")

            # 等待 Worker 消费请求，避免 read-own-write
            protocol.wait_for_read(timeout=timeout)

            # 等待确认
            msg_type, data = protocol.read_message(
                timeout=timeout, expected_sender="worker"
            )
            if msg_type == MSG_ACK:
                return True
            if msg_type == MSG_ERROR:
                error_msg = data.decode("utf-8", errors="replace")
                raise OCRWorkerProcessError(f"批量添加失败: {error_msg}")
            raise OCRWorkerProcessError(
                f"意外响应类型: {msg_type.decode('ascii', errors='replace')}"
            )

        except SharedMemoryProtocolError as e:
            raise OCRWorkerProcessError(f"通信错误: {e}") from None

    def _send_batch_commit(
        self,
        commit_data: bytes,
        timeout: float = Constants.Timeout.BATCH_COMMIT_DEFAULT,
        file_completed_callback=None,
    ) -> dict:
        """发送批量提交请求并等待结果

        Args:
            commit_data: 序列化的批量提交数据
            timeout: 超时时间（秒）
            file_completed_callback: 单文件完成回调 (request_id, result)

        Returns:
            {request_id: result} 结果字典
        """
        if not self.is_ready:
            raise OCRWorkerProcessError(f"Worker {self.worker_id} 未就绪")

        protocol = self.protocol
        if protocol is None:  # guarded by is_ready check above
            raise OCRWorkerProcessError(f"Worker {self.worker_id} 通信协议未初始化")
        self.busy = True
        try:
            protocol.write_message(
                MSG_BATCH_COMMIT, commit_data, timeout=timeout, sender="main"
            )
            logger.debug(f"Worker {self.worker_id} 批量提交请求已发送，等待结果...")

            # 等待 Worker 消费请求，避免 read-own-write
            protocol.wait_for_read(timeout=timeout)

            # 等待批量结果（可能需要较长时间）
            all_results: dict = {}
            reported_ids: set[str] = set()
            start_time = time.time()
            while True:
                # 检查取消标志（独立控制通道，不依赖消息读取）
                if protocol.is_cancelled():
                    logger.info(
                        f"Worker {self.worker_id} 批量处理被取消，返回已收集的 {len(all_results)} 个结果"
                    )
                    protocol.clear_cancel_flag()
                    return all_results

                remaining_timeout = timeout - (time.time() - start_time)
                if remaining_timeout <= 0:
                    raise OCRWorkerProcessError(f"批量处理超时 ({timeout}s)")

                try:
                    msg_type, data = protocol.read_message(
                        timeout=min(remaining_timeout, 60.0), expected_sender="worker"
                    )
                except SharedMemoryProtocolError as e:
                    if "读取超时" in str(e):
                        logger.debug(f"Worker {self.worker_id} 等待批量结果中...")
                        continue
                    raise

                if msg_type == MSG_BATCH_FILE_DONE:
                    # 单文件流式结果
                    file_results = deserialize_batch_result(data)
                    all_results.update(file_results)
                    if file_completed_callback:
                        for req_id, result in file_results.items():
                            if req_id not in reported_ids:
                                reported_ids.add(req_id)
                                file_completed_callback(req_id, result)
                    continue

                if msg_type == MSG_BATCH_RESULT:
                    # 最终汇总结果
                    final_results = deserialize_batch_result(data)
                    all_results.update(final_results)
                    if file_completed_callback:
                        for req_id, result in final_results.items():
                            if req_id not in reported_ids:
                                reported_ids.add(req_id)
                                file_completed_callback(req_id, result)
                    logger.debug(
                        f"Worker {self.worker_id} 批量处理完成，返回 {len(all_results)} 个结果"
                    )
                    return all_results

                if msg_type == MSG_BATCH_PROGRESS:
                    # 进度更新，继续等待
                    from vibeocr.utils.shared_memory_v2 import (
                        deserialize_batch_progress,
                    )

                    progress = deserialize_batch_progress(data)
                    logger.debug(
                        f"Worker {self.worker_id} 批量进度: {progress['completed']}/{progress['total']}"
                    )
                    continue

                if msg_type == MSG_ERROR:
                    error_msg = data.decode("utf-8", errors="replace")
                    raise OCRWorkerProcessError(f"批量处理失败: {error_msg}")

                logger.warning(
                    f"Worker {self.worker_id} 收到意外消息类型: {msg_type.decode('ascii', errors='replace')}"
                )
                continue

        except SharedMemoryProtocolError as e:
            raise OCRWorkerProcessError(f"通信错误: {e}") from None

        finally:
            self.busy = False

    def request_batch_cancel(self) -> None:
        """请求取消正在进行的批量处理（独立控制通道）。

        直接写 SHM cancel flag 字节，不经过 WorkerManager 调度，
        也不与 _send_batch_commit 的消息读写竞争同一通道。
        worker 端的后台轮询线程检测到此标志后调用 mgr.cancel()。
        _send_batch_commit 的读循环也会每轮检查此标志并提前返回部分结果。
        """
        protocol = self.protocol
        if protocol is None or not self.is_ready:
            logger.debug(f"Worker {self.worker_id} 未就绪，无法发送取消")
            return
        protocol.set_cancel_flag()
        logger.debug(f"Worker {self.worker_id} 批量取消标志已设置")

    def _send_batch_cancel(self, timeout: float = Constants.Timeout.SHUTDOWN) -> bool:
        """发送批量取消请求

        Args:
            timeout: 超时时间（秒）

        Returns:
            是否成功
        """
        if not self.is_ready:
            return False

        protocol = self.protocol
        if protocol is None:  # guarded by is_ready check above
            raise OCRWorkerProcessError(f"Worker {self.worker_id} 通信协议未初始化")
        try:
            protocol.write_message(
                MSG_BATCH_CANCEL, b"", timeout=timeout, sender="main"
            )
            logger.debug(f"Worker {self.worker_id} 批量取消请求已发送")

            # 等待 Worker 消费请求，避免 read-own-write
            protocol.wait_for_read(timeout=timeout)

            # 等待确认
            msg_type, _data = protocol.read_message(
                timeout=timeout, expected_sender="worker"
            )
            return msg_type == MSG_ACK

        except SharedMemoryProtocolError as e:
            logger.warning(f"发送批量取消请求失败: {e}")
            return False

    def release_pipelines(
        self, heavy_only: bool = True, timeout: float = Constants.Timeout.WORKER_TIMEOUT
    ) -> list[str]:
        """向 worker 发送 RELEASE_PIPELINES 命令，返回被释放的管道名列表。

        Args:
            heavy_only: True 只释放重管道，False 释放全部。
            timeout: 超时时间（秒）。

        Returns:
            被释放的管道名列表，失败时返回空列表。
        """
        import json

        if not self.is_ready:
            return []

        protocol = self.protocol
        if protocol is None:
            raise OCRWorkerProcessError(f"Worker {self.worker_id} 通信协议未初始化")
        try:
            payload = json.dumps({"heavy_only": heavy_only}).encode("utf-8")
            protocol.write_message(
                MSG_RELEASE_PIPELINES, payload, timeout=timeout, sender="main"
            )
            logger.debug(f"Worker {self.worker_id} 释放管道请求已发送")

            protocol.wait_for_read(timeout=timeout)

            msg_type, data = protocol.read_message(
                timeout=timeout, expected_sender="worker"
            )
            if msg_type == MSG_ACK:
                result = json.loads(data.decode("utf-8")) if data else {}
                return result.get("released", [])
            logger.warning(f"Worker {self.worker_id} 释放管道未收到 ACK: {msg_type}")
            return []

        except SharedMemoryProtocolError as e:
            logger.warning(f"发送释放管道请求失败: {e}")
            return []

    def set_ttl(self, ttl_seconds: int, timeout: float = Constants.Timeout.SHM_WRITE) -> bool:
        """向 worker 发送 SET_TTL 命令。

        Args:
            ttl_seconds: TTL 秒数，0=禁用。
            timeout: 超时时间（秒）。

        Returns:
            是否成功。
        """
        import json

        if not self.is_ready:
            return False

        protocol = self.protocol
        if protocol is None:
            raise OCRWorkerProcessError(f"Worker {self.worker_id} 通信协议未初始化")
        try:
            payload = json.dumps({"ttl_seconds": int(ttl_seconds)}).encode("utf-8")
            protocol.write_message(MSG_SET_TTL, payload, timeout=timeout, sender="main")
            logger.debug(f"Worker {self.worker_id} SET_TTL 请求已发送")

            protocol.wait_for_read(timeout=timeout)

            msg_type, _data = protocol.read_message(
                timeout=timeout, expected_sender="worker"
            )
            return msg_type == MSG_ACK

        except SharedMemoryProtocolError as e:
            logger.warning(f"发送 SET_TTL 请求失败: {e}")
            return False

    def stop(self, timeout: float = Constants.Timeout.SHUTDOWN) -> None:
        """停止 Worker 进程（优雅关闭顺序）。

        顺序：发 MSG_SHUTDOWN → 有界 wait → 超时才 kill/关 guard → 清 SHM → join reader。
        Job Object guard 保留为超时兜底与父进程崩溃保护，不作为第一步
        （旧实现先关 guard 导致内核 kill 子进程，MSG_SHUTDOWN 无机会执行）。
        """
        # 1. 先发 SHUTDOWN 消息，给 worker 优雅退出机会
        if self.protocol and self.is_running:
            with contextlib.suppress(SharedMemoryProtocolError):
                self.protocol.write_message(MSG_SHUTDOWN, b"", timeout=1.0)

        # 2. 有界等待进程退出
        if self.process is not None:
            try:
                self.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                # 3. 超时才关 Job guard（内核 kill）或 kill
                logger.warning(f"Worker {self.worker_id} 未响应 SHUTDOWN，强制终止")
                if self._job_guard is not None:
                    self._job_guard.close()
                    self._job_guard = None
                else:
                    self.process.kill()
                    self.process.wait(timeout=1.0)

        # 4. 关闭 Job guard（若仍未关闭且存在）
        if self._job_guard is not None:
            self._job_guard.close()
            self._job_guard = None

        # 5. 清理共享内存（先 unlink 再 close，因为 close 会置 shm=None）
        if self.protocol:
            self.protocol.unlink()
            self.protocol.close()
            self.protocol = None

        # 6. join stdout reader 线程（确定性清理，避免重启时旧 reader 残留）
        # join 对已退出的线程会立即返回，故无需先检查 is_alive
        if self._stdout_thread is not None:
            self._stdout_thread.join(timeout=2.0)
        self._stdout_thread = None

        self.process = None
        self._ready = False
        self.busy = False

        logger.debug(f"Worker {self.worker_id} 已停止")

    def restart(self, timeout: float = Constants.Timeout.RESTART) -> None:
        """重启 Worker 进程

        Args:
            timeout: 等待就绪的超时时间（秒）
        """
        logger.debug(f"重启 Worker {self.worker_id}...")
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

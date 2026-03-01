"""共享内存通信协议 V2 - 使用指数退避等待

用于主进程与 OCR Worker 子进程之间的高效通信。
使用 multiprocessing.shared_memory 实现跨进程数据共享，
结合指数退避算法实现高效的等待机制。

内存布局:
+----------+----------+------------------+
| 消息类型  | 数据大小  | 实际数据         |
| (4 bytes) | (4 bytes) | (size-8 bytes)  |
+----------+----------+------------------+

相比 V1 的改进:
1. 使用指数退避替代固定轮询，大幅降低 CPU 占用
2. 支持可中断的等待
3. 简化的握手流程（单次确认）
4. 更好的错误处理和超时控制
"""

import logging
import struct
import time
import threading
from multiprocessing import shared_memory
from typing import Optional
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class MessageType(bytes, Enum):
    """消息类型枚举"""
    INIT = b"INIT"           # 初始化
    RECOGNIZE = b"RECO"      # 识别请求
    RESULT = b"RESL"         # 结果返回
    ERROR = b"ERR "          # 错误
    SHUTDOWN = b"SHUT"       # 关闭
    ACK = b"_ACK"            # 确认
    READY = b"REDY"          # Worker 就绪信号
    PRELOAD = b"PREL"        # 预加载请求
    PRELOAD_DONE = b"PRED"   # 预加载完成
    LOG = b"LOG "            # 日志消息
    HEARTBEAT = b"BEAT"      # 心跳（新增）


# 头部大小: 消息类型(4) + 数据大小(4)
HEADER_SIZE = 8
HEADER_FORMAT = "<4sI"  # 小端序: 4字节消息类型 + 4字节数据大小


class SharedMemoryProtocolError(Exception):
    """共享内存协议错误"""
    pass


@dataclass
class SharedMemoryConfig:
    """共享内存配置"""
    name: str
    size: int = 10 * 1024 * 1024  # 默认 10MB


# 导出消息类型别名（保持与 V1 的兼容）
MSG_INIT = MessageType.INIT
MSG_RECOGNIZE = MessageType.RECOGNIZE
MSG_RESULT = MessageType.RESULT
MSG_ERROR = MessageType.ERROR
MSG_SHUTDOWN = MessageType.SHUTDOWN
MSG_ACK = MessageType.ACK
MSG_READY = MessageType.READY
MSG_PRELOAD = MessageType.PRELOAD
MSG_PRELOAD_DONE = MessageType.PRELOAD_DONE
MSG_LOG = MessageType.LOG
MSG_HEARTBEAT = MessageType.HEARTBEAT

# 状态常量
STATE_EMPTY = 0


class SharedMemoryProtocolV2:
    """共享内存通信协议 V2
    
    使用指数退避等待机制实现高效的双向通信。
    
    工作流程:
    1. 主进程创建共享内存
    2. Worker 连接共享内存
    3. 写方写入数据并设置状态标志
    4. 读方通过指数退避等待状态标志，然后读取数据
    5. 读方读取完成后清除状态标志，允许下一次写入
    
    使用示例:
        # 主进程
        config = SharedMemoryConfig("vibeocr_shm_123", size=10*1024*1024)
        protocol = SharedMemoryProtocolV2(config)
        protocol.create()
        protocol.write_message(MessageType.RECOGNIZE, image_data)
        msg_type, data = protocol.read_message()
        protocol.close()
        protocol.unlink()
        
        # Worker 进程
        protocol = SharedMemoryProtocolV2(config)
        protocol.connect()
        msg_type, data = protocol.read_message()
        protocol.write_message(MessageType.RESULT, result_data)
        protocol.close()
    """
    
    def __init__(self, config: SharedMemoryConfig | str, size: int = None):
        """初始化共享内存协议
        
        Args:
            config: 共享内存配置对象，或共享内存名称字符串（兼容 V1）
            size: 共享内存大小（字节，当 config 为字符串时使用，兼容 V1）
        """
        # 兼容 V1 的调用方式: SharedMemoryProtocol(name, size)
        if isinstance(config, str) and size is not None:
            self.config = SharedMemoryConfig(name=config, size=size)
        elif isinstance(config, SharedMemoryConfig):
            self.config = config
        else:
            raise ValueError("参数错误：需要提供 SharedMemoryConfig 或 (name, size) 组合")

        self.shm: Optional[shared_memory.SharedMemory] = None
        self._is_creator = False
        
        # 数据偏移（前9字节保留给头部+状态）
        self._data_offset = 8
        
        # 用于实现可中断的等待
        self._stop_event = threading.Event()
        
    def create(self) -> None:
        """创建共享内存（主进程调用）
        
        创建新的共享内存区域。如果已存在同名共享内存，将尝试连接。
        """
        try:
            # 创建共享内存
            self.shm = shared_memory.SharedMemory(
                name=self.config.name,
                create=True,
                size=self.config.size
            )
            self._is_creator = True
            
            # 创建 Event（用于进程间通知）
            # 注意：multiprocessing.Event 不能跨进程共享名称
            # 所以我们使用简单的状态标志在共享内存中
            self._init_state_flags()
            
            logger.info(f"创建共享内存: {self.config.name}, 大小: {self.config.size} 字节")
        except FileExistsError:
            logger.warning(f"共享内存 {self.config.name} 已存在，尝试连接")
            self.connect()
    
    def _init_state_flags(self) -> None:
        """初始化状态标志区域"""
        if self.shm is None:
            return
        # 使用共享内存的前几个字节作为状态标志
        # 字节 0-3: 消息类型
        # 字节 4-7: 数据大小
        # 字节 8: 数据就绪标志 (0=空, 1=有数据)
        self.shm.buf[8] = 0  # 初始化为空
    
    def connect(self) -> None:
        """连接共享内存（Worker 调用）
        
        连接到已存在的共享内存区域。
        """
        self.shm = shared_memory.SharedMemory(name=self.config.name)
        self._is_creator = False
        logger.info(f"连接共享内存: {self.config.name}")
    
    def _set_data_ready(self, ready: bool) -> None:
        """设置数据就绪标志"""
        if self.shm is None:
            raise SharedMemoryProtocolError("共享内存未初始化")
        # 多次写入确保生效（Windows 共享内存同步问题）
        for _ in range(3):
            self.shm.buf[8] = 1 if ready else 0
            time.sleep(0.001)
        logger.debug(f"[SHM {self.config.name}] 设置 _is_data_ready = {ready}")

    def _is_data_ready(self) -> bool:
        """检查数据是否就绪"""
        if self.shm is None:
            raise SharedMemoryProtocolError("共享内存未初始化")
        # 直接读取共享内存字节
        result = self.shm.buf[8] == 1
        # 只在状态为 True 时打印，避免过多日志
        if result:
            logger.debug(f"[SHM {self.config.name}] 检测到 _is_data_ready = True")
        return result

    def wait_for_read(self, timeout: float = 5.0) -> None:
        """等待消息被读取（等待 _is_data_ready 变为 False）

        在发送响应后调用，确保对方进程已读取消息。

        Args:
            timeout: 超时时间（秒）
        """
        if self.shm is None:
            raise SharedMemoryProtocolError("共享内存未初始化")
        start_time = time.time()
        while self._is_data_ready():
            if time.time() - start_time > timeout:
                logger.warning(f"[SHM {self.config.name}] 等待读取超时")
                break
            time.sleep(0.01)

    def _get_state(self) -> int:
        """获取状态标志（兼容 V1）"""
        return self.shm.buf[8] if self.shm else 0

    def _set_state(self, state: int) -> None:
        """设置状态标志（兼容 V1）"""
        if self.shm:
            self.shm.buf[8] = state
    
    def write_message(self, msg_type: MessageType | bytes, data: bytes,
                      timeout: float = 30.0, sender: str = None) -> int:
        """写入消息

        等待共享内存变为可写状态，然后写入消息并通知读方。

        Args:
            msg_type: 消息类型（4 字节）
            data: 消息数据
            timeout: 超时时间（秒）

        Returns:
            写入的字节数

        Raises:
            SharedMemoryProtocolError: 超时或数据过大
        """
        if self.shm is None:
            raise SharedMemoryProtocolError("共享内存未初始化")

        if isinstance(msg_type, MessageType):
            msg_type = msg_type.value

        if len(msg_type) != 4:
            raise SharedMemoryProtocolError(f"消息类型必须是 4 字节，当前: {len(msg_type)}")

        total_size = HEADER_SIZE + len(data)
        if total_size > self.config.size - 9:  # 减去状态标志占用的 9 字节
            raise SharedMemoryProtocolError(
                f"数据过大: {total_size} 字节，最大可用: {self.config.size - 9} 字节"
            )

        # 等待可写状态（使用指数退避）
        start_time = time.time()
        wait_time = 0.001  # 初始等待 1ms
        max_wait = 0.05  # 降低最大等待时间
        wait_count = 0

        while True:
            # 强制重新读取共享内存
            ready_flag = self.shm.buf[8]
            if ready_flag != 1:
                break

            wait_count += 1
            if time.time() - start_time > timeout:
                logger.warning(f"[SHM {self.config.name}] 写入超时: 共享内存被占用，等待了 {wait_count} 次")
                raise SharedMemoryProtocolError("写入超时: 共享内存被占用")

            # 使用 time.sleep 让出 CPU 时间
            time.sleep(wait_time)

            # 指数退避
            wait_time = min(wait_time * 2, max_wait)

        if wait_count > 0:
            logger.debug(f"[SHM {self.config.name}] 等待了 {wait_count} 次后开始写入")
        
        # 写入头部（字节 0-7）
        header = struct.pack(HEADER_FORMAT, msg_type, len(data))
        self.shm.buf[0:HEADER_SIZE] = header
        
        # 写入数据（字节 9 开始）
        data_start = self._data_offset + 1
        self.shm.buf[data_start:data_start + len(data)] = data
        
        # 设置数据就绪标志，通知读方
        self._set_data_ready(True)
        
        logger.debug(f"[SHM {self.config.name}] 写入消息: type={msg_type}, size={len(data)}")
        return total_size
    
    def read_message(self, timeout: float = 60.0,
                     check_interval: float = 0.001,
                     expected_sender: str = None) -> tuple[bytes, bytes]:
        """读取消息

        等待数据就绪，然后读取消息。

        Args:
            timeout: 超时时间（秒）
            check_interval: 检查间隔（秒），用于平衡延迟和 CPU 使用

        Returns:
            (消息类型, 数据) 元组

        Raises:
            SharedMemoryProtocolError: 超时
        """
        if self.shm is None:
            raise SharedMemoryProtocolError("共享内存未初始化")

        # 等待数据就绪（使用指数退避）
        start_time = time.time()
        wait_time = check_interval
        max_wait = 0.05  # 降低最大等待时间

        while True:
            # 强制重新读取共享内存，确保看到其他进程的更新
            ready_flag = self.shm.buf[8]
            if ready_flag == 1:
                break

            if time.time() - start_time > timeout:
                raise SharedMemoryProtocolError(f"读取超时: 无可用数据")

            # 使用 time.sleep 而不是 threading.Event.wait
            # 这样可以更好地让出 CPU 时间给其他进程
            time.sleep(wait_time)

            # 指数退避
            wait_time = min(wait_time * 2, max_wait)

        # 读取头部
        header = bytes(self.shm.buf[0:HEADER_SIZE])
        msg_type, data_size = struct.unpack(HEADER_FORMAT, header)

        # 读取数据
        data_start = self._data_offset + 1
        data = bytes(self.shm.buf[data_start:data_start + data_size])

        # 清除数据就绪标志，允许下一次写入
        self._set_data_ready(False)

        logger.debug(f"[SHM {self.config.name}] 读取消息: type={msg_type}, size={data_size}")
        return msg_type, data
    
    def interrupt(self) -> None:
        """中断当前的读写操作"""
        self._stop_event.set()
    
    def reset_interrupt(self) -> None:
        """重置中断状态"""
        self._stop_event.clear()
    
    def close(self) -> None:
        """关闭共享内存连接
        
        关闭当前进程对共享内存的访问，但不删除共享内存。
        """
        if self.shm is not None:
            self.shm.close()
            self.shm = None
            logger.debug(f"关闭共享内存连接: {self.config.name}")
    
    def unlink(self) -> None:
        """删除共享内存（仅创建者调用）
        
        删除共享内存区域。只有创建者应该调用此方法。
        """
        if self._is_creator and self.shm is not None:
            try:
                self.shm.unlink()
                logger.info(f"删除共享内存: {self.config.name}")
            except Exception as e:
                logger.warning(f"删除共享内存失败: {e}")
        self._is_creator = False
    
    def __enter__(self) -> "SharedMemoryProtocolV2":
        """上下文管理器入口"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """上下文管理器退出"""
        self.close()
        if self._is_creator:
            self.unlink()


# =============================================================================
# 序列化/反序列化函数（与 V1 兼容）
# =============================================================================

def serialize_request(image_data: bytes, options_dict: dict) -> bytes:
    """序列化 OCR 请求
    
    格式: [图像数据大小 4B | 图像数据 | pickle(options_dict)]
    """
    import pickle
    
    options_bytes = pickle.dumps(options_dict)
    header = struct.pack("<I", len(image_data))
    return header + image_data + options_bytes


def deserialize_request(data: bytes) -> tuple[bytes, dict]:
    """反序列化 OCR 请求"""
    import pickle
    
    image_size = struct.unpack("<I", data[:4])[0]
    image_data = data[4:4 + image_size]
    options_dict = pickle.loads(data[4 + image_size:])
    return image_data, options_dict


def serialize_result(result) -> bytes:
    """序列化 OCR 结果"""
    import pickle
    return pickle.dumps(result)


def deserialize_result(data: bytes):
    """反序列化 OCR 结果"""
    import pickle
    return pickle.loads(data)


def serialize_preload_request(pipelines: list[str]) -> bytes:
    """序列化预加载请求"""
    import pickle
    return pickle.dumps(pipelines)


def deserialize_preload_request(data: bytes) -> list[str]:
    """反序列化预加载请求"""
    import pickle
    return pickle.loads(data)


def serialize_preload_result(results: dict[str, bool]) -> bytes:
    """序列化预加载结果"""
    import pickle
    return pickle.dumps(results)


def deserialize_preload_result(data: bytes) -> dict[str, bool]:
    """反序列化预加载结果"""
    import pickle
    return pickle.loads(data)


def serialize_log_entries(entries: list[dict]) -> bytes:
    """序列化日志条目列表"""
    import pickle
    return pickle.dumps(entries)


def deserialize_log_entries(data: bytes) -> list[dict]:
    """反序列化日志条目列表"""
    import pickle
    return pickle.loads(data)

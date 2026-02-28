"""共享内存通信协议

用于主进程与 OCR Worker 子进程之间的通信。
使用 multiprocessing.shared_memory 实现跨进程数据共享。

内存布局:
+----------+----------+------------------+
| 消息类型  | 数据大小  | 实际数据         |
| (4 bytes) | (4 bytes) | (size-8 bytes)  |
+----------+----------+------------------+
"""

import logging
import struct
import time
from multiprocessing import shared_memory
from typing import Optional

logger = logging.getLogger(__name__)

# 消息类型常量
MSG_INIT = b"INIT"      # 初始化
MSG_RECOGNIZE = b"RECO"  # 识别请求
MSG_RESULT = b"RESL"     # 结果返回
MSG_ERROR = b"ERR "      # 错误
MSG_SHUTDOWN = b"SHUT"   # 关闭
MSG_ACK = b"_ACK"        # 确认
MSG_PRELOAD = b"PREL"    # 预加载请求
MSG_PRELOAD_DONE = b"PRED"  # 预加载完成
MSG_LOG = b"LOG "        # 日志消息

# 头部大小: 消息类型(4) + 数据大小(4)
HEADER_SIZE = 8
HEADER_FORMAT = "<4sI"  # 小端序: 4字节消息类型 + 4字节数据大小


class SharedMemoryProtocolError(Exception):
    """共享内存协议错误"""
    pass


class SharedMemoryProtocol:
    """共享内存通信协议

    用于主进程与 Worker 子进程之间的双向通信。

    使用示例:
        # 主进程
        protocol = SharedMemoryProtocol("vibeocr_shm_123", 10*1024*1024)
        protocol.create()
        protocol.write_message(MSG_RECOGNIZE, image_data)
        msg_type, data = protocol.read_message()
        protocol.close()
        protocol.unlink()  # 仅创建者调用

        # Worker 进程
        protocol = SharedMemoryProtocol("vibeocr_shm_123", 10*1024*1024)
        protocol.connect()
        msg_type, data = protocol.read_message()
        protocol.write_message(MSG_RESULT, result_data)
        protocol.close()
    """

    def __init__(self, name: str, size: int = 10 * 1024 * 1024):
        """初始化共享内存协议

        Args:
            name: 共享内存名称
            size: 共享内存大小（字节），默认 10MB
        """
        self.name = name
        self.size = size
        self.shm: Optional[shared_memory.SharedMemory] = None
        self._is_creator = False

        # 用于同步的状态标志（使用共享内存的前几个字节）
        # 0 = 空，可写
        # 1 = 有数据，可读
        self._state_offset = 0
        self._data_offset = 4  # 数据从第 4 字节开始

    def create(self) -> None:
        """创建共享内存（主进程调用）

        创建新的共享内存区域。如果已存在同名共享内存，将引发错误。
        """
        try:
            self.shm = shared_memory.SharedMemory(
                name=self.name,
                create=True,
                size=self.size
            )
            self._is_creator = True
            # 初始化状态为空
            self._set_state(0)
            logger.info(f"创建共享内存: {self.name}, 大小: {self.size} 字节")
        except FileExistsError:
            # 如果已存在，尝试连接
            logger.warning(f"共享内存 {self.name} 已存在，尝试连接")
            self.connect()

    def connect(self) -> None:
        """连接共享内存（Worker 调用）

        连接到已存在的共享内存区域。
        """
        self.shm = shared_memory.SharedMemory(name=self.name)
        self._is_creator = False
        logger.info(f"连接共享内存: {self.name}")

    def _set_state(self, state: int) -> None:
        """设置状态标志"""
        if self.shm is None:
            raise SharedMemoryProtocolError("共享内存未初始化")
        self.shm.buf[self._state_offset] = state

    def _get_state(self) -> int:
        """获取状态标志"""
        if self.shm is None:
            raise SharedMemoryProtocolError("共享内存未初始化")
        return self.shm.buf[self._state_offset]

    def write_message(self, msg_type: bytes, data: bytes, timeout: float = 30.0) -> int:
        """写入消息

        等待共享内存变为可写状态，然后写入消息。

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

        if len(msg_type) != 4:
            raise SharedMemoryProtocolError(f"消息类型必须是 4 字节，当前: {len(msg_type)}")

        total_size = HEADER_SIZE + len(data)
        if total_size > self.size - 4:  # 减去状态标志占用的 4 字节
            raise SharedMemoryProtocolError(
                f"数据过大: {total_size} 字节，最大可用: {self.size - 4} 字节"
            )

        # 等待可写状态
        start_time = time.time()
        while self._get_state() != 0:
            if time.time() - start_time > timeout:
                raise SharedMemoryProtocolError("写入超时: 共享内存被占用")
            time.sleep(0.001)

        # 写入头部
        header = struct.pack(HEADER_FORMAT, msg_type, len(data))
        offset = self._data_offset
        self.shm.buf[offset:offset + HEADER_SIZE] = header

        # 写入数据
        offset += HEADER_SIZE
        self.shm.buf[offset:offset + len(data)] = data

        # 设置状态为有数据
        self._set_state(1)

        logger.debug(f"写入消息: type={msg_type}, size={len(data)}")
        return total_size

    def read_message(self, timeout: float = 60.0) -> tuple[bytes, bytes]:
        """读取消息

        等待共享内存变为可读状态，然后读取消息。

        Args:
            timeout: 超时时间（秒）

        Returns:
            (消息类型, 数据) 元组

        Raises:
            SharedMemoryProtocolError: 超时
        """
        if self.shm is None:
            raise SharedMemoryProtocolError("共享内存未初始化")

        # 等待可读状态
        start_time = time.time()
        while self._get_state() != 1:
            if time.time() - start_time > timeout:
                raise SharedMemoryProtocolError("读取超时: 无可用数据")
            time.sleep(0.001)

        # 读取头部
        offset = self._data_offset
        header = bytes(self.shm.buf[offset:offset + HEADER_SIZE])
        msg_type, data_size = struct.unpack(HEADER_FORMAT, header)

        # 读取数据
        offset += HEADER_SIZE
        data = bytes(self.shm.buf[offset:offset + data_size])

        # 设置状态为空
        self._set_state(0)

        logger.debug(f"读取消息: type={msg_type}, size={data_size}")
        return msg_type, data

    def close(self) -> None:
        """关闭共享内存连接

        关闭当前进程对共享内存的访问，但不删除共享内存。
        """
        if self.shm is not None:
            self.shm.close()
            self.shm = None
            logger.debug(f"关闭共享内存连接: {self.name}")

    def unlink(self) -> None:
        """删除共享内存（仅创建者调用）

        删除共享内存区域。只有创建者应该调用此方法。
        """
        if self._is_creator and self.shm is not None:
            try:
                self.shm.unlink()
                logger.info(f"删除共享内存: {self.name}")
            except Exception as e:
                logger.warning(f"删除共享内存失败: {e}")
        self._is_creator = False

    def __enter__(self) -> "SharedMemoryProtocol":
        """上下文管理器入口"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """上下文管理器退出"""
        self.close()
        if self._is_creator:
            self.unlink()


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
    """序列化预加载请求
    
    Args:
        pipelines: 管道名称列表
    
    Returns:
        序列化后的字节数据
    """
    import pickle
    return pickle.dumps(pipelines)


def deserialize_preload_request(data: bytes) -> list[str]:
    """反序列化预加载请求"""
    import pickle
    return pickle.loads(data)


def serialize_preload_result(results: dict[str, bool]) -> bytes:
    """序列化预加载结果
    
    Args:
        results: {pipeline_name: success} 结果字典
    
    Returns:
        序列化后的字节数据
    """
    import pickle
    return pickle.dumps(results)


def deserialize_preload_result(data: bytes) -> dict[str, bool]:
    """反序列化预加载结果"""
    import pickle
    return pickle.loads(data)


def serialize_log_entries(entries: list[dict]) -> bytes:
    """序列化日志条目列表
    
    Args:
        entries: 日志条目列表，每个条目包含 level, name, message, time
    
    Returns:
        序列化后的字节数据
    """
    import pickle
    return pickle.dumps(entries)


def deserialize_log_entries(data: bytes) -> list[dict]:
    """反序列化日志条目列表"""
    import pickle
    return pickle.loads(data)

"""
Tests for SharedMemoryProtocol V2.

Tests the shared memory communication protocol for subprocess OCR.
"""

from typing import Any

import pytest

# Check if shared_memory module is available
try:
    from vibeocr.utils.shared_memory_v2 import (
        MessageType,
        SharedMemoryProtocolError,
        deserialize_request,
        deserialize_result,
        serialize_request,
        serialize_result,
    )
    from vibeocr.utils.shared_memory_v2 import (
        SharedMemoryProtocolV2 as SharedMemoryProtocol,
    )

    # V2 消息类型
    MSG_INIT = MessageType.INIT
    MSG_RECOGNIZE = MessageType.RECOGNIZE
    MSG_RESULT = MessageType.RESULT
    MSG_ERROR = MessageType.ERROR
    MSG_SHUTDOWN = MessageType.SHUTDOWN
    MSG_ACK = MessageType.ACK
    HAS_SHARED_MEMORY = True
except ImportError:
    # 可选依赖缺失时占位；测试由 @skipif(not HAS_SHARED_MEMORY) 跳过，
    # 这些占位值运行时永不被调用。用 Any 类型满足静态绑定分析。
    MessageType: Any = None  # type: ignore[assignment]
    SharedMemoryProtocolError: Any = None  # type: ignore[assignment]
    deserialize_request: Any = None  # type: ignore[assignment]
    deserialize_result: Any = None  # type: ignore[assignment]
    serialize_request: Any = None  # type: ignore[assignment]
    serialize_result: Any = None  # type: ignore[assignment]
    SharedMemoryProtocol: Any = None  # type: ignore[assignment]
    MSG_INIT: Any = None
    MSG_RECOGNIZE: Any = None
    MSG_RESULT: Any = None
    MSG_ERROR: Any = None
    MSG_SHUTDOWN: Any = None
    MSG_ACK: Any = None
    HAS_SHARED_MEMORY = False


@pytest.mark.skipif(not HAS_SHARED_MEMORY, reason="shared_memory module not available")
class TestSharedMemoryProtocol:
    """Tests for SharedMemoryProtocol class."""

    def test_init(self):
        """Test protocol initialization."""
        protocol = SharedMemoryProtocol("test_shm_init", 1024)
        assert protocol.config.name == "test_shm_init"
        assert protocol.config.size == 1024
        assert protocol.shm is None
        assert not protocol._is_creator

    def test_create_and_close(self):
        """Test creating and closing shared memory."""
        protocol = SharedMemoryProtocol("test_shm_create", 1024)
        protocol.create()

        assert protocol.shm is not None
        assert protocol._is_creator

        protocol.close()
        assert protocol.shm is None

        protocol.unlink()

    def test_connect_without_create(self):
        """Test connecting to non-existent shared memory raises error."""
        protocol = SharedMemoryProtocol("test_shm_nonexistent", 1024)

        with pytest.raises((FileNotFoundError, SharedMemoryProtocolError)):
            protocol.connect()

    def test_write_read_message(self):
        """Test writing and reading messages."""
        protocol = SharedMemoryProtocol("test_shm_rw", 4096)
        protocol.create()

        try:
            # Write a message
            test_data = b"Hello, World!"
            protocol.write_message(MSG_RECOGNIZE, test_data, timeout=5.0)

            # Read the message
            msg_type, data = protocol.read_message(timeout=5.0)

            assert msg_type == MSG_RECOGNIZE
            assert data == test_data
        finally:
            protocol.close()
            protocol.unlink()

    def test_write_read_large_data(self):
        """Test writing and reading large data."""
        size = 1024 * 100  # 100KB
        protocol = SharedMemoryProtocol("test_shm_large", size + 1024)
        protocol.create()

        try:
            # Generate large data
            large_data = b"x" * size
            protocol.write_message(MSG_RESULT, large_data, timeout=5.0)

            # Read back
            _msg_type, data = protocol.read_message(timeout=5.0)

            assert _msg_type == MSG_RESULT
            assert len(data) == size
            assert data == large_data
        finally:
            protocol.close()
            protocol.unlink()

    def test_invalid_message_type_length(self):
        """Test that invalid message type length raises error."""
        protocol = SharedMemoryProtocol("test_shm_invalid", 1024)
        protocol.create()

        try:
            with pytest.raises(SharedMemoryProtocolError):
                protocol.write_message(
                    b"INVALID", b"data", timeout=5.0
                )  # 7 bytes, should be 4
        finally:
            protocol.close()
            protocol.unlink()

    def test_data_too_large(self):
        """Test that oversized data raises error."""
        protocol = SharedMemoryProtocol("test_shm_oversize", 100)
        protocol.create()

        try:
            large_data = b"x" * 200  # Larger than shared memory
            with pytest.raises(SharedMemoryProtocolError):
                protocol.write_message(MSG_RECOGNIZE, large_data, timeout=5.0)
        finally:
            protocol.close()
            protocol.unlink()

    def test_context_manager(self):
        """Test using protocol as context manager."""
        with SharedMemoryProtocol("test_shm_ctx", 1024) as protocol:
            protocol.create()
            assert protocol.shm is not None
            # Context manager should close on exit

    def test_multiple_messages(self):
        """Test multiple write/read cycles."""
        protocol = SharedMemoryProtocol("test_shm_multi", 4096)
        protocol.create()

        try:
            for i in range(5):
                test_data = f"Message {i}".encode()
                protocol.write_message(MSG_RECOGNIZE, test_data, timeout=5.0)
                _msg_type, data = protocol.read_message(timeout=5.0)
                assert data == test_data
        finally:
            protocol.close()
            protocol.unlink()


@pytest.mark.skipif(not HAS_SHARED_MEMORY, reason="shared_memory module not available")
class TestSerialization:
    """Tests for request/result serialization."""

    def test_serialize_deserialize_request(self):
        """Test request serialization and deserialization."""
        image_data = b"fake_image_bytes"
        options = {
            "use_angle_cls": True,
            "lang": "ch",
            "det_db_thresh": 0.3,
        }

        serialized = serialize_request(image_data, options)
        assert isinstance(serialized, bytes)

        img_out, opt_out = deserialize_request(serialized)
        assert img_out == image_data
        assert opt_out == options

    def test_serialize_deserialize_result(self):
        """Test result serialization and deserialization."""
        # Mock OCR result
        result = {
            "raw_text": "Hello World",
            "text_with_scores": [("Hello", 0.99), ("World", 0.98)],
            "avg_score": 0.985,
        }

        serialized = serialize_result(result)
        assert isinstance(serialized, bytes)

        result_out = deserialize_result(serialized)
        assert result_out["raw_text"] == "Hello World"
        assert len(result_out["text_with_scores"]) == 2

    def test_serialize_empty_options(self):
        """Test serialization with empty options."""
        image_data = b"image_data"
        options = {}

        serialized = serialize_request(image_data, options)
        img_out, opt_out = deserialize_request(serialized)

        assert img_out == image_data
        assert opt_out == {}

    def test_serialize_large_image(self):
        """Test serialization with large image data."""
        # Simulate a 1MB image
        image_data = b"x" * (1024 * 1024)
        options = {"use_angle_cls": True}

        serialized = serialize_request(image_data, options)
        img_out, opt_out = deserialize_request(serialized)

        assert len(img_out) == 1024 * 1024
        assert opt_out["use_angle_cls"] is True


class TestRecognizeBatchSerialization:
    """Tests for the RCBG (multi-image batch) request/result serialization."""

    def test_rcbg_message_type_value(self):
        """RCBG must be a unique 4-byte tag distinct from existing tags."""
        # The new tag
        assert MessageType.RECOGNIZE_BATCH.value == b"RCBG"
        # Must not collide with any existing 4-byte tag
        existing = {
            MessageType.RECOGNIZE.value,
            MessageType.RESULT.value,
            MessageType.BATCH_ADD.value,
            MessageType.BATCH_COMMIT.value,
            MessageType.BATCH_RESULT.value,
        }
        assert MessageType.RECOGNIZE_BATCH.value not in existing

    def test_serialize_deserialize_batch_request_multi_images(self):
        """Round-trip a multi-image batch request preserving order & options."""
        from vibeocr.utils.shared_memory_v2 import (
            deserialize_recognize_batch_request,
            serialize_recognize_batch_request,
        )

        images = [b"PNG_PAGE_1", b"PNG_PAGE_2_LONGER", b""]
        options = {
            "pipeline": "OCR",
            "use_doc_orientation_classify": True,
            "lang": "ch",
        }

        serialized = serialize_recognize_batch_request(images, options)
        assert isinstance(serialized, bytes)

        out_images, out_options = deserialize_recognize_batch_request(serialized)
        assert out_images == images  # order + values preserved
        assert out_options == options

    def test_serialize_deserialize_batch_request_single_image(self):
        """A single-image batch must still round-trip correctly."""
        from vibeocr.utils.shared_memory_v2 import (
            deserialize_recognize_batch_request,
            serialize_recognize_batch_request,
        )

        images = [b"only_page"]
        options = {"pipeline": "OCR"}

        serialized = serialize_recognize_batch_request(images, options)
        out_images, out_options = deserialize_recognize_batch_request(serialized)

        assert out_images == images
        assert out_options == options

    def test_serialize_deserialize_batch_request_empty_options(self):
        """Empty options dict round-trips."""
        from vibeocr.utils.shared_memory_v2 import (
            deserialize_recognize_batch_request,
            serialize_recognize_batch_request,
        )

        images = [b"a", b"b"]
        serialized = serialize_recognize_batch_request(images, {})
        out_images, out_options = deserialize_recognize_batch_request(serialized)
        assert out_images == images
        assert out_options == {}

    def test_serialize_deserialize_batch_request_large_images(self):
        """Multiple large images (1MB each) round-trip without truncation."""
        from vibeocr.utils.shared_memory_v2 import (
            deserialize_recognize_batch_request,
            serialize_recognize_batch_request,
        )

        images = [b"x" * (1024 * 1024) for _ in range(3)]
        serialized = serialize_recognize_batch_request(images, {"pipeline": "OCR"})
        out_images, _ = deserialize_recognize_batch_request(serialized)
        assert [len(img) for img in out_images] == [1024 * 1024] * 3

    def test_serialize_deserialize_batch_result(self):
        """Batch result list round-trips preserving order."""
        from vibeocr.models.ocr_result import OCRResult
        from vibeocr.utils.shared_memory_v2 import (
            deserialize_recognize_batch_result,
            serialize_recognize_batch_result,
        )

        results = [
            OCRResult(raw_text="page1", text_blocks=[]),
            OCRResult(raw_text="page2", text_blocks=[]),
            OCRResult(raw_text="", text_blocks=[]),
        ]
        serialized = serialize_recognize_batch_result(results)
        out = deserialize_recognize_batch_result(serialized)
        assert len(out) == 3
        assert [r.raw_text for r in out] == ["page1", "page2", ""]

    def test_serialize_deserialize_batch_result_empty(self):
        """Empty result list round-trips."""
        from vibeocr.utils.shared_memory_v2 import (
            deserialize_recognize_batch_result,
            serialize_recognize_batch_result,
        )

        serialized = serialize_recognize_batch_result([])
        out = deserialize_recognize_batch_result(serialized)
        assert out == []

    def test_no_name_collision_with_badd_funcs(self):
        """RCBG serializers must not shadow the BADD queue serializers."""
        from vibeocr.utils import shared_memory_v2 as sm

        # Both families must coexist as distinct callables
        assert sm.serialize_recognize_batch_request is not sm.serialize_batch_request
        assert (
            sm.deserialize_recognize_batch_request is not sm.deserialize_batch_request
        )

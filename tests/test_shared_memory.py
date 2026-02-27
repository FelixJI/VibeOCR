"""
Tests for SharedMemoryProtocol.

Tests the shared memory communication protocol for subprocess OCR.
"""

import os
import time
import pytest

# Check if shared_memory module is available
try:
    from vibeocr.utils.shared_memory import (
        SharedMemoryProtocol,
        SharedMemoryProtocolError,
        MSG_INIT,
        MSG_RECOGNIZE,
        MSG_RESULT,
        MSG_ERROR,
        MSG_SHUTDOWN,
        MSG_ACK,
        serialize_request,
        deserialize_request,
        serialize_result,
        deserialize_result,
    )
    HAS_SHARED_MEMORY = True
except ImportError:
    HAS_SHARED_MEMORY = False


@pytest.mark.skipif(not HAS_SHARED_MEMORY, reason="shared_memory module not available")
class TestSharedMemoryProtocol:
    """Tests for SharedMemoryProtocol class."""

    def test_init(self):
        """Test protocol initialization."""
        protocol = SharedMemoryProtocol("test_shm_init", 1024)
        assert protocol.name == "test_shm_init"
        assert protocol.size == 1024
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

        with pytest.raises(Exception):  # FileNotFoundError on most systems
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
            msg_type, data = protocol.read_message(timeout=5.0)

            assert msg_type == MSG_RESULT
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
                protocol.write_message(b"INVALID", b"data", timeout=5.0)  # 7 bytes, should be 4
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
                msg_type, data = protocol.read_message(timeout=5.0)
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

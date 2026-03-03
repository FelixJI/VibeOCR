"""测试批量共享内存消息序列化"""

from vibeocr.utils.shared_memory_v2 import (
    MessageType,
    deserialize_batch_commit,
    deserialize_batch_progress,
    deserialize_batch_request,
    deserialize_batch_result,
    serialize_batch_commit,
    serialize_batch_progress,
    serialize_batch_request,
    serialize_batch_result,
)


def test_batch_request_serialization():
    """测试批量请求序列化/反序列化"""
    request_id = "test-123"
    image_data = b"\x89PNG\r\n\x1a\n" + b"fake_image_data" * 100
    options = {"use_gpu": True, "lang": "ch"}

    serialized = serialize_batch_request(request_id, image_data, options)
    req_id, img_data, opts = deserialize_batch_request(serialized)

    assert req_id == request_id
    assert img_data == image_data
    assert opts == options


def test_batch_commit_serialization():
    """测试批量提交序列化/反序列化"""
    options = {
        "use_doc_orientation_classify": True,
        "use_doc_unwarping": True,
        "use_textline_orientation": False,
    }

    serialized = serialize_batch_commit(options)
    result = deserialize_batch_commit(serialized)

    assert result == options


def test_batch_result_serialization():
    """测试批量结果序列化/反序列化"""
    # 模拟结果
    results = {
        "req-1": {"text": "Hello", "confidence": 0.95},
        "req-2": {"error": "Failed to process"},
    }

    serialized = serialize_batch_result(results)
    result = deserialize_batch_result(serialized)

    assert result == results


def test_batch_progress_serialization():
    """测试批量进度序列化/反序列化"""
    progress = {"completed": 5, "total": 10, "current_file": "image_005.png"}

    serialized = serialize_batch_progress(**progress)
    result = deserialize_batch_progress(serialized)

    assert result == progress


def test_batch_message_types():
    """测试批量消息类型定义"""
    assert MessageType.BATCH_ADD == b"BADD"
    assert MessageType.BATCH_COMMIT == b"BCOM"
    assert MessageType.BATCH_RESULT == b"BRES"
    assert MessageType.BATCH_CANCEL == b"BCAN"
    assert MessageType.BATCH_PROGRESS == b"BPRG"

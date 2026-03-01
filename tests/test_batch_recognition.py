"""Batch recognition integration tests

Tests for batch add, commit, cancel functionality.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from vibeocr.models.batch_request import (
    BatchRequest,
    BatchRequestStatus,
    PreprocessOptions,
)
from vibeocr.utils.shared_memory_v2 import (
    serialize_batch_request,
    deserialize_batch_request,
    serialize_batch_commit,
    deserialize_batch_commit,
    serialize_batch_result,
    deserialize_batch_result,
)
from vibeocr.workers.batch_queue_manager import BatchQueueManager


def test_batch_request_model_flow():
    """Test batch request model flow"""
    request = BatchRequest(
        file_path="/test/image.png",
        file_name="image.png",
        image_data=b"fake_image_data",
        options={"lang": "ch"}
    )

    assert request.status == BatchRequestStatus.PENDING

    request.mark_processing()
    assert request.status == BatchRequestStatus.PROCESSING
    assert request.started_at is not None

    result = {"text": "Hello World"}
    request.mark_completed(result)
    assert request.status == BatchRequestStatus.COMPLETED
    assert request.result == result
    assert request.is_finished


    print("[OK] batch_request_model_flow")


def test_preprocess_options_serialization():
    """Test preprocess options serialization"""
    options = PreprocessOptions(
        use_doc_orientation_classify=True,
        use_doc_unwarping=False,
        use_textline_orientation=True
    )

    data = options.to_dict()
    restored = PreprocessOptions.from_dict(data)

    assert restored.use_doc_orientation_classify == options.use_doc_orientation_classify
    assert restored.use_doc_unwarping == options.use_doc_unwarping
    assert restored.use_textline_orientation == options.use_textline_orientation

    print("[OK] preprocess_options_serialization")


def test_shared_memory_batch_messages():
    """Test shared memory batch messages"""
    # Batch request
    request_id = "test-123"
    image_data = b"fake_image_data"
    options = {"lang": "ch"}

    serialized = serialize_batch_request(request_id, image_data, options)
    req_id, img_data, opts = deserialize_batch_request(serialized)

    assert req_id == request_id
    assert img_data == image_data
    assert opts == options

    # Batch commit
    commit_opts = {"use_doc_orientation_classify": True}
    serialized = serialize_batch_commit(commit_opts)
    result = deserialize_batch_commit(serialized)
    assert result == commit_opts

    # Batch result
    results = {"req-1": {"text": "Hello"}}
    serialized = serialize_batch_result(results)
    restored = deserialize_batch_result(serialized)
    assert restored == results

    print("[OK] shared_memory_batch_messages")


class MockPipeline:
    """Mock pipeline for testing"""

    def predict(self, images, **kwargs):
        for i in range(len(images)):
            yield {"text": f"Result {i}", "confidence": 0.95}


def test_batch_queue_manager_basic():
    """Test batch queue manager basic flow"""
    pipeline = MockPipeline()
    manager = BatchQueueManager(pipeline, max_batch_size=4)

    # Add requests
    for i in range(3):
        manager.add_request(
            image_data=b"image_data",
            options={},
            file_name=f"test_{i}.png"
        )

    assert manager.get_queue_size() == 3

    # Commit
    options = PreprocessOptions()
    results = manager.commit(options)

    assert len(results) == 3
    print("[OK] batch_queue_manager_basic")


if __name__ == "__main__":
    test_batch_request_model_flow()
    test_preprocess_options_serialization()
    test_shared_memory_batch_messages()
    test_batch_queue_manager_basic()
    print("All integration tests passed!")

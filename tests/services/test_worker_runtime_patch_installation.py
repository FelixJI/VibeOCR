"""Import-boundary regression for worker restart-state installation."""


def test_worker_manager_installs_runtime_state_patch() -> None:
    from vibeocr.services import worker_manager
    from vibeocr.services.ocr_worker_process import OCRWorkerProcess

    assert worker_manager.WorkerManager is not None
    assert (
        OCRWorkerProcess.__dict__.get("_vibeocr_runtime_residency_patch_v1") is True
    )

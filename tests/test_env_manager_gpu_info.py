# tests/test_env_manager_gpu_info.py
"""env_manager 的 GPU 信息探测与运行时能力缓存测试。"""

import logging
from unittest.mock import patch

import vibeocr.env_manager as em


def _mk_completed(stdout="", returncode=0):
    """构造类似 subprocess.CompletedProcess 的 mock。"""
    return type("R", (), {"returncode": returncode, "stdout": stdout, "stderr": ""})()


class TestDetectGpuInfo:
    def test_with_gpu(self):
        """nvidia-smi 返回 GPU 信息时正确解析名称/显存/CUDA。"""
        sample = (
            "NVIDIA GeForce RTX 4090, 24564 MiB, 560.94\n"
            "NVIDIA GeForce RTX 4090, 24564 MiB, 560.94\n"
        )
        with patch.object(em, "_run_pip") as mock_run, patch.object(
            em, "detect_cuda_version", return_value="cu126"
        ):
            mock_run.return_value = _mk_completed(stdout=sample)
            info = em.detect_gpu_info()

        assert info["has_gpu"] is True
        assert info["name"] == "NVIDIA GeForce RTX 4090"
        assert info["vram_mb"] == 24564
        assert info["cuda"] == "cu126"

    def test_no_gpu_falls_back(self):
        """nvidia-smi 不可用时回退到 detect_gpu 的简单结果。"""
        with patch.object(em, "_run_pip") as mock_run, patch.object(
            em, "detect_gpu", return_value=(False, None)
        ):
            mock_run.return_value = _mk_completed(returncode=1)
            info = em.detect_gpu_info()

        assert info["has_gpu"] is False
        assert info["name"] == ""
        assert info["vram_mb"] == 0
        assert info["cuda"] is None


class TestGetRuntimeGpuCapability:
    def test_caches_after_first_call(self, monkeypatch, tmp_path):
        """进程级缓存：第二次调用不再触发 resolve_use_gpu。"""
        monkeypatch.setattr(em, "_runtime_gpu_capability_cache", None)
        call_count = {"n": 0}

        def fake_resolve(project_root):
            call_count["n"] += 1
            return False

        monkeypatch.setattr(em, "resolve_use_gpu", fake_resolve)

        assert em.get_runtime_gpu_capability(tmp_path) is False
        assert em.get_runtime_gpu_capability(tmp_path) is False
        assert call_count["n"] == 1  # 第二次走缓存，不再调用

    def test_respects_pending_cpu(self, monkeypatch, tmp_path):
        """有 GPU 但 pending_backend=cpu 时返回 False（需求 3）。"""
        monkeypatch.setattr(em, "_runtime_gpu_capability_cache", None)
        monkeypatch.setattr(em, "resolve_use_gpu", lambda pr: False)
        assert em.get_runtime_gpu_capability(tmp_path) is False

    def test_reuses_background_detection_without_second_gpu_probe(
        self, monkeypatch, tmp_path
    ):
        """后台已有物理 GPU 结果时，不应再次调用 detect_gpu。"""
        monkeypatch.setattr(em, "_runtime_gpu_capability_cache", None)
        monkeypatch.setattr(em, "is_cache_valid", lambda _pr: (False, None))

        def fail_if_probed():
            raise AssertionError("不应重复调用 nvidia-smi GPU 探测")

        monkeypatch.setattr(em, "detect_gpu", fail_if_probed)

        assert em.get_runtime_gpu_capability(tmp_path, detected_has_gpu=True) is True

    def test_cached_pending_backend_overrides_background_detection(
        self, monkeypatch, tmp_path
    ):
        """用户选择的 CPU 后端优先于物理 GPU 探测结果。"""
        monkeypatch.setattr(em, "_runtime_gpu_capability_cache", None)
        monkeypatch.setattr(
            em,
            "is_cache_valid",
            lambda _pr: (True, {"pending_backend": "cpu"}),
        )

        assert em.get_runtime_gpu_capability(tmp_path, detected_has_gpu=True) is False


class TestCudaDetectionLogging:
    """detect_cuda_version / detect_gpu 的诊断日志测试。

    回归：此前用 print() 输出硬件检测信息，仅到 stdout（被 WorkerHost 子进程
    转发为 WorkerHost: 前缀 WARNING），不进 vibeocr.log，难以排查"无法检测
    CUDA 版本"的根因。改为 logger 后应能被 caplog 捕获、capsys 无 print。
    """

    def test_detect_cuda_version_failure_uses_logger_not_print(
        self, caplog, capsys, monkeypatch
    ):
        """nvidia-smi 与 nvcc 都不可用时，应走 logger.warning（非 print），
        且返回 None。"""
        # 让两个子进程调用都抛 FileNotFoundError（nvidia-smi/nvcc 不在 PATH）
        def fake_run(*a, **kw):
            raise FileNotFoundError("nvidia-smi not found")

        monkeypatch.setattr(em, "_run_pip", fake_run)

        with caplog.at_level(logging.WARNING, logger=em.logger.name):
            result = em.detect_cuda_version()

        assert result is None
        # 应有 logger 记录（而非 print）
        captured = capsys.readouterr()
        assert "[硬件检测]" not in captured.out, (
            "detect_cuda_version 不应再 print，应走 logger"
        )
        # logger 应记录关键诊断
        msgs = [r.message for r in caplog.records]
        assert any("nvidia-smi" in m and "PATH" in m for m in msgs), (
            f"应记录 nvidia-smi PATH 诊断，实际: {msgs}"
        )
        assert any("无法检测CUDA版本" in m for m in msgs), (
            f"应记录最终回退日志，实际: {msgs}"
        )

    def test_detect_cuda_version_accepts_new_cuda_umd_label(self, monkeypatch):
        """新版 NVIDIA 驱动将表头改为 CUDA UMD Version，仍应识别。"""
        sample = (
            "NVIDIA-SMI 610.74  KMD Version: 610.74  "
            "CUDA UMD Version: 13.3\n"
        )

        def fake_run(command, **_kwargs):
            if command == ["nvidia-smi"]:
                return _mk_completed(stdout=sample)
            raise FileNotFoundError(command[0])

        monkeypatch.setattr(em, "_run_pip", fake_run)

        assert em.detect_cuda_version() == "cu126"

    def test_detect_cuda_version_timeout_uses_logger(self, caplog, capsys, monkeypatch):
        """nvidia-smi 超时应记 warning（含'超时'），不再静默或 print。"""
        import subprocess as sp

        def fake_run(*a, **kw):
            raise sp.TimeoutExpired(cmd="nvidia-smi", timeout=10)

        monkeypatch.setattr(em, "_run_pip", fake_run)

        with caplog.at_level(logging.WARNING, logger=em.logger.name):
            em.detect_cuda_version()

        captured = capsys.readouterr()
        assert "[硬件检测]" not in captured.out
        msgs = [r.message for r in caplog.records]
        assert any("超时" in m for m in msgs), f"应记录超时诊断，实际: {msgs}"

    def test_detect_gpu_no_nvidia_smi_uses_logger(self, caplog, capsys, monkeypatch):
        """detect_gpu 在 nvidia-smi 缺失时应走 logger，不 print。"""
        # detect_gpu 先调 nvidia-smi -L（失败），内部再调 detect_cuda_version
        # （也失败），两者都应走 logger。
        call_count = {"n": 0}

        def fake_run(*a, **kw):
            call_count["n"] += 1
            raise FileNotFoundError("not found")

        monkeypatch.setattr(em, "_run_pip", fake_run)

        with caplog.at_level(logging.INFO, logger=em.logger.name):
            has_gpu, cuda = em.detect_gpu()

        assert has_gpu is False
        assert cuda is None
        captured = capsys.readouterr()
        assert "[硬件检测]" not in captured.out, "detect_gpu 不应 print"
        msgs = [r.message for r in caplog.records]
        assert any("未检测到NVIDIA GPU" in m for m in msgs)


class TestCudaVersionMatching:
    """detect_cuda_version 的版本匹配与 nvcc 回退分支。

    现有测试只覆盖了 nvidia-smi 成功 / 全失败 / 超时三条路径。未覆盖：
    nvcc 方法成功(2821-2842)、find_best_match 向下兼容(非精确匹配)、
    nvidia-smi 解析不到版本走 nvcc、CancelEvent 中断。本类补齐这些分支。
    """

    def test_nvcc_method_succeeds_when_nvidia_smi_missing(self, monkeypatch):
        """nvidia-smi 不可用但 nvcc 可用时，应通过 nvcc 解析 CUDA 版本。"""

        def fake_run(command, **_kwargs):
            if command == ["nvidia-smi"]:
                raise FileNotFoundError("nvidia-smi not found")
            if command == ["nvcc", "--version"]:
                return _mk_completed(stdout="Cuda compilation tools, release 12.6")
            raise FileNotFoundError(command[0])

        monkeypatch.setattr(em, "_run_pip", fake_run)
        assert em.detect_cuda_version() == "cu126"

    def test_nvcc_succeeds_when_nvidia_smi_has_no_version(self, monkeypatch):
        """nvidia-smi 成功返回但输出无 CUDA 版本字段时，回退到 nvcc。"""

        def fake_run(command, **_kwargs):
            if command == ["nvidia-smi"]:
                # returncode 0 但输出里没有 "CUDA Version" 字段
                return _mk_completed(stdout="NVIDIA-SMI 560.94\nDriver Version: 560.94\n")
            if command == ["nvcc", "--version"]:
                return _mk_completed(stdout="release 11.8")
            raise FileNotFoundError(command[0])

        monkeypatch.setattr(em, "_run_pip", fake_run)
        assert em.detect_cuda_version() == "cu118"

    def test_cuda_12_7_maps_to_cu126_via_downward_compat(self, monkeypatch):
        """CUDA 12.7 不在精确映射表，应向下兼容到 cu126（12.x 共享）。"""

        def fake_run(command, **_kwargs):
            if command == ["nvidia-smi"]:
                return _mk_completed(stdout="CUDA Version: 12.7\n")
            raise FileNotFoundError(command[0])

        monkeypatch.setattr(em, "_run_pip", fake_run)
        assert em.detect_cuda_version() == "cu126"

    def test_exact_cuda_11_8_matches_cu118(self, monkeypatch):
        """CUDA 11.8 精确匹配 cu118。"""

        def fake_run(command, **_kwargs):
            if command == ["nvidia-smi"]:
                return _mk_completed(stdout="CUDA Version: 11.8\n")
            raise FileNotFoundError(command[0])

        monkeypatch.setattr(em, "_run_pip", fake_run)
        assert em.detect_cuda_version() == "cu118"

    def test_cuda_below_supported_returns_none(self, monkeypatch):
        """CUDA 版本低于所有支持的映射（如 10.0）时，find_best_match 返回 None。"""

        def fake_run(command, **_kwargs):
            if command == ["nvidia-smi"]:
                return _mk_completed(stdout="CUDA Version: 10.0\n")
            raise FileNotFoundError(command[0])

        monkeypatch.setattr(em, "_run_pip", fake_run)
        assert em.detect_cuda_version() is None

    def test_cancel_event_returns_none(self, monkeypatch):
        """cancel_event 被置位时，_run_pip 抛 InstallCancelled，返回 None。"""
        import threading

        from vibeocr.env_manager import InstallCancelled

        cancel = threading.Event()
        cancel.set()

        def fake_run(*a, **kw):
            raise InstallCancelled()

        monkeypatch.setattr(em, "_run_pip", fake_run)
        assert em.detect_cuda_version(cancel_event=cancel) is None

    def test_nvcc_returncode_nonzero_falls_through_to_none(self, monkeypatch):
        """nvcc 返回非 0 时跳过解析，最终返回 None。"""

        def fake_run(command, **_kwargs):
            return _mk_completed(stdout="", returncode=1)

        monkeypatch.setattr(em, "_run_pip", fake_run)
        assert em.detect_cuda_version() is None

    def test_nvidia_smi_cuda_umd_label_parsed(self, monkeypatch):
        """新版驱动 CUDA UMD Version 标签（13.3）应解析并向下兼容到 cu126。"""

        def fake_run(command, **_kwargs):
            if command == ["nvidia-smi"]:
                return _mk_completed(stdout="CUDA UMD Version: 13.3\n")
            raise FileNotFoundError(command[0])

        monkeypatch.setattr(em, "_run_pip", fake_run)
        assert em.detect_cuda_version() == "cu126"


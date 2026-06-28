# tests/test_env_manager_gpu_info.py
"""env_manager 的 GPU 信息探测与运行时能力缓存测试。"""

from unittest.mock import patch

import vibeocr.env_manager as em


def _mk_completed(stdout="", returncode=0):
    """构造类似 subprocess.CompletedProcess 的 mock。"""
    m = type("R", (), {"returncode": returncode, "stdout": stdout, "stderr": ""})()
    return m


class TestDetectGpuInfo:
    def test_with_gpu(self):
        """nvidia-smi 返回 GPU 信息时正确解析名称/显存/CUDA。"""
        sample = (
            "NVIDIA GeForce RTX 4090, 24564 MiB, 560.94\n"
            "NVIDIA GeForce RTX 4090, 24564 MiB, 560.94\n"
        )
        with patch.object(em.subprocess, "run") as mock_run, patch.object(
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
        with patch.object(em.subprocess, "run") as mock_run, patch.object(
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

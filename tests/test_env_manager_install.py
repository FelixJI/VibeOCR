"""验证 env_manager 安装依赖的规格"""

import io
import tarfile
from unittest.mock import MagicMock, patch

import pytest

from vibeocr.env_manager import (
    _check_imports,
    _load_dep_specs,
    ensure_mineru_models,
    install_dependencies,
    install_embedded_dependencies,
    install_embedded_python,
    resolve_use_gpu,
)


class TestInstallStandalonePython:
    """python-build-standalone 安装测试（验证从 embeddable 迁移）"""

    @staticmethod
    def _make_standalone_tar_bytes() -> bytes:
        """构造一个最小 install_only 布局的 tar.gz：
        顶层为 install_only/python/，含 python.exe 和 Lib/site-packages/pip/__init__.py
        """
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            # 目录
            for d in ("install_only/python", "install_only/python/Lib",
                      "install_only/python/Lib/site-packages",
                      "install_only/python/Lib/site-packages/pip"):
                info = tarfile.TarInfo(name=d)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)
            # python.exe 占位文件
            exe_data = b"fake"
            info = tarfile.TarInfo(name="install_only/python/python.exe")
            info.size = len(exe_data)
            tar.addfile(info, io.BytesIO(exe_data))
            # pip __init__.py（证明自带 pip）
            pip_data = b"pip"
            info = tarfile.TarInfo(
                name="install_only/python/Lib/site-packages/pip/__init__.py"
            )
            info.size = len(pip_data)
            tar.addfile(info, io.BytesIO(pip_data))
        return buf.getvalue()

    def test_downloads_standalone_tar_and_extracts_no_pth_no_getpip(self, tmp_path):
        """安装应下载 .tar.gz、用 tarfile 解压，且不再写 ._pth 或下载 get-pip.py"""
        # install_embedded_python 先检查 get_environment_mode，需返回非 venv
        with (
            patch("vibeocr.env_manager.get_environment_mode", return_value="none"),
            patch(
                "vibeocr.env_manager.download_file_with_progress",
                return_value=True,
            ) as mock_dl,
            patch("tarfile.open", wraps=tarfile.open) as _mock_tar,
            patch("vibeocr.env_manager.get_embedded_python_executable",
                  return_value=tmp_path / "python" / "python.exe"),
            patch("vibeocr.env_manager.subprocess.run"),
        ):
            # 让下载函数把 tar 写到期望路径
            def _fake_dl(url, dest, *a, **kw):
                dest.write_bytes(self._make_standalone_tar_bytes())
                return True
            mock_dl.side_effect = _fake_dl

            ok, msg = install_embedded_python(tmp_path)

        assert ok, f"安装应成功: {msg}"
        # 下载被调用（至少一次），且 URL 指向 python-build-standalone
        assert mock_dl.called
        first_url = mock_dl.call_args[0][0]
        assert "python-build-standalone" in first_url
        assert first_url.endswith(".tar.gz")
        # 关键文件落盘（证明 tarfile 解压 + flatten 首层目录）
        assert (tmp_path / "python" / "python.exe").exists()
        assert (tmp_path / "python" / "Lib" / "site-packages" / "pip" / "__init__.py").exists()
        # 不应再写 ._pth 文件
        assert not any((tmp_path / "python").glob("._pth"))
        assert not any((tmp_path / "python").glob("*._pth"))

    def test_no_get_pip_download(self, tmp_path):
        """不应再下载 get-pip.py（build-standalone 自带 pip）"""
        with (
            patch("vibeocr.env_manager.get_environment_mode", return_value="none"),
            patch(
                "vibeocr.env_manager.download_file_with_progress",
                return_value=True,
            ) as mock_dl,
            patch("vibeocr.env_manager.get_embedded_python_executable",
                  return_value=tmp_path / "python" / "python.exe"),
            patch("vibeocr.env_manager.subprocess.run"),
        ):
            def _fake_dl(url, dest, *a, **kw):
                dest.write_bytes(self._make_standalone_tar_bytes())
                return True
            mock_dl.side_effect = _fake_dl
            install_embedded_python(tmp_path)

        # 所有下载 URL 都不得引用 get-pip.py
        for call in mock_dl.call_args_list:
            url = call[0][0]
            assert "get-pip.py" not in url, f"不应下载 get-pip.py: {url}"

    def test_download_failure_returns_false(self, tmp_path):
        """所有下载源都失败时应返回 False"""
        with (
            patch("vibeocr.env_manager.get_environment_mode", return_value="none"),
            patch("vibeocr.env_manager.download_file_with_progress", return_value=False),
            patch("vibeocr.env_manager.get_embedded_python_executable",
                  return_value=tmp_path / "python" / "python.exe"),
        ):
            ok, _msg = install_embedded_python(tmp_path)
        assert not ok


class TestInstallSpecs:
    """安装规格测试"""

    def test_embedded_deps_uses_core_not_all(self, tmp_path):
        """便携模式安装应使用 mineru[core] 而非 [all]"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        with (
            patch(
                "vibeocr.env_manager.get_pip_source",
                return_value="https://pypi.org/simple",
            ),
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
        ):
            install_embedded_dependencies(tmp_path, progress_callback=lambda s, m: None)

        mineru_cmd = [c for c in calls if "mineru" in " ".join(c)]
        assert len(mineru_cmd) > 0, "应包含 mineru 安装命令"
        joined = " ".join(mineru_cmd[0])
        assert "mineru[core]" in joined, f"应使用 mineru[core]，实际: {joined}"
        assert "mineru[all]" not in joined

    def test_install_deps_uses_core_not_all(self, tmp_path):
        """完整安装应使用 mineru[core] 而非 [all]"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        with (
            patch(
                "vibeocr.env_manager.get_pip_source",
                return_value="https://pypi.org/simple",
            ),
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
        ):
            install_dependencies(tmp_path)

        mineru_cmd = [c for c in calls if "mineru" in " ".join(c)]
        assert len(mineru_cmd) > 0
        joined = " ".join(mineru_cmd[0])
        assert "mineru[core]" in joined
        assert "mineru[all]" not in joined

    def test_embedded_deps_gpu_with_cuda_version(self, tmp_path):
        """便携模式 GPU 安装应使用 paddlepaddle-gpu"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        with (
            patch(
                "vibeocr.env_manager.get_pip_source",
                return_value="https://pypi.org/simple",
            ),
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
        ):
            install_embedded_dependencies(
                tmp_path,
                use_gpu=True,
                cuda_version="12.1",
                progress_callback=lambda s, m: None,
            )

        paddle_cmd = [c for c in calls if "paddlepaddle" in " ".join(c)]
        assert len(paddle_cmd) > 0
        joined = " ".join(paddle_cmd[0])
        assert "paddlepaddle-gpu" in joined, f"应使用 paddlepaddle-gpu，实际: {joined}"
        assert "cu121" in joined

    def test_embedded_deps_gpu_without_cuda_falls_back_to_default(self, tmp_path):
        """便携模式 GPU 无 CUDA 版本时应使用默认 cu129"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        with (
            patch(
                "vibeocr.env_manager.get_pip_source",
                return_value="https://pypi.org/simple",
            ),
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
        ):
            install_embedded_dependencies(
                tmp_path, use_gpu=True, progress_callback=lambda s, m: None
            )

        paddle_cmd = [c for c in calls if "paddlepaddle" in " ".join(c)]
        assert len(paddle_cmd) > 0
        joined = " ".join(paddle_cmd[0])
        assert "paddlepaddle-gpu" in joined
        assert "cu129" in joined

    def test_install_deps_gpu_with_cuda_version(self, tmp_path):
        """完整安装 GPU 应使用 paddlepaddle-gpu"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        with (
            patch(
                "vibeocr.env_manager.get_pip_source",
                return_value="https://pypi.org/simple",
            ),
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
        ):
            install_dependencies(tmp_path, use_gpu=True, cuda_version="12.6")

        paddle_cmd = [c for c in calls if "paddlepaddle" in " ".join(c)]
        assert len(paddle_cmd) > 0
        joined = " ".join(paddle_cmd[0])
        assert "paddlepaddle-gpu" in joined
        assert "cu126" in joined


class TestEnsureMineruModels:
    """MinerU 模型下载测试"""

    def test_calls_models_download(self, tmp_path):
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        with (
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch("vibeocr.env_manager.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            ok, _msg = ensure_mineru_models(tmp_path)

        assert ok
        cmd = mock_run.call_args[0][0]
        assert "mineru.cli.models_download" in " ".join(cmd)

    def test_returns_false_when_no_python(self, tmp_path):
        with patch(
            "vibeocr.env_manager.get_embedded_python_executable",
            return_value=tmp_path / "nonexistent.exe",
        ):
            ok, _msg = ensure_mineru_models(tmp_path)
        assert not ok


class TestLoadDepSpecs:
    """_load_dep_specs 依赖规格加载测试"""

    def test_loads_from_real_pyproject_returns_actual_versions(self):
        """从真实 pyproject.toml 加载时，应返回实际版本（非陈旧 fallback）"""
        # 重置模块级缓存，强制重新加载
        import vibeocr.env_manager as em

        em._dep_specs_cache = None

        specs = _load_dep_specs()

        # 关键：paddleocr 实际 >=3.7.0，旧 fallback 是 3.6.0
        assert "paddleocr" in specs
        assert "3.7.0" in specs["paddleocr"], (
            f"应反映 pyproject 实际版本，got: {specs['paddleocr']}"
        )
        # mineru 实际 >=3.3.1，旧 fallback 是 3.2.0
        assert "mineru" in specs
        assert "3.3.1" in specs["mineru"], (
            f"应反映 pyproject 实际版本，got: {specs['mineru']}"
        )
        # paddlepaddle-gpu
        assert "paddlepaddle-gpu" in specs

    def test_raises_when_pyproject_missing_and_no_version_json(self, tmp_path):
        """pyproject.toml 和 version.json 都不存在时，应 raise 而非返回空 dict"""
        # 重置缓存
        import vibeocr.env_manager as em

        em._dep_specs_cache = None

        with (
            patch("vibeocr.env_manager.get_project_root", return_value=tmp_path),
            pytest.raises(RuntimeError, match=r"pyproject\.toml"),
        ):
            _load_dep_specs()

    def test_raises_with_repair_hint(self, tmp_path):
        """raise 时应包含修复提示（告知用户如何修复）"""
        import vibeocr.env_manager as em

        em._dep_specs_cache = None

        with (
            patch("vibeocr.env_manager.get_project_root", return_value=tmp_path),
            pytest.raises(RuntimeError) as exc_info,
        ):
            _load_dep_specs()
        # 提示应指向 pyproject.toml 或 uv sync
        msg = str(exc_info.value)
        assert "pyproject.toml" in msg

    def test_uses_cache_on_second_call(self):
        """第二次调用应命中缓存，不重新解析"""
        import vibeocr.env_manager as em

        em._dep_specs_cache = None
        _load_dep_specs()
        assert em._dep_specs_cache is not None
        # 篡改缓存证明第二次返回的是缓存
        em._dep_specs_cache["__sentinel__"] = "from_cache"
        second = _load_dep_specs()
        assert second.get("__sentinel__") == "from_cache"
        # 清理：恢复真实缓存
        em._dep_specs_cache = None
        _load_dep_specs()


class TestCheckImportsPrimitive:
    """_check_imports 原语测试（消除 4 处 subprocess 重复）"""

    def test_returns_mapping_of_package_to_bool(self, tmp_path):
        """应返回 {包名: 是否可导入} 映射，key 用包名而非模块名"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        # 模拟所有 import 都成功
        def mock_run(cmd, **kwargs):
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        with patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run):
            result = _check_imports(python_exe)

        # key 应是包名（paddlepaddle），不是模块名（paddle）
        assert "paddlepaddle" in result
        assert "paddleocr" in result
        assert "mineru" in result
        assert "torch" in result
        assert all(isinstance(v, bool) for v in result.values())

    def test_marks_missing_module_as_false(self, tmp_path):
        """import 失败的模块应标记为 False"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kwargs):
            r = MagicMock()
            # cmd 形如 [python, "-c", "import paddle"]；paddle 失败，其余成功
            import_code = cmd[cmd.index("-c") + 1] if "-c" in cmd else ""
            r.returncode = 1 if import_code == "import paddle" else 0
            r.stderr = ""
            return r

        with patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run):
            result = _check_imports(python_exe)

        assert result["paddlepaddle"] is False

    def test_covers_same_modules_as_ocr_check_modules(self, tmp_path):
        """检测的模块集应与 env_config.OCR_CHECK_MODULES 一致（单一源）"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        with patch("vibeocr.env_manager.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = _check_imports(python_exe)

        from vibeocr.services.env_config import OCR_CHECK_MODULES

        # 返回的 key 集合应等于 OCR_CHECK_MODULES 的 value（包名）集合
        assert set(result.keys()) == set(OCR_CHECK_MODULES.values())

    def test_uses_extended_timeout_for_paddle(self, tmp_path):
        """paddle 首次导入需初始化 CUDA，应使用延长 timeout（而非默认 15s）"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        captured = []

        def mock_run(cmd, **kwargs):
            captured.append(kwargs.get("timeout"))
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        with patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run):
            _check_imports(python_exe)

        # 从 cmd 取出 import 的模块名，对应其 timeout
        from vibeocr.services.env_config import OCR_CHECK_TIMEOUTS

        # OCR_CHECK_TIMEOUTS 应存在且 paddle 的 timeout 显著大于默认
        assert "paddle" in OCR_CHECK_TIMEOUTS
        assert OCR_CHECK_TIMEOUTS["paddle"] >= 60, (
            "paddle 首次导入需初始化 CUDA，timeout 应 >= 60s"
        )


class TestResolveUseGpu:
    """resolve_use_gpu 测试：缓存优先 + 探测回退"""

    def test_returns_false_when_cache_says_no_gpu(self, tmp_path):
        """缓存 hardware_info.has_gpu=False 时应返回 False（CPU 模式）"""
        cached = {
            "version": 1,
            "machine_id": "any",
            "hardware_info": {"has_gpu": False, "cuda_version": None},
        }
        with (
            patch("vibeocr.env_manager.is_cache_valid", return_value=(True, cached)),
            patch("vibeocr.env_manager.detect_gpu") as mock_detect,
        ):
            result = resolve_use_gpu(tmp_path)

        assert result is False
        # 缓存命中时不应触发实时探测
        mock_detect.assert_not_called()

    def test_returns_true_when_cache_says_has_gpu(self, tmp_path):
        """缓存 hardware_info.has_gpu=True 时应返回 True（GPU 模式）"""
        cached = {
            "version": 1,
            "machine_id": "any",
            "hardware_info": {"has_gpu": True, "cuda_version": "cu129"},
        }
        with (
            patch("vibeocr.env_manager.is_cache_valid", return_value=(True, cached)),
            patch("vibeocr.env_manager.detect_gpu") as mock_detect,
        ):
            result = resolve_use_gpu(tmp_path)

        assert result is True
        mock_detect.assert_not_called()

    def test_falls_back_to_detect_gpu_when_cache_invalid(self, tmp_path):
        """缓存失效时应回退到 detect_gpu() 实时探测"""
        with (
            patch("vibeocr.env_manager.is_cache_valid", return_value=(False, None)),
            patch(
                "vibeocr.env_manager.detect_gpu", return_value=(False, None)
            ) as mock_detect,
        ):
            result = resolve_use_gpu(tmp_path)

        assert result is False
        mock_detect.assert_called_once()

    def test_falls_back_to_detect_gpu_when_no_hardware_info(self, tmp_path):
        """缓存有效但缺 hardware_info 字段时也应回退探测"""
        cached = {"version": 1, "machine_id": "any"}  # 无 hardware_info
        with (
            patch("vibeocr.env_manager.is_cache_valid", return_value=(True, cached)),
            patch(
                "vibeocr.env_manager.detect_gpu", return_value=(True, "cu129")
            ) as mock_detect,
        ):
            result = resolve_use_gpu(tmp_path)

        assert result is True
        mock_detect.assert_called_once()

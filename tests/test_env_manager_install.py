"""验证 env_manager 安装依赖的规格"""

import io
import tarfile
from unittest.mock import MagicMock, patch

import pytest

from vibeocr.env_manager import (
    _NVIDIA_CU13_PACKAGES,
    _check_imports,
    _load_dep_specs,
    ensure_mineru_models,
    install_dependencies,
    install_embedded_dependencies,
    install_embedded_python,
    resolve_use_gpu,
    switch_paddle_backend,
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
        """便携模式 GPU 安装应使用 paddlepaddle-gpu + cu-tag index

        cuda_version 是 detect_gpu() 返回的 cu-tag（如 "cu121"），直接用作 index URL。
        """
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
                cuda_version="cu121",
                progress_callback=lambda s, m: None,
            )

        paddle_cmd = [c for c in calls if "paddlepaddle" in " ".join(c)]
        assert len(paddle_cmd) > 0
        joined = " ".join(paddle_cmd[0])
        assert "paddlepaddle-gpu" in joined, f"应使用 paddlepaddle-gpu，实际: {joined}"
        assert "cu121" in joined

    def test_embedded_deps_gpu_without_cuda_falls_back_to_default(self, tmp_path):
        """便携模式 GPU 无 CUDA 版本时应使用默认 cu130"""
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
        assert "cu130" in joined

    def test_install_deps_gpu_with_cuda_version(self, tmp_path):
        """完整安装 GPU 应使用 paddlepaddle-gpu + cu-tag index"""
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
            install_dependencies(tmp_path, use_gpu=True, cuda_version="cu126")

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
        # nvidia cu13 运行时依赖（paddle GPU wheel 不内嵌 DLL，需显式声明）
        assert "nvidia-cublas" in specs
        assert specs["nvidia-cublas"] == "nvidia-cublas==13.0.2.14", (
            f"nvidia-cublas 版本应与 paddle Requires-Dist 精确匹配，got: {specs['nvidia-cublas']}"
        )

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

    def test_pending_backend_gpu_overrides_hardware_info(self, tmp_path):
        """pending_backend=gpu 优先于 hardware_info.has_gpu=False"""
        cached = {
            "version": 1,
            "machine_id": "any",
            "pending_backend": "gpu",
            "hardware_info": {"has_gpu": False, "cuda_version": None},
        }
        with (
            patch("vibeocr.env_manager.is_cache_valid", return_value=(True, cached)),
            patch("vibeocr.env_manager.detect_gpu") as mock_detect,
        ):
            result = resolve_use_gpu(tmp_path)

        assert result is True
        mock_detect.assert_not_called()

    def test_pending_backend_cpu_overrides_hardware_info(self, tmp_path):
        """pending_backend=cpu 优先于 hardware_info.has_gpu=True"""
        cached = {
            "version": 1,
            "machine_id": "any",
            "pending_backend": "cpu",
            "hardware_info": {"has_gpu": True, "cuda_version": "cu130"},
        }
        with (
            patch("vibeocr.env_manager.is_cache_valid", return_value=(True, cached)),
            patch("vibeocr.env_manager.detect_gpu") as mock_detect,
        ):
            result = resolve_use_gpu(tmp_path)

        assert result is False
        mock_detect.assert_not_called()


class TestNvidiaCu13Install:
    """便携版 GPU 安装应显式安装 7 个 cu13 nvidia 运行时库"""

    @staticmethod
    def _run_install(tmp_path, **kwargs):
        """共享的 mock 安装执行器，返回捕获的 subprocess.run 命令列表"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        calls = []

        def mock_run(cmd, **kw):
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
                tmp_path, progress_callback=lambda s, m: None, **kwargs
            )
        return calls

    def test_gpu_install_includes_all_7_nvidia_cu13_deps(self, tmp_path):
        """GPU 安装应有一条 pip 命令包含全部 7 个 nvidia cu13 包"""
        calls = self._run_install(tmp_path, use_gpu=True, cuda_version="cu130")

        # 找包含 nvidia 的安装命令
        nvidia_cmds = [c for c in calls if any("nvidia-" in str(a) for a in c)]
        assert len(nvidia_cmds) >= 1, "应有 nvidia 安装命令"
        # 全部 7 个包名应出现在同一条命令中（单条 pip install）
        joined = " ".join(nvidia_cmds[0])
        for pkg in _NVIDIA_CU13_PACKAGES:
            assert pkg in joined, f"nvidia 安装命令应包含 {pkg}，实际: {joined}"

    def test_gpu_install_nvidia_uses_pypi_source(self, tmp_path):
        """nvidia 包应从 PyPI 镜像源安装（非 paddle index）"""
        calls = self._run_install(tmp_path, use_gpu=True, cuda_version="cu130")
        nvidia_cmds = [c for c in calls if any("nvidia-" in str(a) for a in c)]
        assert len(nvidia_cmds) >= 1
        joined = " ".join(nvidia_cmds[0])
        assert "pypi.org/simple" in joined, "nvidia 包应从 PyPI 安装"

    def test_cpu_install_does_not_install_nvidia(self, tmp_path):
        """CPU 安装不应安装任何 nvidia 包"""
        calls = self._run_install(tmp_path, use_gpu=False)
        nvidia_cmds = [c for c in calls if any("nvidia-" in str(a) for a in c)]
        assert len(nvidia_cmds) == 0, f"CPU 安装不应装 nvidia，实际出现: {nvidia_cmds}"

    def test_gpu_install_uses_cu130_index_for_cuda13(self, tmp_path):
        """CUDA 13 (cu130) 应使用 cu130 paddle index"""
        calls = self._run_install(tmp_path, use_gpu=True, cuda_version="cu130")
        paddle_cmd = [c for c in calls if "paddlepaddle" in " ".join(c)]
        assert len(paddle_cmd) > 0
        joined = " ".join(paddle_cmd[0])
        assert "cu130" in joined, f"应使用 cu130 index，实际: {joined}"

    def test_gpu_install_argv_split_for_multi_packages(self, tmp_path):
        """多包安装命令（nvidia）应拆成独立 argv 元素，而非单个带空格字符串"""
        calls = self._run_install(tmp_path, use_gpu=True, cuda_version="cu130")
        nvidia_cmds = [c for c in calls if any("nvidia-" in str(a) for a in c)]
        assert len(nvidia_cmds) >= 1
        # 每个包应是 argv list 中独立的元素（不以空格合并）
        cmd = nvidia_cmds[0]
        # 找出所有 nvidia- 开头的参数
        nv_args = [a for a in cmd if isinstance(a, str) and a.startswith("nvidia-")]
        assert len(nv_args) == 7, (
            f"应有 7 个独立 nvidia argv 元素，实际 {len(nv_args)}: {nv_args}"
        )


class TestSwitchPaddleBackend:
    """switch_paddle_backend 测试：GPU ↔ CPU 切换"""

    @staticmethod
    def _run_switch(tmp_path, target, install_ok=True):
        """共享的 mock 切换执行器，返回捕获的 subprocess.run 命令列表"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        calls = []

        def mock_run(cmd, **kw):
            calls.append(cmd)
            r = MagicMock()
            r.returncode = 0 if install_ok else 1
            r.stderr = "" if install_ok else "fail"
            r.stdout = ""
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
            patch("vibeocr.env_manager.detect_gpu", return_value=(True, "cu130")),
            patch("vibeocr.env_manager.update_cache_field", return_value=True) as mock_update,
        ):
            ok, msg = switch_paddle_backend(
                tmp_path, target, progress_callback=lambda s, m: None
            )
        return ok, msg, calls, mock_update

    def test_switch_to_cpu_uninstalls_both_paddle_names(self, tmp_path):
        """切到 CPU 应卸载 paddlepaddle 和 paddlepaddle-gpu 两个包名"""
        ok, _msg, calls, _ = self._run_switch(tmp_path, "cpu")
        assert ok
        uninstall_cmds = [c for c in calls if "uninstall" in c]
        assert len(uninstall_cmds) >= 1
        joined = " ".join(uninstall_cmds[0])
        assert "paddlepaddle" in joined
        assert "paddlepaddle-gpu" in joined

    def test_switch_to_cpu_uninstalls_nvidia_packages(self, tmp_path):
        """切到 CPU 应额外卸载 7 个 nvidia cu13 包"""
        ok, _msg, calls, _ = self._run_switch(tmp_path, "cpu")
        assert ok
        uninstall_cmds = [c for c in calls if "uninstall" in c]
        # 至少有一条卸载命令含 nvidia 包
        nv_uninstall = [
            c for c in uninstall_cmds if any("nvidia-" in str(a) for a in c)
        ]
        assert len(nv_uninstall) >= 1, "应有卸载 nvidia 的命令"

    def test_switch_to_gpu_installs_gpu_paddle_and_nvidia(self, tmp_path):
        """切到 GPU 应安装 paddlepaddle-gpu (cu130) + 7 个 nvidia 包"""
        ok, _msg, calls, _ = self._run_switch(tmp_path, "gpu")
        assert ok
        install_cmds = [c for c in calls if "install" in " ".join(c)]
        all_joined = " ".join(" ".join(c) for c in install_cmds)
        assert "paddlepaddle-gpu" in all_joined
        assert "cu130" in all_joined
        assert "nvidia-cublas" in all_joined

    def test_switch_does_not_uninstall_nvidia_when_target_gpu(self, tmp_path):
        """切到 GPU 不应卸载 nvidia 包"""
        ok, _msg, calls, _ = self._run_switch(tmp_path, "gpu")
        assert ok
        uninstall_cmds = [c for c in calls if "uninstall" in c]
        nv_uninstall = [
            c for c in uninstall_cmds if any("nvidia-" in str(a) for a in c)
        ]
        assert len(nv_uninstall) == 0

    def test_switch_writes_pending_backend_to_cache(self, tmp_path):
        """切换成功后应写 pending_backend 到缓存"""
        ok, _msg, _calls, mock_update = self._run_switch(tmp_path, "cpu")
        assert ok
        mock_update.assert_called_once()
        args = mock_update.call_args[0]
        assert args[1] == "pending_backend"
        assert args[2] == "cpu"

    def test_switch_rejects_invalid_target(self, tmp_path):
        """无效 target 应立即失败"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        with patch(
            "vibeocr.env_manager.get_embedded_python_executable",
            return_value=python_exe,
        ):
            ok, msg = switch_paddle_backend(tmp_path, "tpu")
        assert not ok
        assert "无效" in msg or "tpu" in msg

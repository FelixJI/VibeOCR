"""验证 env_manager 安装依赖的规格"""

import io
import tarfile
from unittest.mock import MagicMock, patch

import pytest

from vibeocr.env_manager import (
    _check_imports,
    _install_paddle_stack,
    _load_dep_specs,
    ensure_mineru_models,
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
            for d in (
                "install_only/python",
                "install_only/python/Lib",
                "install_only/python/Lib/site-packages",
                "install_only/python/Lib/site-packages/pip",
            ):
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
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=tmp_path / "python" / "python.exe",
            ),
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
        assert (
            tmp_path / "python" / "Lib" / "site-packages" / "pip" / "__init__.py"
        ).exists()
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
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=tmp_path / "python" / "python.exe",
            ),
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
            patch(
                "vibeocr.env_manager.download_file_with_progress", return_value=False
            ),
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=tmp_path / "python" / "python.exe",
            ),
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

    def test_embedded_deps_gpu_with_cuda_version(self, tmp_path):
        """便携模式 GPU 安装应使用 paddlepaddle-gpu + cu-tag index

        cuda_version 是 detect_gpu() 返回的 cu-tag（如 "cu126"），直接用作 index URL。
        注意：detect_cuda_version 现仅产出 cu118/cu126（CUDA 12.x 全部归并到 cu126）。
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
                cuda_version="cu126",
                progress_callback=lambda s, m: None,
            )

        paddle_cmd = [c for c in calls if "paddlepaddle" in " ".join(c)]
        assert len(paddle_cmd) > 0
        joined = " ".join(paddle_cmd[0])
        assert "paddlepaddle-gpu" in joined, f"应使用 paddlepaddle-gpu，实际: {joined}"
        assert "cu126" in joined

    def test_embedded_deps_gpu_without_cuda_falls_back_to_default(self, tmp_path):
        """便携模式 GPU 无 CUDA 版本时应使用默认 cu126"""
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

        # paddleocr：版本号随 pyproject 变化，只验证来自 pyproject（非陈旧 fallback）
        assert "paddleocr" in specs
        assert specs["paddleocr"], f"paddleocr spec 不应为空，got: {specs['paddleocr']}"
        assert "paddleocr" in specs["paddleocr"].split("[")[0], (
            f"应来自 pyproject 的 paddleocr 声明，got: {specs['paddleocr']}"
        )
        # mineru：版本号随 pyproject 变化，只验证非空且来自 pyproject（非陈旧 fallback）
        assert "mineru" in specs
        assert specs["mineru"], f"mineru spec 不应为空，got: {specs['mineru']}"
        assert "mineru[core]>=" in specs["mineru"], (
            f"应来自 pyproject 的 mineru[core] 声明，got: {specs['mineru']}"
        )
        # paddlepaddle-gpu
        assert "paddlepaddle-gpu" in specs
        # torch（CUDA 运行时由 torch/lib 提供，不再声明 nvidia-*-cu13 包）
        assert "torch" in specs

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


class TestInstallPaddleStackAlias:
    """_install_paddle_stack 键名兼容测试

    回归：打包环境 version.json 由 bump_version 生成，其 dep_versions 用
    _KEY_ALIASES 把 paddlepaddle-gpu 归一为 paddlepaddle；_load_dep_specs 的
    version.json 分支据此构造的 specs 只有 "paddlepaddle" 键。_install_paddle_stack
    必须同时兼容 paddlepaddle-gpu（dev pyproject）与 paddlepaddle（打包）两种键，
    否则打包环境抛 KeyError: 'paddlepaddle-gpu'（依赖安装阶段，pip 还没开始下包）。
    """

    @staticmethod
    def _mock_pip_success():
        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        return calls, mock_run

    def test_specs_with_paddlepaddle_key_only_succeeds(self, tmp_path):
        """specs 仅含 paddlepaddle 键（打包环境 version.json 风格）时应正常安装，不抛 KeyError"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        # 模拟打包环境 _load_dep_specs 的 version.json 分支输出：
        # 键为 paddlepaddle（归一化后），无 paddlepaddle-gpu
        specs = {
            "paddlepaddle": "paddlepaddle>=3.3.1",
            "paddleocr": "paddleocr[doc-parser]>=3.7.0",
            "mineru": "mineru[core]>=3.2.0",
            "torch": "torch>=2.6.0",
        }
        calls, mock_run = self._mock_pip_success()

        with patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run):
            ok, msg = _install_paddle_stack(
                python_exe=python_exe,
                specs=specs,
                pip_source="https://pypi.tuna.tsinghua.edu.cn/simple",
                network_type="domestic",
                use_gpu=True,
                cuda_version="cu126",
                report_fn=lambda stage, m: None,
                success_msg="done",
            )

        assert ok, f"应成功，msg={msg}"
        # 应实际安装 paddlepaddle-gpu（值由 paddlepaddle 规格替换得到）
        joined = " ".join(" ".join(c) for c in calls)
        assert "paddlepaddle-gpu" in joined, f"应安装 paddlepaddle-gpu，实际: {joined}"

    def test_specs_with_paddlepaddle_gpu_key_still_works(self, tmp_path):
        """dev 环境（pyproject 分支，键为 paddlepaddle-gpu）行为不变"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        specs = {
            "paddlepaddle-gpu": "paddlepaddle-gpu>=3.3.1",
            "paddleocr": "paddleocr[doc-parser]>=3.7.0",
            "mineru": "mineru[core]>=3.2.0",
            "torch": "torch>=2.6.0",
        }
        calls, mock_run = self._mock_pip_success()

        with patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run):
            ok, _ = _install_paddle_stack(
                python_exe=python_exe,
                specs=specs,
                pip_source="https://pypi.org/simple",
                network_type="international",
                use_gpu=True,
                cuda_version="cu126",
                report_fn=lambda stage, m: None,
                success_msg="done",
            )

        assert ok
        joined = " ".join(" ".join(c) for c in calls)
        assert "paddlepaddle-gpu" in joined


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
            "hardware_info": {"has_gpu": True, "cuda_version": "cu126"},
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
                "vibeocr.env_manager.detect_gpu", return_value=(True, "cu126")
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
            "hardware_info": {"has_gpu": True, "cuda_version": "cu126"},
        }
        with (
            patch("vibeocr.env_manager.is_cache_valid", return_value=(True, cached)),
            patch("vibeocr.env_manager.detect_gpu") as mock_detect,
        ):
            result = resolve_use_gpu(tmp_path)

        assert result is False
        mock_detect.assert_not_called()


class TestGpuInstallUsesTorchForCudaRuntime:
    """便携版 GPU 安装策略：CUDA 运行时由 torch/lib 提供，不装 nvidia-*-cu13 包"""

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

    def test_gpu_install_does_not_install_nvidia_packages(self, tmp_path):
        """GPU 安装不应安装任何 nvidia-* 包（CUDA 运行时由 torch/lib 提供）"""
        calls = self._run_install(tmp_path, use_gpu=True, cuda_version="cu126")
        nvidia_cmds = [c for c in calls if any("nvidia-" in str(a) for a in c)]
        assert len(nvidia_cmds) == 0, (
            f"GPU 安装不应再装 nvidia 包，实际出现: {nvidia_cmds}"
        )

    def test_gpu_install_installs_torch(self, tmp_path):
        """GPU 安装应安装 torch（其 wheel 自带 CUDA 运行时 DLL）"""
        calls = self._run_install(tmp_path, use_gpu=True, cuda_version="cu126")
        torch_cmds = [c for c in calls if "torch" in " ".join(c)]
        assert len(torch_cmds) >= 1, "应有 torch 安装命令"
        joined = " ".join(torch_cmds[0])
        assert "torch" in joined

    def test_cpu_install_does_not_install_nvidia(self, tmp_path):
        """CPU 安装不应安装任何 nvidia 包"""
        calls = self._run_install(tmp_path, use_gpu=False)
        nvidia_cmds = [c for c in calls if any("nvidia-" in str(a) for a in c)]
        assert len(nvidia_cmds) == 0, f"CPU 安装不应装 nvidia，实际出现: {nvidia_cmds}"

    def test_gpu_install_uses_cu126_index(self, tmp_path):
        """GPU 安装应使用 cu126 paddle index（CUDA 12 构建，与 torch/lib 匹配）"""
        calls = self._run_install(tmp_path, use_gpu=True, cuda_version="cu126")
        paddle_cmd = [c for c in calls if "paddlepaddle" in " ".join(c)]
        assert len(paddle_cmd) > 0
        joined = " ".join(paddle_cmd[0])
        assert "cu126" in joined, f"应使用 cu126 index，实际: {joined}"

    def test_gpu_install_default_tag_is_cu126(self, tmp_path):
        """未指定 cuda_version 时，默认使用 cu126 paddle index"""
        calls = self._run_install(tmp_path, use_gpu=True)
        paddle_cmd = [c for c in calls if "paddlepaddle" in " ".join(c)]
        assert len(paddle_cmd) > 0
        joined = " ".join(paddle_cmd[0])
        assert "cu126" in joined, f"默认应使用 cu126，实际: {joined}"


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
            patch("vibeocr.env_manager.detect_gpu", return_value=(True, "cu126")),
            patch(
                "vibeocr.env_manager.update_cache_field", return_value=True
            ) as mock_update,
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

    def test_switch_to_cpu_does_not_uninstall_nvidia(self, tmp_path):
        """切到 CPU 不再单独卸载 nvidia 包（CUDA 运行时由 torch/lib 提供，保留 torch）"""
        ok, _msg, calls, _ = self._run_switch(tmp_path, "cpu")
        assert ok
        uninstall_cmds = [c for c in calls if "uninstall" in c]
        nv_uninstall = [
            c for c in uninstall_cmds if any("nvidia-" in str(a) for a in c)
        ]
        assert len(nv_uninstall) == 0, "不应再单独卸载 nvidia 包"

    def test_switch_to_gpu_installs_gpu_paddle_and_torch(self, tmp_path):
        """切到 GPU 应安装 paddlepaddle-gpu (cu126) + torch（提供 CUDA 运行时）"""
        ok, _msg, calls, _ = self._run_switch(tmp_path, "gpu")
        assert ok
        install_cmds = [c for c in calls if "install" in " ".join(c)]
        all_joined = " ".join(" ".join(c) for c in install_cmds)
        assert "paddlepaddle-gpu" in all_joined
        assert "cu126" in all_joined
        assert "torch" in all_joined

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


class TestInstallLogging:
    """安装过程日志应走 logging（写 vibeocr.log）而非 print"""

    def test_download_with_progress_uses_logging(self, tmp_path, caplog):
        """download_file_with_progress 应通过 logger.info 输出，不依赖 print"""
        import logging

        from vibeocr.env_manager import download_file_with_progress

        dest = tmp_path / "fake.tar.gz"
        with (
            patch("vibeocr.env_manager.urlopen") as mock_urlopen,
            patch("vibeocr.env_manager.Request"),
        ):
            # 构造一个最小 response：content-length=4，body=b"data"
            fake_resp = MagicMock()
            fake_resp.headers = {"content-length": "4"}
            fake_resp.__enter__ = MagicMock(return_value=fake_resp)
            fake_resp.__exit__ = MagicMock(return_value=False)
            # read 第一次返回 b"data"，第二次返回空（终止循环）
            fake_resp.read.side_effect = [b"data", b""]
            mock_urlopen.return_value = fake_resp

            with caplog.at_level(logging.INFO, logger="vibeocr.env_manager"):
                ok = download_file_with_progress(
                    "http://x/y.tar.gz", dest, "Python(镜像)"
                )

        assert ok
        # 应有 info 级日志记录下载开始（message 含"正在下载"）
        info_msgs = [r.message for r in caplog.records if r.levelno == logging.INFO]
        assert any("正在下载" in m for m in info_msgs), (
            f"应通过 logger.info 输出下载开始，实际 records: {info_msgs}"
        )

    def test_install_python_logs_stages(self, tmp_path, caplog):
        """install_embedded_python 各阶段（安装开始/下载源/解压/pip自检）应有日志"""
        ok, _msg = self._run_install_python(tmp_path, caplog)

        assert ok
        all_msgs = " ".join(r.message for r in caplog.records)
        assert "安装 Python 运行时" in all_msgs, "应记录安装开始"
        assert "尝试下载源" in all_msgs, "应记录下载源尝试"
        assert "解压完成" in all_msgs, "应记录解压完成"
        assert "pip 可用" in all_msgs, "应记录 pip 自检结果"

    @staticmethod
    def _run_install_python(tmp_path, caplog):
        """共享的 mock 安装执行器，写入最小 standalone tar.gz 并捕获日志"""
        import logging as _logging

        with (
            patch("vibeocr.env_manager.get_environment_mode", return_value="none"),
            patch(
                "vibeocr.env_manager.download_file_with_progress"
            ) as mock_dl,
            patch("tarfile.open", wraps=tarfile.open),
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=tmp_path / "python" / "python.exe",
            ),
            patch("vibeocr.env_manager.subprocess.run") as mock_run,
        ):
            # 让下载写一个最小 tar.gz
            def _make_tar():
                buf = io.BytesIO()
                with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                    for d in (
                        "install_only/python",
                        "install_only/python/Lib",
                        "install_only/python/Lib/site-packages",
                        "install_only/python/Lib/site-packages/pip",
                    ):
                        info = tarfile.TarInfo(name=d)
                        info.type = tarfile.DIRTYPE
                        info.mode = 0o755
                        tar.addfile(info)
                    exe_data = b"fake"
                    info = tarfile.TarInfo(name="install_only/python/python.exe")
                    info.size = len(exe_data)
                    tar.addfile(info, io.BytesIO(exe_data))
                    pip_data = b"pip"
                    info = tarfile.TarInfo(
                        name="install_only/python/Lib/site-packages/pip/__init__.py"
                    )
                    info.size = len(pip_data)
                    tar.addfile(info, io.BytesIO(pip_data))
                return buf.getvalue()

            def _fake_dl(url, dest, *a, **kw):
                dest.write_bytes(_make_tar())
                return True

            mock_dl.side_effect = _fake_dl
            mock_run.return_value = MagicMock(returncode=0, stdout="pip 25.0", stderr="")

            with caplog.at_level(_logging.INFO, logger="vibeocr.env_manager"):
                ok, msg = install_embedded_python(tmp_path)
        return ok, msg

    def test_install_deps_logs_report(self, tmp_path, caplog):
        """install_embedded_dependencies 的 report 应通过 logger.info 输出"""
        import logging

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kw):
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
            caplog.at_level(logging.INFO, logger="vibeocr.env_manager"),
        ):
            ok, _msg = install_embedded_dependencies(
                tmp_path, progress_callback=lambda s, m: None
            )

        assert ok
        info_msgs = " ".join(r.message for r in caplog.records)
        assert "开始安装OCR依赖" in info_msgs, "应记录安装开始"
        assert "pip源" in info_msgs, "应记录 pip 源"

    def test_switch_backend_logs_report(self, tmp_path, caplog):
        """switch_paddle_backend 的 report 应通过 logger.info 输出"""
        import logging

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
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
            patch("vibeocr.env_manager.detect_gpu", return_value=(True, "cu126")),
            patch("vibeocr.env_manager.update_cache_field", return_value=True),
            caplog.at_level(logging.INFO, logger="vibeocr.env_manager"),
        ):
            ok, _msg = switch_paddle_backend(
                tmp_path, "cpu", progress_callback=lambda s, m: None
            )

        assert ok
        info_msgs = " ".join(r.message for r in caplog.records)
        assert "开始切换到 CPU" in info_msgs, "应记录切换开始"


class TestReinstallPython:
    """reinstall_embedded_python：强制删除 python/ 后重装"""

    def test_deletes_python_dir_then_installs(self, tmp_path):
        """应先 rmtree(python/) 再调 install_embedded_python"""
        from vibeocr.env_manager import reinstall_embedded_python

        python_dir = tmp_path / "python"
        python_dir.mkdir()
        (python_dir / "python.exe").write_bytes(b"old")

        call_order = []

        def fake_rmtree(path, *a, **kw):
            call_order.append(("rmtree", str(path)))

        def fake_install(project_root, network_type="domestic", progress_callback=None):
            call_order.append(("install", str(project_root)))
            return True, "ok"

        with (
            patch("vibeocr.env_manager.shutil.rmtree", side_effect=fake_rmtree),
            patch(
                "vibeocr.env_manager.install_embedded_python", side_effect=fake_install
            ),
        ):
            ok, _msg = reinstall_embedded_python(tmp_path)

        assert ok
        # 先删后装
        assert call_order[0][0] == "rmtree", "应先删除 python/"
        assert "python" in call_order[0][1], "应删除 python/ 目录"
        assert call_order[1][0] == "install", "删除后应调用安装"

    def test_rmtree_ignores_errors_when_dir_missing(self, tmp_path):
        """python/ 不存在时 rmtree(ignore_errors=True) 不报错，继续安装"""
        from vibeocr.env_manager import reinstall_embedded_python

        with (
            patch("vibeocr.env_manager.shutil.rmtree") as mock_rmtree,
            patch(
                "vibeocr.env_manager.install_embedded_python",
                return_value=(True, "ok"),
            ),
        ):
            ok, _msg = reinstall_embedded_python(tmp_path)

        assert ok
        mock_rmtree.assert_called_once()
        # 应以 ignore_errors=True 调用
        assert mock_rmtree.call_args.kwargs.get("ignore_errors") is True

    def test_progress_callback_receives_cleanup_stage(self, tmp_path):
        """progress_callback 应收到'清理'阶段"""
        from vibeocr.env_manager import reinstall_embedded_python

        stages = []
        with (
            patch("vibeocr.env_manager.shutil.rmtree"),
            patch(
                "vibeocr.env_manager.install_embedded_python",
                return_value=(True, "ok"),
            ),
        ):
            ok, _msg = reinstall_embedded_python(
                tmp_path, progress_callback=lambda s, m: stages.append((s, m))
            )

        assert ok
        cleanup_stages = [s for s in stages if "清理" in s[1] or "清理" in s[0]]
        assert len(cleanup_stages) > 0, f"应收到清理阶段回调，实际: {stages}"

    def test_returns_false_when_install_fails(self, tmp_path):
        """install_embedded_python 失败时应返回 False"""
        from vibeocr.env_manager import reinstall_embedded_python

        with (
            patch("vibeocr.env_manager.shutil.rmtree"),
            patch(
                "vibeocr.env_manager.install_embedded_python",
                return_value=(False, "下载失败"),
            ),
        ):
            ok, msg = reinstall_embedded_python(tmp_path)

        assert not ok
        assert "下载失败" in msg


class TestBuildPaddleRequirements:
    """_build_paddle_requirements：构建 paddle 项（GPU/CPU/index 选择）"""

    @staticmethod
    def _specs():
        return {
            "paddlepaddle-gpu": "paddlepaddle-gpu>=3.3.1",
            "paddlepaddle": "paddlepaddle>=3.3.1",
            "paddleocr": "paddleocr[doc-parser]>=3.7.0",
            "mineru": "mineru[core]>=3.4.0",
            "torch": "torch>=2.6.0",
        }

    def test_gpu_with_cuda_selects_gpu_index(self):
        """GPU + cuda_version → paddlepaddle-gpu + 含 cu-tag 的 index"""
        from vibeocr.env_manager import _build_paddle_requirements

        reqs = _build_paddle_requirements(
            specs=self._specs(),
            use_gpu=True,
            cuda_version="cu126",
            network_type="domestic",
            report_fn=lambda s, m: None,
        )
        assert len(reqs) == 1
        name, pkg_spec, index = reqs[0]
        assert "GPU" in name
        assert "paddlepaddle-gpu" in pkg_spec
        assert "cu126" in index

    def test_cpu_selects_cpu_index(self):
        """CPU → paddlepaddle(CPU) + cpu index"""
        from vibeocr.env_manager import _build_paddle_requirements

        reqs = _build_paddle_requirements(
            specs=self._specs(),
            use_gpu=False,
            cuda_version=None,
            network_type="domestic",
            report_fn=lambda s, m: None,
        )
        assert len(reqs) == 1
        name, pkg_spec, index = reqs[0]
        assert "CPU" in name
        assert pkg_spec.startswith("paddlepaddle")
        assert "paddlepaddle-gpu" not in pkg_spec
        assert "/cpu/" in index

    def test_gpu_default_tag_when_no_cuda(self):
        """GPU 无 cuda_version → 用默认 cu126"""
        from vibeocr.env_manager import _build_paddle_requirements

        reqs = _build_paddle_requirements(
            specs=self._specs(),
            use_gpu=True,
            cuda_version=None,
            network_type="domestic",
            report_fn=lambda s, m: None,
        )
        name, _pkg, index = reqs[0]
        assert "cu126" in name
        assert "cu126" in index

    def test_specs_with_paddlepaddle_key_only(self):
        """specs 仅含 paddlepaddle 键（打包环境 version.json 风格）应正常工作"""
        from vibeocr.env_manager import _build_paddle_requirements

        specs = {"paddlepaddle": "paddlepaddle>=3.3.1"}
        reqs = _build_paddle_requirements(
            specs=specs,
            use_gpu=True,
            cuda_version="cu126",
            network_type="domestic",
            report_fn=lambda s, m: None,
        )
        assert len(reqs) == 1
        name, pkg_spec, _index = reqs[0]
        assert "GPU" in name
        assert "paddlepaddle-gpu" in pkg_spec


class TestInstallPaddleStackRequirementsOverride:
    """_install_paddle_stack 的 requirements_override 参数"""

    @staticmethod
    def _specs():
        return {
            "paddlepaddle-gpu": "paddlepaddle-gpu>=3.3.1",
            "paddlepaddle": "paddlepaddle>=3.3.1",
            "paddleocr": "paddleocr[doc-parser]>=3.7.0",
            "mineru": "mineru[core]>=3.4.0",
            "torch": "torch>=2.6.0",
        }

    @staticmethod
    def _filter_install_cmds(calls):
        """从 subprocess.run 调用中过滤出真正的 pip install 命令（排除 pip 升级）。"""
        return [
            c
            for c in calls
            if "install" in c
            and "--upgrade" not in c
            and "pip" not in " ".join(c[3:])
        ]

    def test_override_installs_only_provided_subset(self, tmp_path):
        """传 requirements_override 时只装子集，不构建完整列表"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        calls = []

        def mock_run(cmd, **kw):
            calls.append(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        # 只装 mineru 一个
        subset = [("MinerU", "mineru[core]>=3.4.0", "https://pypi.org/simple")]
        with patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run):
            ok, _msg = _install_paddle_stack(
                python_exe=python_exe,
                specs=self._specs(),
                pip_source="https://pypi.org/simple",
                network_type="domestic",
                use_gpu=True,
                cuda_version="cu126",
                report_fn=lambda s, m: None,
                success_msg="done",
                requirements_override=subset,
            )

        assert ok
        install_cmds = self._filter_install_cmds(calls)
        assert len(install_cmds) == 1, f"应只装 1 个包，实际命令: {install_cmds}"
        assert "mineru" in " ".join(install_cmds[0])

    def test_no_override_builds_full_requirements(self, tmp_path):
        """不传 requirements_override 时构建完整列表（向后兼容）"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        calls = []

        def mock_run(cmd, **kw):
            calls.append(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        with patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run):
            ok, _msg = _install_paddle_stack(
                python_exe=python_exe,
                specs=self._specs(),
                pip_source="https://pypi.org/simple",
                network_type="domestic",
                use_gpu=True,
                cuda_version="cu126",
                report_fn=lambda s, m: None,
                success_msg="done",
            )

        assert ok
        install_cmds = self._filter_install_cmds(calls)
        # GPU 完整列表：paddle + paddleocr + mineru + torch = 4 个安装命令
        assert len(install_cmds) == 4, (
            f"GPU 完整列表应装 4 个，实际: {install_cmds}"
        )


class TestInstallMissingDependencies:
    """install_missing_dependencies：增量安装（跳过已 import 成功的包）"""

    @staticmethod
    def _specs():
        return {
            "paddlepaddle-gpu": "paddlepaddle-gpu>=3.3.1",
            "paddlepaddle": "paddlepaddle>=3.3.1",
            "paddleocr": "paddleocr[doc-parser]>=3.7.0",
            "mineru": "mineru[core]>=3.4.0",
            "torch": "torch>=2.6.0",
        }

    @staticmethod
    def _filter_install_cmds(calls):
        """从 subprocess.run 调用中过滤出 pip install 命令（排除 import 检测、pip 升级）。"""
        return [
            c
            for c in calls
            if "install" in c
            and "--upgrade" not in c
            and "-c" not in c
            and "import" not in " ".join(c)
        ]

    def test_skips_installed_packages_only_installs_missing(self, tmp_path):
        """已 import 成功的包应跳过，只装缺失的"""
        from vibeocr.env_manager import install_missing_dependencies

        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        all_calls = []

        def mock_run(cmd, **kw):
            all_calls.append(cmd)
            r = MagicMock()
            r.stderr = ""
            # _check_imports 走 subprocess.run：paddle/paddleocr 成功，mineru/torch 失败
            import_code = cmd[cmd.index("-c") + 1] if "-c" in cmd else ""
            if import_code.startswith("import "):
                module = import_code.split()[1]
                r.returncode = 0 if module in ("paddle", "paddleocr") else 1
            else:
                # pip install 成功
                r.returncode = 0
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
            patch("vibeocr.env_manager._load_dep_specs", return_value=self._specs()),
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
        ):
            ok, msg = install_missing_dependencies(
                tmp_path,
                use_gpu=True,
                cuda_version="cu126",
                progress_callback=lambda s, m: None,
            )

        assert ok, f"应成功: {msg}"
        pip_installs = self._filter_install_cmds(all_calls)
        # paddle + paddleocr 已装 → 跳过；只应装 mineru + torch
        joined = " ".join(" ".join(c) for c in pip_installs)
        assert "paddlepaddle" not in joined, "paddle 已装应跳过"
        assert "paddleocr" not in joined, "paddleocr 已装应跳过"
        assert "mineru" in joined, "mineru 缺失应安装"
        assert "torch" in joined, "torch 缺失应安装"

    def test_all_installed_skips_everything(self, tmp_path):
        """全部已装时应跳过所有安装，返回成功"""
        from vibeocr.env_manager import install_missing_dependencies

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 0  # 所有 import 都成功
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
            patch("vibeocr.env_manager._load_dep_specs", return_value=self._specs()),
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
        ):
            ok, msg = install_missing_dependencies(
                tmp_path, progress_callback=lambda s, m: None
            )

        assert ok
        assert "已安装" in msg or "无需" in msg

    def test_all_missing_installs_everything(self, tmp_path):
        """全部缺失时应装全部（等同全量）"""
        from vibeocr.env_manager import install_missing_dependencies

        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        all_calls = []

        def mock_run(cmd, **kw):
            all_calls.append(cmd)
            r = MagicMock()
            r.stderr = ""
            import_code = cmd[cmd.index("-c") + 1] if "-c" in cmd else ""
            if import_code.startswith("import "):
                r.returncode = 1  # 全部 import 失败
            else:
                r.returncode = 0
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
            patch("vibeocr.env_manager._load_dep_specs", return_value=self._specs()),
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
        ):
            ok, _msg = install_missing_dependencies(
                tmp_path,
                use_gpu=False,
                progress_callback=lambda s, m: None,
            )

        assert ok
        pip_installs = self._filter_install_cmds(all_calls)
        # CPU 模式完整列表：paddle + paddleocr + mineru = 3 个
        assert len(pip_installs) == 3, (
            f"CPU 全量应装 3 个，实际: {pip_installs}"
        )

    def test_force_backend_gpu_uses_gpu_requirements(self, tmp_path):
        """force_backend=gpu 时应构建 GPU requirements（含 torch）"""
        from vibeocr.env_manager import install_missing_dependencies

        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        all_calls = []

        def mock_run(cmd, **kw):
            all_calls.append(cmd)
            r = MagicMock()
            r.stderr = ""
            import_code = cmd[cmd.index("-c") + 1] if "-c" in cmd else ""
            if import_code.startswith("import "):
                r.returncode = 1  # 全部缺失
            else:
                r.returncode = 0
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
            patch("vibeocr.env_manager._load_dep_specs", return_value=self._specs()),
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
            patch("vibeocr.env_manager.detect_gpu", return_value=(True, "cu126")),
        ):
            ok, _msg = install_missing_dependencies(
                tmp_path,
                force_backend="gpu",
                progress_callback=lambda s, m: None,
            )

        assert ok
        pip_installs = self._filter_install_cmds(all_calls)
        # GPU 完整列表：paddle + paddleocr + mineru + torch = 4 个
        assert len(pip_installs) == 4, (
            f"GPU force_backend 应装 4 个，实际: {pip_installs}"
        )


class TestInstallFailureLogging:
    """安装失败时应 logger.error 完整 stderr（UI 只显示截断版）"""

    def test_failure_logs_full_stderr(self, tmp_path, caplog):
        """pip 返回非 0 时应 logger.error 完整 stderr（不止返回的 500 字截断）"""
        import logging

        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        long_stderr = "ERROR: " + "x" * 800  # 超过 500 字截断阈值

        def mock_run(cmd, **kw):
            r = MagicMock()
            # pip 升级成功；安装命令失败
            if "--upgrade" in cmd:
                r.returncode = 0
                r.stderr = ""
            else:
                r.returncode = 1
                r.stderr = long_stderr
            r.stdout = ""
            return r

        specs = {
            "paddlepaddle": "paddlepaddle>=3.3.1",
            "paddleocr": "paddleocr[doc-parser]>=3.7.0",
            "mineru": "mineru[core]>=3.4.0",
            "torch": "torch>=2.6.0",
        }
        with (
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
            caplog.at_level(logging.ERROR, logger="vibeocr.env_manager"),
        ):
            ok, _msg = _install_paddle_stack(
                python_exe=python_exe,
                specs=specs,
                pip_source="https://pypi.org/simple",
                network_type="domestic",
                use_gpu=False,
                cuda_version=None,
                report_fn=lambda s, m: None,
                success_msg="done",
            )

        assert not ok
        # 完整 stderr（800 个 x）应在 ERROR 日志中
        error_msgs = " ".join(
            r.message for r in caplog.records if r.levelno >= logging.ERROR
        )
        assert "x" * 800 in error_msgs, (
            f"应记录完整 stderr（800 个 x），实际 ERROR 日志长度: {len(error_msgs)}"
        )

"""验证 env_manager 安装依赖的规格"""

import io
import subprocess
import tarfile
from unittest.mock import MagicMock, patch

import pytest

from vibeocr.env_manager import (
    _check_imports,
    _install_paddle_stack,
    _load_dep_specs,
    ensure_mineru_models,
    install_dependencies_batch,
    install_embedded_dependencies,
    install_embedded_python,
    install_missing_dependencies,
    install_single_dependency,
    resolve_use_gpu,
    switch_paddle_backend,
)


def _popen_side_effect(mock_run):
    """把旧的 mock_run(cmd, **kw) -> result 桥接为 Popen mock 工厂。

    env_manager._run_pip 现在用 subprocess.Popen + communicate/poll 而非
    subprocess.run。本 helper 让旧的 mock_run（返回带 .returncode/.stdout/.stderr
    的对象）继续可用，无需逐个重写测试 mock。

    用法：patch("vibeocr.env_manager.subprocess.Popen",
              side_effect=_popen_side_effect(mock_run))
    """

    def factory(cmd, **kw):
        result = mock_run(cmd, **kw)
        proc = MagicMock()
        proc.returncode = result.returncode
        stdout = getattr(result, "stdout", "") or ""
        stderr = getattr(result, "stderr", "") or ""
        # communicate() 立即返回（_run_pip 在子线程调用它收尾）
        proc.communicate.return_value = (stdout, stderr)
        # poll() 先返回 None（_run_pip 首轮判定"运行中"），再返回 returncode（已退出）
        proc.poll.side_effect = [None, result.returncode]
        return proc

    return factory


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

    def test_short_circuit_validates_python_exe(self, tmp_path):
        """python/ 存在但缺 python.exe（半成品）时不应短路返回 True，应清理后重装

        回归：解压中断会留下不完整的 python/ 目录，旧逻辑仅凭
        python_dir.exists() 即短路返回"已安装"，导致后续依赖安装找不到解释器。
        """
        # 构造半成品：python/ 存在但无 python.exe
        python_dir = tmp_path / "python"
        python_dir.mkdir()
        (python_dir / "Lib").mkdir()  # 有内容但缺可执行文件

        rmtree_called = []

        def fake_rmtree(path, *a, **kw):
            rmtree_called.append(str(path))

        with (
            patch("vibeocr.env_manager.get_environment_mode", return_value="none"),
            # python.exe 检测：不存在（半成品）
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=tmp_path / "python" / "python.exe",
            ),
            patch(
                "vibeocr.env_manager.download_file_with_progress", return_value=True
            ) as mock_dl,
            patch("vibeocr.env_manager.shutil.rmtree", side_effect=fake_rmtree),
            patch("vibeocr.env_manager.subprocess.run"),
        ):
            def _fake_dl(url, dest, *a, **kw):
                dest.write_bytes(self._make_standalone_tar_bytes())
                return True

            mock_dl.side_effect = _fake_dl
            ok, _msg = install_embedded_python(tmp_path)

        assert ok, "半成品清理后应重新安装成功"
        # 应先清理半成品 python/ 目录
        assert any("python" in p for p in rmtree_called), (
            f"应清理半成品 python/，实际 rmtree 调用: {rmtree_called}"
        )


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
            patch(
                "vibeocr.env_manager.subprocess.Popen",
                side_effect=_popen_side_effect(mock_run),
            ),
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
            patch(
                "vibeocr.env_manager.subprocess.Popen",
                side_effect=_popen_side_effect(mock_run),
            ),
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
            patch(
                "vibeocr.env_manager.subprocess.Popen",
                side_effect=_popen_side_effect(mock_run),
            ),
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

    @pytest.fixture(autouse=True)
    def _stub_job_object_guard(self):
        """所有测试都 mock subprocess.Popen 返回假进程，真实 JobObjectGuard 会
        拿假 pid 调用原生 OpenProcess/AssignProcessToJobObject 导致崩溃，
        故统一替换为 MagicMock。"""
        with patch("vibeocr.env_manager.JobObjectGuard") as guard:
            yield guard

    def test_calls_models_download(self, tmp_path):
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_popen_factory():
            proc = MagicMock()
            proc.stdout = iter([b"downloading model...\n", b""])
            proc.returncode = 0
            proc.poll.return_value = 0
            proc.wait.return_value = None
            return proc

        with (
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.env_manager.subprocess.Popen", return_value=mock_popen_factory()
            ) as mock_popen,
            patch("vibeocr.env_manager.detect_network_source", return_value="domestic"),
        ):
            ok, _msg = ensure_mineru_models(tmp_path)

        assert ok
        cmd = mock_popen.call_args[0][0]
        assert "mineru.cli.models_download" in " ".join(cmd)

    def test_progress_callback_receives_output(self, tmp_path):
        """progress_callback 应收到下载进度（逐行输出）"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_popen_factory():
            proc = MagicMock()
            proc.stdout = iter(
                [b"downloading file1.zip\n", b"downloading file2.zip\n", b""]
            )
            proc.returncode = 0
            proc.poll.return_value = 0
            proc.wait.return_value = None
            return proc

        messages: list[str] = []
        with (
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.env_manager.subprocess.Popen", return_value=mock_popen_factory()
            ),
            patch("vibeocr.env_manager.detect_network_source", return_value="domestic"),
        ):
            ok, _msg = ensure_mineru_models(
                tmp_path, progress_callback=lambda s, m: messages.append(m)
            )

        assert ok
        # 应收到子进程的逐行输出
        assert any("file1.zip" in m for m in messages), f"实际: {messages}"
        assert any("file2.zip" in m for m in messages), f"实际: {messages}"

    def test_returns_false_when_popen_fails(self, tmp_path):
        """子进程返回非 0 退出码时应返回 False"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_popen_factory():
            proc = MagicMock()
            proc.stdout = iter([b""])
            proc.returncode = 1
            proc.poll.return_value = 1
            proc.wait.return_value = None
            return proc

        with (
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.env_manager.subprocess.Popen", return_value=mock_popen_factory()
            ),
            patch("vibeocr.env_manager.detect_network_source", return_value="domestic"),
        ):
            ok, msg = ensure_mineru_models(tmp_path)

        assert not ok
        assert "失败" in msg or "退出码" in msg

    def test_returns_false_when_no_python(self, tmp_path):
        with patch(
            "vibeocr.env_manager.get_embedded_python_executable",
            return_value=tmp_path / "nonexistent.exe",
        ):
            ok, _msg = ensure_mineru_models(tmp_path)
        assert not ok

    def test_download_subprocess_bound_to_job_object(self, tmp_path, _stub_job_object_guard):
        """下载子进程必须绑定 JobObjectGuard，防止主进程崩溃后留下孤儿下载进程。

        回归测试：ensure_mineru_models 跑几十分钟的模型下载，若主进程中途崩溃，
        未绑定的下载子进程会成为孤儿。绑定 JobObjectGuard（KILL_ON_JOB_CLOSE）
        后由内核在主进程退出时连带终止。
        """
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_popen_factory():
            proc = MagicMock()
            proc.stdout = iter([b"downloading model...\n", b""])
            proc.returncode = 0
            proc.poll.return_value = 0
            proc.wait.return_value = None
            return proc

        with (
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.env_manager.subprocess.Popen",
                return_value=mock_popen_factory(),
            ),
            patch("vibeocr.env_manager.detect_network_source", return_value="domestic"),
        ):
            ok, _msg = ensure_mineru_models(tmp_path)

        assert ok
        # JobObjectGuard 实例应被创建，且下载子进程被绑定
        _stub_job_object_guard.assert_called_once()
        guard_instance = _stub_job_object_guard.return_value
        guard_instance.assign_from_popen.assert_called_once()
        # close() 在 finally 中调用（下载完成后关闭 Job 句柄，无活进程时 no-op）
        guard_instance.close.assert_called_once()

    def test_job_object_closed_on_timeout(self, tmp_path, _stub_job_object_guard):
        """下载超时时，子进程被 kill，Job 句柄也在 finally 中关闭。"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        proc = MagicMock()
        proc.stdout = iter([b""])
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd="x", timeout=1)

        with (
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch("vibeocr.env_manager.subprocess.Popen", return_value=proc),
            patch("vibeocr.env_manager.detect_network_source", return_value="domestic"),
        ):
            ok, msg = ensure_mineru_models(tmp_path, timeout=1)

        assert not ok
        assert "超时" in msg
        proc.kill.assert_called_once()
        guard_instance = _stub_job_object_guard.return_value
        guard_instance.close.assert_called_once()


class TestInstallMissingLeafTrigger:
    """install_missing_dependencies 的 leaf 缺失→重装承载顶层包逻辑测试。

    核心场景：paddleocr 顶层 import 成功（usable=True）但 paddlex[ocr] leaf 缺失时，
    补装应把 paddleocr 加入 subset 重装，让 pip 重新解析传递树补齐 leaf。
    """

    def test_paddleocr_reinstalled_when_leaf_missing(self, tmp_path):
        """顶层 paddleocr 可用但 leaf 缺失时，paddleocr 应被加入重装 subset"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        # 构造 import_detailed：顶层全 usable，但 leaf（scipy）usable=False
        from vibeocr.services.env_config import (
            OCR_CHECK_LEAF_MODULES,
            OCR_CHECK_MODULES,
        )

        detailed = {}
        for _mod, pkg in OCR_CHECK_MODULES.items():
            detailed[pkg] = (True, True)  # 顶层全装且可用
        for _mod, pkg in OCR_CHECK_LEAF_MODULES.items():
            detailed[pkg] = (True, True)
        # 制造 leaf 缺失：scipy usable=False
        detailed["scipy"] = (False, False)

        captured_stack = {}

        def fake_stack(**kwargs):
            captured_stack.update(kwargs)
            return True, "ok"

        with (
            patch(
                "vibeocr.env_manager.get_pip_source",
                return_value="https://pypi.org/simple",
            ),
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.env_manager._check_imports_detailed",
                return_value=detailed,
            ),
            patch("vibeocr.env_manager._install_paddle_stack", side_effect=fake_stack),
            patch("vibeocr.env_manager.detect_gpu", return_value=(False, None)),
        ):
            ok, _msg = install_missing_dependencies(
                tmp_path, progress_callback=lambda s, m: None
            )

        assert ok is True
        subset = captured_stack.get("requirements_override", [])
        subset_names = [name for name, _spec, _idx in subset]
        # paddleocr 应在 subset 中（因 leaf 缺失触发）
        assert any("PaddleOCR" in n for n in subset_names), (
            f"leaf 缺失时 paddleocr 应被加入重装 subset，实际 subset: {subset_names}"
        )

    def test_paddleocr_skipped_when_all_leafs_present(self, tmp_path):
        """顶层和 leaf 全可用时，paddleocr 应被跳过（不在 subset）"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        from vibeocr.services.env_config import (
            OCR_CHECK_LEAF_MODULES,
            OCR_CHECK_MODULES,
        )

        detailed = {}
        for _mod, pkg in OCR_CHECK_MODULES.items():
            detailed[pkg] = (True, True)
        for _mod, pkg in OCR_CHECK_LEAF_MODULES.items():
            detailed[pkg] = (True, True)  # 全部 leaf 可用

        captured_stack = {}

        def fake_stack(**kwargs):
            captured_stack.update(kwargs)
            return True, "ok"

        with (
            patch(
                "vibeocr.env_manager.get_pip_source",
                return_value="https://pypi.org/simple",
            ),
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.env_manager._check_imports_detailed",
                return_value=detailed,
            ),
            patch("vibeocr.env_manager._install_paddle_stack", side_effect=fake_stack),
            patch("vibeocr.env_manager.detect_gpu", return_value=(False, None)),
        ):
            install_missing_dependencies(
                tmp_path, progress_callback=lambda s, m: None
            )

        subset = captured_stack.get("requirements_override")
        # 全部已装时 subset 为空（函数提前返回 "所有OCR依赖已安装"）
        # 或 subset 不含 paddleocr
        if subset is not None:
            subset_names = [name for name, _spec, _idx in subset]
            assert not any("PaddleOCR" in n for n in subset_names), (
                f"全部可用时 paddleocr 不应在 subset: {subset_names}"
            )


class TestInstallSingleDependency:
    """install_single_dependency 单包重装测试"""

    def test_leaf_pkg_uses_plain_name(self, tmp_path):
        """leaf 包（如 scipy）用纯包名安装（不在 specs 中）"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        captured_stack = {}

        def fake_stack(**kwargs):
            captured_stack.update(kwargs)
            return True, "ok"

        with (
            patch(
                "vibeocr.env_manager.get_pip_source",
                return_value="https://pypi.org/simple",
            ),
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch("vibeocr.env_manager._install_paddle_stack", side_effect=fake_stack),
        ):
            ok, _msg = install_single_dependency(tmp_path, "scipy")

        assert ok is True
        requirements = captured_stack.get("requirements_override", [])
        assert len(requirements) == 1, "单包重装应只装一个包"
        _name, spec, _idx = requirements[0]
        # leaf 包用纯包名（无版本约束，无 extras）
        assert "scipy" in spec
        assert "[" not in spec, f"leaf 包不应有 extras: {spec}"
        # 单包重装：跳过 pip 升级 + 完成日志用具体包名（而非"所有OCR依赖安装完成"）
        assert captured_stack.get("skip_pip_upgrade") is True
        assert "scipy" in captured_stack.get("done_msg", "")

    def test_toplevel_pkg_uses_full_spec(self, tmp_path):
        """顶层包（如 paddleocr）用完整 spec（含 extras+版本约束）"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        captured_stack = {}

        def fake_stack(**kwargs):
            captured_stack.update(kwargs)
            return True, "ok"

        with (
            patch(
                "vibeocr.env_manager.get_pip_source",
                return_value="https://pypi.org/simple",
            ),
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch("vibeocr.env_manager._install_paddle_stack", side_effect=fake_stack),
        ):
            ok, _msg = install_single_dependency(tmp_path, "paddleocr")

        assert ok is True
        requirements = captured_stack.get("requirements_override", [])
        assert len(requirements) == 1
        _name, spec, _idx = requirements[0]
        # 顶层包应有 extras（doc-parser）和版本约束
        assert "paddleocr[doc-parser]" in spec, (
            f"顶层包应用完整 spec（含 extras），实际: {spec}"
        )

    def test_returns_false_when_python_missing(self, tmp_path):
        """Python 运行时不存在时返回失败"""
        with patch(
            "vibeocr.env_manager.get_embedded_python_executable",
            return_value=tmp_path / "nonexistent.exe",
        ):
            ok, msg = install_single_dependency(tmp_path, "scipy")
        assert ok is False
        assert "Python" in msg or "运行时" in msg


class TestInstallDependenciesBatch:
    """install_dependencies_batch 批量重装测试（设置页"重装选中项"）"""

    def test_batch_builds_requirements_with_count(self, tmp_path):
        """批量重装：去重保序 + 每个包带计数展示名 (i/n)"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        captured_stack = {}

        def fake_stack(**kwargs):
            captured_stack.update(kwargs)
            return True, "ok"

        with (
            patch(
                "vibeocr.env_manager.get_pip_source",
                return_value="https://pypi.org/simple",
            ),
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch("vibeocr.env_manager._install_paddle_stack", side_effect=fake_stack),
        ):
            ok, _msg = install_dependencies_batch(
                tmp_path, ["scipy", "einops", "scipy"]
            )

        assert ok is True
        requirements = captured_stack.get("requirements_override", [])
        # 去重后剩 2 个
        assert len(requirements) == 2
        # 计数展示名 (1/2) (2/2)
        names = [r[0] for r in requirements]
        assert "(1/2)" in names[0]
        assert "(2/2)" in names[1]
        # 批量也跳过 pip 升级 + done_msg 带计数
        assert captured_stack.get("skip_pip_upgrade") is True
        assert "2" in captured_stack.get("done_msg", "")

    def test_empty_packages_short_circuits(self, tmp_path):
        """空列表直接返回成功，不调 _install_paddle_stack"""
        with patch(
            "vibeocr.env_manager._install_paddle_stack"
        ) as mock_stack:
            ok, _msg = install_dependencies_batch(tmp_path, [])
        assert ok is True
        mock_stack.assert_not_called()

    def test_returns_false_when_python_missing(self, tmp_path):
        """Python 运行时不存在时返回失败"""
        with patch(
            "vibeocr.env_manager.get_embedded_python_executable",
            return_value=tmp_path / "nonexistent.exe",
        ):
            ok, msg = install_dependencies_batch(tmp_path, ["scipy"])
        assert ok is False
        assert "Python" in msg or "运行时" in msg


class TestGetDirectDependencies:
    """get_direct_dependencies 直接依赖动态推导测试"""

    def test_parses_requires_and_filters_extras(self, tmp_path):
        """从 metadata.requires 解析直接依赖名，过滤 extra marker"""
        import json

        from vibeocr.env_manager import get_direct_dependencies

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kwargs):
            r = MagicMock()
            # requires 返回含 extra marker 的可选依赖 + 无条件直接依赖
            r.returncode = 0
            r.stdout = json.dumps(
                ["numpy>=1.21", 'pandas; extra == "full"', "scipy"]
            )
            return r

        with patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run):
            deps = get_direct_dependencies(python_exe, "mineru")

        # extra == "full" 的 pandas 应被过滤；保留 numpy/scipy
        assert "numpy" in deps
        assert "scipy" in deps
        assert "pandas" not in deps, "仅 extra 条件的依赖应被过滤"

    def test_returns_empty_when_not_installed(self, tmp_path):
        """包未安装（requires 返回 None）时返回空列表"""
        from vibeocr.env_manager import get_direct_dependencies

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kwargs):
            r = MagicMock()
            r.returncode = 0
            r.stdout = "[]"
            return r

        with patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run):
            deps = get_direct_dependencies(python_exe, "nonexistent")
        assert deps == []


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

    def test_uses_packaged_profile_when_repository_files_missing(self, tmp_path):
        """普通 wheel 安装无仓库文件时，应读取随 client wheel 分发的 profile。"""
        # 重置缓存
        import vibeocr.env_manager as em

        em._dep_specs_cache = None

        with patch("vibeocr.env_manager.get_project_root", return_value=tmp_path):
            specs = _load_dep_specs()
        assert specs["paddleocr"] == "paddleocr[doc-parser]>=3.7.0"
        assert specs["paddlepaddle-gpu"] == "paddlepaddle-gpu>=3.3.1"

    def test_raises_when_packaged_profile_is_empty(self, tmp_path):
        """仓库文件与随包 profile 都不可用时才报告损坏。"""
        import vibeocr.env_manager as em

        em._dep_specs_cache = None

        with (
            patch("vibeocr.env_manager.get_project_root", return_value=tmp_path),
            patch(
                "vibeocr.env_manager._load_packaged_dependency_profiles",
                return_value={},
            ),
            pytest.raises(RuntimeError, match="随包分发"),
        ):
            _load_dep_specs()

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


class TestLoadDepSpecsVersionJsonFormat:
    """_load_dep_specs 的 version.json 分支：三层格式向后兼容。

    P1：bump_version 现在写 constraint 串（如 ">=3.3.1" / "==3.3.1+cu126"），
    完整保留 PEP 440 规格（含 local version、多段、!= 等）。读端拼接
    ``{pkg}{constraint}`` 即得合法 pip requirement；extras 从 dep_extras 拼回。

    三层兼容：
    - 当前版：constraint 串 str（以 PEP 440 操作符开头）→ 直接用
    - 曾用版：{"version", "op"} dict → 拼 "{op}{version}"
    - 旧旧版：裸版本号 str（如 "3.3.1"）→ 按 ">=3.3.1"
    """

    def _load_with_version_json(self, tmp_path, dep_versions, dep_extras=None):
        """patch project_root 到 tmp_path，写入 version.json，重置缓存后调用。

        用 try/finally 确保测试后还原 _dep_specs_cache，避免污染后续测试
        （缓存命中会让其它测试读到测试数据而非真实 pyproject）。
        """
        import json

        import vibeocr.env_manager as em

        em._dep_specs_cache = None
        payload: dict = {"version": "1.0.0", "dep_versions": dep_versions}
        if dep_extras is not None:
            payload["dep_extras"] = dep_extras
        (tmp_path / "version.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
        try:
            with patch("vibeocr.env_manager.get_project_root", return_value=tmp_path):
                return _load_dep_specs()
        finally:
            em._dep_specs_cache = None

    def test_parses_constraint_string(self, tmp_path):
        """当前版 constraint 串应直接拼成完整 spec。"""
        specs = self._load_with_version_json(
            tmp_path,
            {
                "paddlepaddle": ">=3.3.1",
                "torch": "==2.6.0+cu126",
            },
        )
        assert specs["paddlepaddle"] == "paddlepaddle>=3.3.1"
        # local version 完整保留
        assert specs["torch"] == "torch==2.6.0+cu126"

    def test_parses_multi_segment_constraint(self, tmp_path):
        """多段约束应完整拼回。"""
        specs = self._load_with_version_json(
            tmp_path,
            {"torch": ">=2.6.0,<3.0.0"},
        )
        assert specs["torch"] == "torch>=2.6.0,<3.0.0"

    def test_parses_not_equal_constraint(self, tmp_path):
        """!= 操作符的 constraint 应正确识别。"""
        specs = self._load_with_version_json(
            tmp_path,
            {"torch": "!=2.7.0"},
        )
        assert specs["torch"] == "torch!=2.7.0"

    def test_parses_legacy_dict_format(self, tmp_path):
        """曾用 {version, op} dict 应拼成 '{op}{version}'。"""
        specs = self._load_with_version_json(
            tmp_path,
            {
                "paddlepaddle": {"version": "3.3.1", "op": ">="},
                "torch": {"version": "2.6.0", "op": "=="},
            },
        )
        assert specs["paddlepaddle"] == "paddlepaddle>=3.3.1"
        assert specs["torch"] == "torch==2.6.0"

    def test_parses_legacy_bare_string(self, tmp_path):
        """旧旧版裸版本号 str 应按 >=N 处理。"""
        specs = self._load_with_version_json(
            tmp_path,
            {"paddlepaddle": "3.3.1", "torch": "2.6.0"},
        )
        assert specs["paddlepaddle"] == "paddlepaddle>=3.3.1"
        assert specs["torch"] == "torch>=2.6.0"

    def test_legacy_dict_default_op_when_missing(self, tmp_path):
        """dict 缺 op 字段时按 >= 处理（防御）。"""
        specs = self._load_with_version_json(
            tmp_path,
            {"paddlepaddle": {"version": "3.3.1"}},  # 无 op
        )
        assert specs["paddlepaddle"] == "paddlepaddle>=3.3.1"

    def test_legacy_dict_empty_op_falls_back_to_ge(self, tmp_path):
        """dict 的 op 为空串时按 >= 处理。"""
        specs = self._load_with_version_json(
            tmp_path,
            {"paddlepaddle": {"version": "3.3.1", "op": ""}},
        )
        assert specs["paddlepaddle"] == "paddlepaddle>=3.3.1"

    def test_rebuilds_extras(self, tmp_path):
        """extras 应从 dep_extras 拼回 spec：pkg[extra]constraint。"""
        specs = self._load_with_version_json(
            tmp_path,
            {"paddleocr": ">=3.7.0"},
            dep_extras={"paddleocr": ["doc-parser"]},
        )
        assert specs["paddleocr"] == "paddleocr[doc-parser]>=3.7.0"

    def test_rebuilds_multi_extras(self, tmp_path):
        """多 extras 应按 [a,b] 顺序拼回。"""
        specs = self._load_with_version_json(
            tmp_path,
            {"paddleocr": ">=3.7.0"},
            dep_extras={"paddleocr": ["doc-parser", "rapid-table"]},
        )
        assert specs["paddleocr"] == "paddleocr[doc-parser,rapid-table]>=3.7.0"

    def test_no_extras_field_means_bare_pkg(self, tmp_path):
        """无 dep_extras 字段时，包名不带 extras（兼容旧 version.json）。"""
        specs = self._load_with_version_json(
            tmp_path,
            {"paddleocr": ">=3.7.0"},  # 无 dep_extras
        )
        assert specs["paddleocr"] == "paddleocr>=3.7.0"

    def test_empty_constraint_means_bare_pkg(self, tmp_path):
        """constraint 为空串（无版本约束）时 spec 只剩包名。"""
        specs = self._load_with_version_json(
            tmp_path,
            {"mineru": ""},  # 无版本约束
        )
        # 裸包名（pip install mineru 装最新版）
        assert specs["mineru"] == "mineru"


class TestUninstallRemovedDeps:
    """P4：uninstall_removed_deps 卸载已从 dep_versions 移除的依赖。"""

    def test_empty_list_returns_success(self, tmp_path):
        """空 removed_names 应立即返回成功，不调用 pip。"""
        from vibeocr.env_manager import uninstall_removed_deps

        ok, msg = uninstall_removed_deps(tmp_path, [])
        assert ok
        assert "无依赖需移除" in msg

    def test_returns_failure_when_python_missing(self, tmp_path):
        """嵌入式 Python 不存在时应失败。"""
        from vibeocr.env_manager import uninstall_removed_deps

        # tmp_path 下无 python/python.exe
        with patch(
            "vibeocr.env_manager.get_embedded_python_executable",
            return_value=tmp_path / "python" / "python.exe",
        ):
            ok, msg = uninstall_removed_deps(tmp_path, ["mineru"])
        assert not ok
        assert "Python 运行时未安装" in msg

    def test_uninstall_calls_pip_for_each_pkg(self, tmp_path):
        """对每个包应调用 pip uninstall -y。"""
        from vibeocr.env_manager import uninstall_removed_deps

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        calls = []

        def mock_run(cmd, **kwargs):
            calls.append(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stdout = "Successfully uninstalled"
            r.stderr = ""
            return r

        with (
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.env_manager._check_imports", return_value={"mineru": False}
            ),
            patch("vibeocr.env_manager.update_cache_field"),
            patch(
                "vibeocr.env_manager.subprocess.Popen",
                side_effect=_popen_side_effect(mock_run),
            ),
        ):
            ok, msg = uninstall_removed_deps(tmp_path, ["mineru", "scipy"])

        assert ok, f"应成功，msg={msg}"
        # 应有两次 pip uninstall 调用
        uninstall_cmds = [c for c in calls if "uninstall" in c]
        assert len(uninstall_cmds) == 2
        # 验证 -y 非交互
        for cmd in uninstall_cmds:
            assert "-y" in cmd

    def test_package_not_installed_treated_as_success(self, tmp_path):
        """pip 返回非零但提示"未安装"时视为成功（目标已达成）。"""
        from vibeocr.env_manager import uninstall_removed_deps

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kwargs):
            r = MagicMock()
            r.returncode = 1  # 非零
            r.stdout = "WARNING: Skipping mineru as it is not installed."
            r.stderr = ""
            return r

        with (
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=python_exe,
            ),
            patch(
                "vibeocr.env_manager._check_imports", return_value={}
            ),
            patch("vibeocr.env_manager.update_cache_field"),
            patch(
                "vibeocr.env_manager.subprocess.Popen",
                side_effect=_popen_side_effect(mock_run),
            ),
        ):
            ok, msg = uninstall_removed_deps(tmp_path, ["mineru"])
        assert ok, f"包未安装应视为成功，msg={msg}"


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

        with patch(
            "vibeocr.env_manager.subprocess.Popen",
            side_effect=_popen_side_effect(mock_run),
        ):
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

        with patch(
            "vibeocr.env_manager.subprocess.Popen",
            side_effect=_popen_side_effect(mock_run),
        ):
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
        """检测的模块集应与 OCR_CHECK_MODULES + OCR_CHECK_LEAF_MODULES 一致（单一源）"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        with patch("vibeocr.env_manager.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            result = _check_imports(python_exe)

        from vibeocr.services.env_config import (
            OCR_CHECK_LEAF_MODULES,
            OCR_CHECK_MODULES,
        )

        # 返回的 key 集合应等于顶层模块 + leaf 模块的包名集合
        expected = set(OCR_CHECK_MODULES.values()) | set(OCR_CHECK_LEAF_MODULES.values())
        assert set(result.keys()) == expected

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


class TestProbeModuleDoubleLayer:
    """_probe_module 双层检测：metadata 判发行版存在 + import 判可导入

    回归（Bug B）：MinerU[core] 间接依赖（torch/paddle/opencv/rapid-table）未装完时，
    `import mineru` 会抛 ImportError，旧 `_check_imports` 误判为"未安装"，
    掩盖了"装了但依赖损坏"的真实状态。双层检测区分二者，import 失败但发行版
    存在时落盘 warning 指向"间接依赖未完成"。
    """

    def test_installed_and_importable(self, tmp_path):
        """发行版存在 + import 成功 → (installed=True, usable=True)"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kwargs):
            r = MagicMock()
            code = cmd[cmd.index("-c") + 1] if "-c" in cmd else ""
            r.returncode = 0  # metadata.version + import 都成功
            r.stderr = ""
            r.stdout = "3.4.0" if "metadata" in code else ""
            return r

        from vibeocr.env_manager import _probe_module

        with patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run):
            installed, usable, missing = _probe_module(python_exe, "mineru", "mineru")

        assert installed is True
        assert usable is True
        assert missing is None, "import 成功时 missing_module 应为 None"

    def test_installed_but_import_fails(self, tmp_path):
        """发行版存在但 import 失败 → (installed=True, usable=False)

        典型场景：mineru[core] 装了发行版，但 torch/paddle 等间接依赖缺失，
        import mineru 抛 ModuleNotFoundError。
        """
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kwargs):
            r = MagicMock()
            code = cmd[cmd.index("-c") + 1] if "-c" in cmd else ""
            if "metadata" in code:
                r.returncode = 0  # 发行版存在
                r.stdout = "3.4.0"
                r.stderr = ""
            else:
                # import 失败（间接依赖缺失）
                r.returncode = 1
                r.stderr = "ModuleNotFoundError: No module named 'torch'"
                r.stdout = ""
            return r

        from vibeocr.env_manager import _probe_module

        with patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run):
            installed, usable, missing = _probe_module(python_exe, "mineru", "mineru")

        assert installed is True, "发行版存在应判 installed=True"
        assert usable is False, "import 失败应判 usable=False（不掩盖）"
        assert missing == "torch", "应从 stderr 抓到缺失模块名 torch"

    def test_installed_but_import_fails_logs_warning(self, tmp_path, caplog):
        """发行版存在但 import 失败时，应 logger.warning 记录'间接依赖'提示"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        import logging

        def mock_run(cmd, **kwargs):
            r = MagicMock()
            code = cmd[cmd.index("-c") + 1] if "-c" in cmd else ""
            if "metadata" in code:
                r.returncode = 0
                r.stdout = "3.4.0"
                r.stderr = ""
            else:
                r.returncode = 1
                r.stderr = "ModuleNotFoundError: No module named 'torch'"
                r.stdout = ""
            return r

        from vibeocr.env_manager import _probe_module

        with (
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
            caplog.at_level(logging.WARNING, logger="vibeocr.env_manager"),
        ):
            _probe_module(python_exe, "mineru", "mineru")

        warn_msgs = " ".join(
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert "mineru" in warn_msgs, "warning 应提及包名"
        assert "间接依赖" in warn_msgs or "import" in warn_msgs.lower(), (
            f"应提示间接依赖/import 问题，实际: {warn_msgs}"
        )

    def test_not_installed(self, tmp_path):
        """发行版不存在 → (installed=False, usable=False)，且跳过 import 探测"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        call_count = {"n": 0}

        def mock_run(cmd, **kwargs):
            call_count["n"] += 1
            r = MagicMock()
            # metadata.version 找不到包（PackageNotFoundError → returncode 1）
            r.returncode = 1
            r.stderr = "PackageNotFoundError: mineru"
            r.stdout = ""
            return r

        from vibeocr.env_manager import _probe_module

        with patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run):
            installed, usable, missing = _probe_module(python_exe, "mineru", "mineru")

        assert installed is False
        assert usable is False
        assert missing is None, "发行版不存在时 missing_module 应为 None"
        # 发行版不存在时不应再做 import 探测（省一次 subprocess）
        assert call_count["n"] == 1, (
            f"包不存在时应只探 metadata（1 次 subprocess），实际: {call_count['n']}"
        )

    def test_installed_but_module_self_missing_warns_broken_install(
        self, tmp_path, caplog
    ):
        """残缺安装（合成纯 Python 包场景）：metadata 在但 import 报模块自身缺失

        回归：旧 warning 统一称"间接依赖未完成"，但纯 Python 包无间接依赖，
        真实原因是 .dist-info 残留导致模块文件缺失。warning 文案应指向"残缺/损坏"，
        不应再误导为"间接依赖"。

        用合成包名 ``brokenpkg`` 而非真实 ``fonttools``：fonttools 的真实 import 名是
        fontTools（大写 T，PEP 235 区分大小写），用它做"import 失败"fixture 会与
        OCR_CHECK_MODULES 的真实映射冲突。brokenpkg 是不存在的包，纯粹验证机制。
        """
        import logging

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kwargs):
            r = MagicMock()
            code = cmd[cmd.index("-c") + 1] if "-c" in cmd else ""
            if "metadata" in code:
                r.returncode = 0  # 发行版元数据存在（.dist-info 残留）
                r.stdout = "1.0.0"
                r.stderr = ""
            else:
                # import brokenpkg 报模块自身缺失（非间接依赖）
                r.returncode = 1
                r.stderr = (
                    'Traceback (most recent call last):\n  File "<string>", '
                    'line 1, in <module>\n    import brokenpkg\n'
                    "ModuleNotFoundError: No module named 'brokenpkg'"
                )
                r.stdout = ""
            return r

        from vibeocr.env_manager import _probe_module

        with (
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
            caplog.at_level(logging.WARNING, logger="vibeocr.env_manager"),
        ):
            installed, usable, missing = _probe_module(
                python_exe, "brokenpkg", "brokenpkg"
            )

        assert installed is True, ".dist-info 残留时 metadata 层应判 installed=True"
        assert usable is False, "import 失败应判 usable=False"
        assert missing == "brokenpkg", "本体残缺时抓到的就是模块自身名"
        warn_msgs = " ".join(
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert "brokenpkg" in warn_msgs
        assert "残缺" in warn_msgs or "损坏" in warn_msgs, (
            f"残缺安装应提示'残缺/损坏'，实际: {warn_msgs}"
        )
        assert "间接依赖" not in warn_msgs, (
            f"纯 Python 包无间接依赖，不应误报'间接依赖未完成'，实际: {warn_msgs}"
        )

    def test_installed_but_indirect_dep_missing_warns_indirect(
        self, tmp_path, caplog
    ):
        """间接依赖缺失（mineru 场景）：import mineru 报 No module named 'torch'

        回归：A/B 类区分后，B 类（缺的是别的模块）仍应提示"间接依赖未完成"。
        """
        import logging

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kwargs):
            r = MagicMock()
            code = cmd[cmd.index("-c") + 1] if "-c" in cmd else ""
            if "metadata" in code:
                r.returncode = 0
                r.stdout = "3.4.0"
                r.stderr = ""
            else:
                r.returncode = 1
                r.stderr = "ModuleNotFoundError: No module named 'torch'"
                r.stdout = ""
            return r

        from vibeocr.env_manager import _probe_module

        with (
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
            caplog.at_level(logging.WARNING, logger="vibeocr.env_manager"),
        ):
            installed, usable, missing = _probe_module(python_exe, "mineru", "mineru")

        assert installed is True
        assert usable is False
        assert missing == "torch"
        warn_msgs = " ".join(
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert "mineru" in warn_msgs
        assert "间接依赖" in warn_msgs, (
            f"缺 torch 应提示'间接依赖未完成'，实际: {warn_msgs}"
        )


class TestCheckImportsDoubleLayer:
    """改写后的 _check_imports：双层检测，返回签名不变 + 损坏时落盘 warning

    回归（Bug B）：MinerU 装了发行版但 import 失败时，旧逻辑静默判 False，
    用户无法区分"包没装"vs"装了但依赖损坏"。
    """

    def test_returns_mapping_signature_unchanged(self, tmp_path):
        """返回签名应保持 dict[str,bool]，key 集合 == 顶层 + leaf 模块包名"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kwargs):
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            r.stdout = "1.0.0" if "metadata" in (cmd[cmd.index("-c") + 1] if "-c" in cmd else "") else ""
            return r

        with patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run):
            result = _check_imports(python_exe)

        from vibeocr.services.env_config import (
            OCR_CHECK_LEAF_MODULES,
            OCR_CHECK_MODULES,
        )

        expected = set(OCR_CHECK_MODULES.values()) | set(OCR_CHECK_LEAF_MODULES.values())
        assert set(result.keys()) == expected, (
            "key 集合应等于顶层 + leaf 模块的包名集合"
        )
        assert all(isinstance(v, bool) for v in result.values())

    def test_installed_but_unusable_still_returns_false(self, tmp_path, caplog):
        """mineru 装了发行版但 import 崩 → usable=False（不掩盖），且落盘 warning"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        import logging

        def mock_run(cmd, **kwargs):
            r = MagicMock()
            code = cmd[cmd.index("-c") + 1] if "-c" in cmd else ""
            # mineru：metadata 成功，import 失败（间接依赖缺失）
            if "metadata" in code:
                if "mineru" in code:
                    r.returncode = 0
                    r.stdout = "3.4.0"
                else:
                    r.returncode = 0
                    r.stdout = "1.0.0"
                r.stderr = ""
            else:
                # import 路径：mineru 失败，其余成功
                if code == "import mineru":
                    r.returncode = 1
                    r.stderr = "ModuleNotFoundError: No module named 'torch'"
                else:
                    r.returncode = 0
                    r.stderr = ""
                r.stdout = ""
            return r

        with (
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
            caplog.at_level(logging.WARNING, logger="vibeocr.env_manager"),
        ):
            result = _check_imports(python_exe)

        assert result["mineru"] is False, "import 失败应判 False（可用性准确）"
        warn_msgs = " ".join(
            r.message for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert "mineru" in warn_msgs, "应落盘 warning 指向 mineru 间接依赖"


class TestCheckImportsDetailed:
    """_check_imports_detailed：返回 (installed, usable) 二元组

    补装逻辑用此区分三类状态：
    - (True, True) → 已装可用，跳过
    - (False, False) → 未安装，普通 pip install
    - (True, False) → 残缺安装（.dist-info 残留），pip install --force-reinstall
    """

    def test_returns_tuple_mapping(self, tmp_path):
        """返回 dict[pkg, (installed, usable)]，key 集合 == OCR_CHECK_MODULES.values()"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kwargs):
            r = MagicMock()
            code = cmd[cmd.index("-c") + 1] if "-c" in cmd else ""
            r.returncode = 0  # metadata + import 全成功
            r.stderr = ""
            r.stdout = "1.0.0" if "metadata" in code else ""
            return r

        with patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run):
            from vibeocr.env_manager import _check_imports_detailed

            result = _check_imports_detailed(python_exe)

        from vibeocr.services.env_config import (
            OCR_CHECK_LEAF_MODULES,
            OCR_CHECK_MODULES,
        )

        expected = set(OCR_CHECK_MODULES.values()) | set(OCR_CHECK_LEAF_MODULES.values())
        assert set(result.keys()) == expected
        for _pkg, (installed, usable) in result.items():
            assert isinstance(installed, bool)
            assert isinstance(usable, bool)

    def test_broken_install_reports_installed_true_usable_false(self, tmp_path):
        """fonttools 残缺安装：metadata 在 + import 自身失败 → (True, False)

        这是补装走 --force-reinstall 的触发条件。

        注意：fonttools 的真实 import 名是 ``fontTools``（大写 T，PEP 235），
        OCR_CHECK_MODULES 已修正为 ``fontTools``。mock 需匹配 ``import fontTools``
        才能模拟"import 失败"。结果 dict 的 key 仍是 pip 包名 ``fonttools``。
        """
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kwargs):
            r = MagicMock()
            code = cmd[cmd.index("-c") + 1] if "-c" in cmd else ""
            if "metadata" in code:
                r.returncode = 0
                r.stdout = "4.61.1"
                r.stderr = ""
            elif code == "import fontTools":
                r.returncode = 1
                r.stderr = "ModuleNotFoundError: No module named 'fontTools'"
                r.stdout = ""
            else:
                r.returncode = 0
                r.stderr = ""
                r.stdout = ""
            return r

        with patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run):
            from vibeocr.env_manager import _check_imports_detailed

            result = _check_imports_detailed(python_exe)

        assert result["fonttools"] == (True, False), (
            f"fonttools 残缺应为 (True, False)，实际: {result['fonttools']}"
        )


class TestGetDependencyVersionsImportlibMetadata:
    """get_dependency_versions 改用 importlib.metadata（修复 mineru 版本空串）

    回归（Bug B）：getattr(mineru, '__version__', '') 返回空串——现代 mineru 包
    不暴露 __version__，依赖 importlib.metadata.version('mineru')。导致设置页
    表格显示"（版本未知）"。
    """

    def test_uses_importlib_metadata_version(self, tmp_path):
        """版本应来自 importlib.metadata.version，而非 __version__ 属性"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kwargs):
            r = MagicMock()
            code = cmd[cmd.index("-c") + 1] if "-c" in cmd else ""
            r.returncode = 0
            r.stderr = ""
            # metadata 路径返回版本号
            r.stdout = "3.4.0" if "metadata" in code else ""
            return r

        with patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run):
            from vibeocr.env_manager import get_dependency_versions

            versions = get_dependency_versions(python_exe)

        # mineru 应有版本号（来自 metadata），不再是空串
        assert versions["mineru"] == "3.4.0", (
            f"mineru 版本应来自 importlib.metadata，实际: {versions['mineru']!r}"
        )

    def test_falls_back_to_dunder_version_when_metadata_fails(self, tmp_path):
        """metadata 探测失败时，回退 getattr(module, '__version__', '')"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kwargs):
            r = MagicMock()
            code = cmd[cmd.index("-c") + 1] if "-c" in cmd else ""
            if "metadata" in code:
                # metadata 失败（旧包无 metadata）
                r.returncode = 1
                r.stderr = "PackageNotFoundError"
                r.stdout = ""
            else:
                # 回退路径：getattr(module, '__version__', '')
                r.returncode = 0
                r.stderr = ""
                r.stdout = "2.6.0"  # __version__ 返回值
            return r

        with patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run):
            from vibeocr.env_manager import get_dependency_versions

            versions = get_dependency_versions(python_exe)

        # 回退到 __version__
        assert versions["torch"] == "2.6.0", (
            f"metadata 失败应回退 __version__，实际: {versions['torch']!r}"
        )

    def test_returns_empty_for_not_installed(self, tmp_path):
        """包未安装（metadata + __version__ 都失败）→ 空串"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kwargs):
            r = MagicMock()
            r.returncode = 1
            r.stderr = "PackageNotFoundError"
            r.stdout = ""
            return r

        with patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run):
            from vibeocr.env_manager import get_dependency_versions

            versions = get_dependency_versions(python_exe)

        from vibeocr.services.env_config import OCR_CHECK_MODULES

        assert all(v == "" for v in versions.values()), (
            f"未安装应全为空串，实际: {versions}"
        )
        assert set(versions.keys()) == set(OCR_CHECK_MODULES.values())


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
            patch(
                "vibeocr.env_manager.subprocess.Popen",
                side_effect=_popen_side_effect(mock_run),
            ),
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


class TestGpuNoFallbackPyPi:
    """GPU 依赖镜像失败时重试同源镜像，绝不回退 PyPI（避免装成 CPU 版）"""

    @staticmethod
    def _specs():
        return {
            "paddlepaddle-gpu": "paddlepaddle-gpu>=3.3.1",
            "paddleocr": "paddleocr[doc-parser]>=3.7.0",
            "mineru": "mineru[core]>=3.4.0",
            "torch": "torch>=2.6.0",
        }

    def test_gpu_req_retries_same_mirror_not_pypi(self, tmp_path):
        """GPU 项（torch）镜像失败 → 重试仍带 -i mirror，不出现裸 PyPI 调用，最终返回 False"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        calls = []

        def mock_run(cmd, **kw):
            calls.append(cmd)
            r = MagicMock()
            # pip 升级成功；torch 安装始终失败（模拟大文件 IncompleteRead）
            if "--upgrade" in cmd:
                r.returncode = 0
                r.stderr = ""
            elif "torch" in " ".join(cmd):
                r.returncode = 1
                r.stderr = "ERROR: IncompleteRead Connection broken"
            else:
                r.returncode = 0
                r.stderr = ""
            return r

        with (
            patch(
                "vibeocr.env_manager.subprocess.Popen",
                side_effect=_popen_side_effect(mock_run),
            ),
        ):
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

        assert not ok, "GPU torch 持续失败应返回 False"
        # 所有 torch 相关安装命令都应带 -i <mirror>（重试同源，不回退 PyPI）
        torch_cmds = [c for c in calls if "torch" in " ".join(c)]
        assert len(torch_cmds) >= 2, f"应至少重试 1 次，实际 torch 命令数: {len(torch_cmds)}"
        for c in torch_cmds:
            joined = " ".join(c)
            assert "-i" in joined, f"GPU 重试仍应带 -i mirror，实际: {joined}"
            assert "mirrors.nju.edu.cn" in joined or "pytorch" in joined, (
                f"应走 torch 镜像源，实际: {joined}"
            )

    def test_cpu_req_falls_back_to_pypi_on_version_not_found(self, tmp_path):
        """非 GPU 包镜像源确无此版本 → 回退官方 PyPI"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        calls = []

        def mock_run(cmd, **kw):
            calls.append(cmd)
            r = MagicMock()
            if "--upgrade" in cmd:
                r.returncode = 0
                r.stderr = ""
            elif "-i" in cmd and "paddleocr" in " ".join(cmd):
                # 镜像源无此版本
                r.returncode = 1
                r.stderr = "ERROR: Could not find a version"
            else:
                # 回退 PyPI 成功
                r.returncode = 0
                r.stderr = ""
            return r

        with patch(
            "vibeocr.env_manager.subprocess.Popen",
            side_effect=_popen_side_effect(mock_run),
        ):
            ok, _msg = _install_paddle_stack(
                python_exe=python_exe,
                specs=self._specs(),
                pip_source="https://pypi.org/simple",
                network_type="domestic",
                use_gpu=False,
                cuda_version=None,
                report_fn=lambda s, m: None,
                success_msg="done",
            )

        assert ok, "CPU 包回退 PyPI 成功应返回 True"
        # 应有一次不带 -i 的 paddleocr 安装（回退 PyPI）
        paddleocr_no_index = [
            c
            for c in calls
            if "paddleocr" in " ".join(c)
            and "install" in c
            and "--upgrade" not in c
            and "-i" not in c
        ]
        assert len(paddleocr_no_index) >= 1, "应回退 PyPI（不带 -i）"

    def test_pip_install_has_retries_flag(self, tmp_path):
        """所有 pip install 命令都应带 --retries（大文件韧性）"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        calls = []

        def mock_run(cmd, **kw):
            calls.append(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        with patch(
            "vibeocr.env_manager.subprocess.Popen",
            side_effect=_popen_side_effect(mock_run),
        ):
            _install_paddle_stack(
                python_exe=python_exe,
                specs=self._specs(),
                pip_source="https://pypi.org/simple",
                network_type="domestic",
                use_gpu=True,
                cuda_version="cu126",
                report_fn=lambda s, m: None,
                success_msg="done",
            )

        install_cmds = [c for c in calls if "install" in c]
        assert len(install_cmds) > 0
        for c in install_cmds:
            assert "--retries" in c, f"pip install 应带 --retries，实际: {c}"
            assert "--timeout" in c, f"pip install 应带 --timeout，实际: {c}"


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
            # uninstall 等仍走 subprocess.run；安装路径（_run_pip）走 Popen。
            # 两者共用 mock_run，命令都 append 到同一 calls 列表。
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
            patch(
                "vibeocr.env_manager.subprocess.Popen",
                side_effect=_popen_side_effect(mock_run),
            ),
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
        """切换成功后应写 pending_backend 到缓存

        注意：切换路径现在会调用 update_cache_field 两次——
        1. install_embedded_dependencies 成功后写 dependencies（安装后刷新缓存）；
        2. switch_paddle_backend 写 pending_backend。
        本测试只验证 pending_backend 的写入，不限定调用次数。
        """
        ok, _msg, _calls, mock_update = self._run_switch(tmp_path, "cpu")
        assert ok
        # 在所有 update_cache_field 调用中找到写 pending_backend 的那次
        pending_calls = [
            c for c in mock_update.call_args_list if c.args[1] == "pending_backend"
        ]
        assert len(pending_calls) == 1, (
            f"应写一次 pending_backend，实际: {mock_update.call_args_list}"
        )
        assert pending_calls[0].args[2] == "cpu"

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

    def test_download_retries_on_failure(self, tmp_path):
        """下载失败时应重试（max_retries 次），最终成功"""
        import contextlib

        from vibeocr.env_manager import download_file_with_progress

        dest = tmp_path / "fake.tar.gz"
        call_count = {"n": 0}

        def fake_resp_factory():
            fake_resp = MagicMock()
            fake_resp.headers = {"content-length": "4"}
            fake_resp.status = 200
            fake_resp.__enter__ = MagicMock(return_value=fake_resp)
            fake_resp.__exit__ = MagicMock(return_value=False)
            return fake_resp

        def side_effect(req, timeout=30):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise ConnectionError("simulated network break")
            resp = fake_resp_factory()
            resp.read.side_effect = [b"data", b""]
            return resp

        with (
            patch("vibeocr.env_manager.urlopen", side_effect=side_effect),
            patch("vibeocr.env_manager.Request"),
            contextlib.nullcontext(),
        ):
            ok = download_file_with_progress(
                "http://x/y.tar.gz", dest, "Python(镜像)", max_retries=3
            )

        assert ok
        assert call_count["n"] == 3, f"应重试到第 3 次成功，实际 {call_count['n']}"

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
            patch(
                "vibeocr.env_manager.subprocess.Popen",
                side_effect=_popen_side_effect(mock_run),
            ),
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
            patch(
                "vibeocr.env_manager.subprocess.Popen",
                side_effect=_popen_side_effect(mock_run),
            ),
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
        with patch(
            "vibeocr.env_manager.subprocess.Popen",
            side_effect=_popen_side_effect(mock_run),
        ):
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

        with patch(
            "vibeocr.env_manager.subprocess.Popen",
            side_effect=_popen_side_effect(mock_run),
        ):
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
        # GPU 完整列表：paddle + paddleocr + mineru + markdown + pymupdf + fastapi
        # + uvicorn + pydantic + fonttools + torch = 10 个安装命令
        # （PDF 后端依赖 + markdown 已从 exe 包排除，由便携 Python 安装）
        assert len(install_cmds) == 10, (
            f"GPU 完整列表应装 10 个，实际: {install_cmds}"
        )

    def test_continues_after_package_failure(self, tmp_path):
        """单个包失败不应中止整个安装：记录失败后继续装后续包。

        回归（用户痛点"漏装、需二次补装"）：旧逻辑遇首个 pip 失败即 return False，
        排在后面的 fonttools/fastapi 等小包被跳过。改为收集失败继续装，
        循环结束后汇总返回失败（让用户二次补装真正失败的）。
        """
        from vibeocr.env_manager import _install_paddle_stack

        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        calls = []

        def mock_run(cmd, **kw):
            calls.append(cmd)
            r = MagicMock()
            # pip 升级成功
            if "--upgrade" in cmd:
                r.returncode = 0
                r.stderr = ""
                r.stdout = ""
            # 让 mineru 安装失败（版本找不到走 PyPI 回退也失败）
            elif "mineru" in " ".join(cmd):
                r.returncode = 1
                r.stderr = "No matching distribution found for mineru"
                r.stdout = ""
            else:
                r.returncode = 0
                r.stderr = ""
                r.stdout = ""
            return r

        with patch(
            "vibeocr.env_manager.subprocess.Popen",
            side_effect=_popen_side_effect(mock_run),
        ):
            ok, msg = _install_paddle_stack(
                python_exe=python_exe,
                specs=self._specs(),
                pip_source="https://pypi.org/simple",
                network_type="domestic",
                use_gpu=False,
                cuda_version=None,
                report_fn=lambda s, m: None,
                success_msg="done",
            )

        # 有失败 → 返回失败
        assert ok is False, "有包失败应返回 False（汇总）"
        assert "mineru" in msg, "失败消息应提及 mineru"
        # 关键：mineru 失败后，排在它后面的 fonttools 仍应被安装
        install_cmds = self._filter_install_cmds(calls)
        joined = " ".join(" ".join(c) for c in install_cmds)
        assert "mineru" in joined, "mineru 应被尝试安装"
        assert "fonttools" in joined, (
            f"mineru 失败后 fonttools 仍应被安装（漏装修复），实际命令: {joined}"
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
            import_code = cmd[cmd.index("-c") + 1] if "-c" in cmd else ""
            # _check_imports 现走双层 _probe_module：metadata 判发行版存在 + import 判可用。
            # metadata 调用形如 "import importlib.metadata as m; m.version('paddlepaddle')"
            if "metadata" in import_code and "version" in import_code:
                # 所有发行版都存在（installed=True），让双层探针进入 import 层
                r.returncode = 0
                r.stdout = "1.0.0"
            elif import_code.startswith("import "):
                # import 层：paddle/paddleocr + 所有 paddlex[ocr] leaf 包可用
                # （usable=True），mineru/torch 不可用。
                # leaf 包必须标可用，否则 install_missing_dependencies 的 leaf 缺失
                # 检测会把 paddleocr 加入重装 subset（即使顶层可用），破坏"已装跳过"语义。
                from vibeocr.services.env_config import OCR_CHECK_LEAF_MODULES

                usable_modules = {"paddle", "paddleocr"} | set(OCR_CHECK_LEAF_MODULES.keys())
                module = import_code.split()[1]
                r.returncode = 0 if module in usable_modules else 1
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
            # _check_imports 走 subprocess.run（mock_run 区分 metadata/import 成功失败）；
            # 安装路径走 Popen。
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
            patch(
                "vibeocr.env_manager.subprocess.Popen",
                side_effect=_popen_side_effect(mock_run),
            ),
        ):
            ok, msg = install_missing_dependencies(
                tmp_path,
                use_gpu=True,
                cuda_version="cu126",
                progress_callback=lambda s, m: None,
            )

        assert ok, f"应成功: {msg}"
        pip_installs = self._filter_install_cmds(all_calls)
        # paddle + paddleocr 已装 → 跳过；只应装 mineru + markdown + torch
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
            # _check_imports 走 subprocess.run；安装路径走 Popen。两者都用 mock_run。
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
            patch(
                "vibeocr.env_manager.subprocess.Popen",
                side_effect=_popen_side_effect(mock_run),
            ),
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
            # _check_imports 走 subprocess.run；安装路径走 Popen。
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
            patch(
                "vibeocr.env_manager.subprocess.Popen",
                side_effect=_popen_side_effect(mock_run),
            ),
        ):
            ok, _msg = install_missing_dependencies(
                tmp_path,
                use_gpu=False,
                progress_callback=lambda s, m: None,
            )

        assert ok
        pip_installs = self._filter_install_cmds(all_calls)
        # CPU 模式完整列表：paddle + paddleocr + mineru + markdown + pymupdf
        # + fastapi + uvicorn + pydantic + fonttools = 9 个（含 PDF 后端依赖）
        assert len(pip_installs) == 9, (
            f"CPU 全量应装 9 个，实际: {pip_installs}"
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
            # _check_imports 走 subprocess.run；安装路径走 Popen。
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
            patch(
                "vibeocr.env_manager.subprocess.Popen",
                side_effect=_popen_side_effect(mock_run),
            ),
            patch("vibeocr.env_manager.detect_gpu", return_value=(True, "cu126")),
        ):
            ok, _msg = install_missing_dependencies(
                tmp_path,
                force_backend="gpu",
                progress_callback=lambda s, m: None,
            )

        assert ok
        pip_installs = self._filter_install_cmds(all_calls)
        # GPU 完整列表：CPU 9 个 + torch = 10 个
        assert len(pip_installs) == 10, (
            f"GPU force_backend 应装 10 个，实际: {pip_installs}"
        )

    def test_broken_install_uses_force_reinstall(self, tmp_path):
        """fonttools 残缺安装（.dist-info 在但 import 失败）应 --force-reinstall 补装

        回归：普通 `pip install fonttools` 看到残留 .dist-info 报 already satisfied 跳过，
        import 永远失败（用户报告"装几次还失败"）。残缺时必须 --force-reinstall --no-deps
        才能真正重写模块文件。
        """
        from vibeocr.env_manager import install_missing_dependencies

        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        all_calls = []

        def mock_run(cmd, **kw):
            all_calls.append(cmd)
            r = MagicMock()
            r.stderr = ""
            code = cmd[cmd.index("-c") + 1] if "-c" in cmd else ""
            # metadata 层：所有包发行版都"存在"
            if "metadata" in code and "version" in code:
                r.returncode = 0
                r.stdout = "1.0.0"
            elif code == "import fontTools":
                # fonttools 残缺：import 报模块自身缺失（非间接依赖）。
                # 注意 import 名是 fontTools（大写 T，PEP 235），与 OCR_CHECK_MODULES 一致。
                r.returncode = 1
                r.stderr = "ModuleNotFoundError: No module named 'fontTools'"
                r.stdout = ""
            elif code.startswith("import "):
                # 其余包正常可用 → 跳过，只补装 fonttools
                r.returncode = 0
                r.stdout = ""
            else:
                # pip install 成功
                r.returncode = 0
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
            patch("vibeocr.env_manager._load_dep_specs", return_value=self._specs()),
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
            patch(
                "vibeocr.env_manager.subprocess.Popen",
                side_effect=_popen_side_effect(mock_run),
            ),
        ):
            ok, _msg = install_missing_dependencies(
                tmp_path,
                use_gpu=False,
                progress_callback=lambda s, m: None,
            )

        assert ok, "fonttools 补装应成功"
        pip_installs = self._filter_install_cmds(all_calls)
        # 只应补装 fonttools（其余都 usable）
        assert len(pip_installs) == 1, (
            f"只补装 fonttools，实际: {pip_installs}"
        )
        fonttools_cmd = pip_installs[0]
        assert "--force-reinstall" in fonttools_cmd, (
            f"残缺安装应 --force-reinstall，实际: {fonttools_cmd}"
        )
        assert "--no-deps" in fonttools_cmd, (
            f"残缺安装应 --no-deps 避免重装依赖树，实际: {fonttools_cmd}"
        )
        assert "fonttools" in " ".join(fonttools_cmd)


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
            patch(
                "vibeocr.env_manager.subprocess.Popen",
                side_effect=_popen_side_effect(mock_run),
            ),
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


class TestRunPip:
    """_run_pip 辅助函数：可取消、带超时、交出 Popen 句柄"""

    def test_returns_completed_process_with_returncode_stdout_stderr(self):
        """正常完成应返回 CompletedProcess（兼容旧 subprocess.run 返回类型）"""
        from vibeocr.env_manager import _run_pip

        proc = MagicMock()
        proc.returncode = 0
        proc.poll.side_effect = [None, 0]  # 首轮"运行中"，次轮已退出
        proc.communicate.return_value = ("stdout-content", "stderr-content")

        with patch(
            "vibeocr.env_manager.subprocess.Popen", return_value=proc
        ) as popen:
            result = _run_pip(["python", "-m", "pip", "install", "x"], timeout=10)

        assert result.returncode == 0
        assert result.stdout == "stdout-content"
        assert result.stderr == "stderr-content"
        assert popen.call_args.kwargs["errors"] == "replace"

    def test_on_proc_callback_receives_popen_handle(self):
        """on_proc 回调应收到 Popen 句柄（调用方可据此 kill 子进程）"""
        from vibeocr.env_manager import _run_pip

        proc = MagicMock()
        proc.returncode = 0
        proc.poll.side_effect = [None, 0]
        proc.communicate.return_value = ("", "")

        received = []
        with patch("vibeocr.env_manager.subprocess.Popen", return_value=proc):
            _run_pip(["python", "-m", "pip"], timeout=10, on_proc=received.append)

        assert received == [proc], "on_proc 应收到 Popen 句柄"

    def test_cancel_event_kills_proc_and_raises_install_cancelled(self):
        """cancel_event 被 set 后应 kill 子进程并抛 InstallCancelled"""
        import threading

        from vibeocr.env_manager import InstallCancelled, _run_pip

        proc = MagicMock()
        proc.returncode = -1
        # poll() 始终返回 None（进程"永不退出"），强制走取消路径
        proc.poll.return_value = None
        proc.communicate.return_value = ("", "")

        cancel_event = threading.Event()

        def _set_cancel_later():
            import time

            time.sleep(0.3)
            cancel_event.set()

        threading.Thread(target=_set_cancel_later, daemon=True).start()

        with patch("vibeocr.env_manager.subprocess.Popen", return_value=proc):
            with pytest.raises(InstallCancelled):
                _run_pip(
                    ["python", "-m", "pip", "install", "x"],
                    timeout=60,
                    cancel_event=cancel_event,
                )
        # 子进程应被 kill（避免孤儿）
        proc.kill.assert_called()


class TestInstallPaddleStackCancel:
    """_install_paddle_stack 的协作式取消"""

    def test_cancel_before_first_package_returns_cancelled(self, tmp_path):
        """cancel_event 在循环开始前已 set → 应立即返回取消"""
        import threading

        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        cancel_event = threading.Event()
        cancel_event.set()  # 预先 set

        # mock pip 升级成功（升级走 _run_pip，cancel 后 for 循环首项检查）
        def mock_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        reports = []
        with patch(
            "vibeocr.env_manager.subprocess.Popen",
            side_effect=_popen_side_effect(mock_run),
        ):
            ok, msg = _install_paddle_stack(
                python_exe=python_exe,
                specs={"paddlepaddle": "paddlepaddle>=3", "paddleocr": "paddleocr>=3",
                       "mineru": "mineru>=3"},
                pip_source="https://pypi.org/simple",
                network_type="domestic",
                use_gpu=False,
                cuda_version=None,
                report_fn=lambda s, m: reports.append((s, m)),
                success_msg="done",
                cancel_event=cancel_event,
            )

        assert ok is False
        assert "取消" in msg

    def test_cancel_mid_install_aborts_remaining_packages(self, tmp_path):
        """安装第一个包后 set cancel → 不应安装第二个包"""
        import threading

        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        cancel_event = threading.Event()
        installed_pkgs = []

        def mock_run(cmd, **kw):
            installed_pkgs.append(" ".join(cmd))
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            # 第一个包（paddle）装完后触发取消
            if "paddlepaddle" in " ".join(cmd) and "pip" in cmd:
                if not any("upgrade" in c for c in cmd):
                    cancel_event.set()
            return r

        with patch(
            "vibeocr.env_manager.subprocess.Popen",
            side_effect=_popen_side_effect(mock_run),
        ):
            ok, msg = _install_paddle_stack(
                python_exe=python_exe,
                specs={"paddlepaddle": "paddlepaddle>=3", "paddleocr": "paddleocr>=3",
                       "mineru": "mineru>=3"},
                pip_source="https://pypi.org/simple",
                network_type="domestic",
                use_gpu=False,
                cuda_version=None,
                report_fn=lambda s, m: None,
                success_msg="done",
                cancel_event=cancel_event,
            )

        assert ok is False
        assert "取消" in msg
        # paddleocr 不应被安装（在 paddle 装完后的循环检查就中止了）
        pkg_installs = [c for c in installed_pkgs if "paddleocr" in c]
        assert len(pkg_installs) == 0, f"取消后不应装 paddleocr，实际: {pkg_installs}"


class TestInstallWritesCache:
    """安装成功后应刷新依赖缓存（修复 2）"""

    def test_success_writes_dependencies_via_update_cache_field(self, tmp_path):
        """_install_paddle_stack 成功后应 update_cache_field 写 dependencies"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        with (
            patch(
                "vibeocr.env_manager.subprocess.Popen",
                side_effect=_popen_side_effect(mock_run),
            ),
            patch("vibeocr.env_manager._quick_verify_deps",
                  return_value={"paddlepaddle": True, "paddleocr": True,
                                "mineru": True, "markdown": True, "torch": True}),
            patch("vibeocr.env_manager.update_cache_field") as mock_update,
        ):
            ok, _msg = _install_paddle_stack(
                python_exe=python_exe,
                specs={"paddlepaddle": "paddlepaddle>=3", "paddleocr": "paddleocr>=3",
                       "mineru": "mineru>=3"},
                pip_source="https://pypi.org/simple",
                network_type="domestic",
                use_gpu=False,
                cuda_version=None,
                report_fn=lambda s, m: None,
                success_msg="done",
                project_root=tmp_path,
            )

        assert ok
        # 应调用 update_cache_field 写 dependencies
        dep_writes = [c for c in mock_update.call_args_list
                      if len(c.args) >= 2 and c.args[1] == "dependencies"]
        assert len(dep_writes) == 1, f"应写一次 dependencies，实际: {mock_update.call_args_list}"

    def test_no_project_root_skips_cache_write(self, tmp_path):
        """project_root=None 时不应写缓存（向后兼容，如旧调用方）"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        with (
            patch(
                "vibeocr.env_manager.subprocess.Popen",
                side_effect=_popen_side_effect(mock_run),
            ),
            patch("vibeocr.env_manager.update_cache_field") as mock_update,
        ):
            ok, _msg = _install_paddle_stack(
                python_exe=python_exe,
                specs={"paddlepaddle": "paddlepaddle>=3", "paddleocr": "paddleocr>=3",
                       "mineru": "mineru>=3"},
                pip_source="https://pypi.org/simple",
                network_type="domestic",
                use_gpu=False,
                cuda_version=None,
                report_fn=lambda s, m: None,
                success_msg="done",
                project_root=None,  # 不写缓存
            )

        assert ok
        mock_update.assert_not_called()

    def test_write_failure_does_not_break_install(self, tmp_path):
        """写缓存失败不应影响安装成功结果（缓存只是优化，非关键路径）"""
        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        with (
            patch(
                "vibeocr.env_manager.subprocess.Popen",
                side_effect=_popen_side_effect(mock_run),
            ),
            patch("vibeocr.env_manager._quick_verify_deps",
                  return_value={"paddlepaddle": True}),
            patch("vibeocr.env_manager.update_cache_field",
                  side_effect=RuntimeError("disk full")),
        ):
            ok, msg = _install_paddle_stack(
                python_exe=python_exe,
                specs={"paddlepaddle": "paddlepaddle>=3", "paddleocr": "paddleocr>=3",
                       "mineru": "mineru>=3"},
                pip_source="https://pypi.org/simple",
                network_type="domestic",
                use_gpu=False,
                cuda_version=None,
                report_fn=lambda s, m: None,
                success_msg="done",
                project_root=tmp_path,
            )

        # 即使写缓存失败，安装仍应成功
        assert ok, f"写缓存失败不应中断安装，msg={msg}"


class TestDetectDependencyUpdatesLockedVersions:
    """detect_dependency_updates 用锁定版本（uv.lock）作比较基准。

    回归 Bug：便携环境已装 mineru 3.4.0，满足 ``>=3.4.0`` 下界，但实际锁定版本是
    3.4.2。旧逻辑只比下界（3.4.0 < 3.4.0 为 False），误报"依赖已是最新"。
    修复后应优先用锁定版 3.4.2 比较，检出 3.4.0 → 3.4.2 的更新。
    """

    def _run_detect(
        self,
        tmp_path,
        installed,
        locked,
        specs=None,
        mode="portable",
    ):
        """构造指定模式环境，patch 各依赖后调用 detect_dependency_updates。

        Args:
            installed: {pkg: 已装版本}，模拟 importlib.metadata 返回值。
            locked: {pkg: 锁定版本}，模拟 _load_locked_versions 返回值。
            specs: {pkg: spec 串}，默认 mineru ``mineru[core]>=3.4.0``。
            mode: 环境模式（``"portable"`` 默认；``"venv"`` 应短路返回空）。
        """
        import vibeocr.env_manager as em

        python_exe = tmp_path / "python.exe"
        python_exe.touch()  # detect 早期检查 python_exe.exists()

        if specs is None:
            specs = {"mineru": "mineru[core]>=3.4.0"}

        # 重置缓存（避免上一个测试的数据污染）
        em._dep_specs_cache = None
        em._locked_versions_cache = None
        try:
            with (
                patch(
                    "vibeocr.env_manager.get_embedded_python_executable",
                    return_value=python_exe,
                ),
                patch("vibeocr.env_manager._load_dep_specs", return_value=specs),
                patch(
                    "vibeocr.env_manager.get_dependency_versions",
                    return_value=dict(installed),
                ),
                patch(
                    "vibeocr.env_manager._load_locked_versions",
                    return_value=dict(locked),
                ),
                patch(
                    "vibeocr.env_manager.get_environment_mode",
                    return_value=mode,
                ),
            ):
                return em.detect_dependency_updates(tmp_path)
        finally:
            em._dep_specs_cache = None
            em._locked_versions_cache = None

    def test_detect_update_when_locked_newer_than_installed(self, tmp_path):
        """锁定版 3.4.2 vs 已装 3.4.0 → 应报更新（本 Bug 的回归用例）。

        旧逻辑（只比下界 3.4.0）会漏报；修复后用锁定版 3.4.2 比较，正确报更新。
        """
        updates = self._run_detect(
            tmp_path,
            installed={"mineru": "3.4.0"},
            locked={"mineru": "3.4.2"},
        )
        assert "mineru" in updates, (
            "已装 3.4.0 落后于锁定 3.4.2，应报更新；旧逻辑只比下界 3.4.0 会漏报"
        )
        installed_ver, _spec = updates["mineru"]
        assert installed_ver == "3.4.0"

    def test_no_update_when_installed_equals_locked(self, tmp_path):
        """已装 = 锁定版（3.4.2）→ 不报更新。"""
        updates = self._run_detect(
            tmp_path,
            installed={"mineru": "3.4.2"},
            locked={"mineru": "3.4.2"},
        )
        assert "mineru" not in updates, "已装等于锁定版不应报更新"

    def test_no_update_when_installed_newer_than_locked(self, tmp_path):
        """已装 > 锁定版 → 不报更新（用户手动装了更新版，不降级）。"""
        updates = self._run_detect(
            tmp_path,
            installed={"mineru": "3.5.0"},
            locked={"mineru": "3.4.2"},
        )
        assert "mineru" not in updates

    def test_fallback_to_lower_bound_when_no_locked(self, tmp_path):
        """无锁定版字段（旧 version.json）→ 回退下界比较（向后兼容）。

        locked 为空 dict 模拟旧 version.json 无 dep_locked_versions 字段。
        已装 3.4.0 = 下界 3.4.0 → 不报；已装 3.3.0 < 下界 → 报。
        """
        # 已装等于下界 → 不报
        updates = self._run_detect(
            tmp_path,
            installed={"mineru": "3.4.0"},
            locked={},  # 无锁定版，回退下界 3.4.0
        )
        assert "mineru" not in updates, "回退下界时，已装=下界不应报"

        # 已装低于下界 → 报
        updates = self._run_detect(
            tmp_path,
            installed={"mineru": "3.3.0"},
            locked={},
        )
        assert "mineru" in updates, "回退下界时，已装<下界应报"

    def test_local_version_locked_comparison(self, tmp_path):
        """锁定版含 local label（torch 2.12.1+cu126）应正确比较。

        packaging.version.Version 能解析 +cu126；已装 = 锁定 → 不报。
        """
        updates = self._run_detect(
            tmp_path,
            installed={"torch": "2.12.1+cu126"},
            locked={"torch": "2.12.1+cu126"},
            specs={"torch": "torch>=2.6.0"},
        )
        assert "torch" not in updates, "local label 版本相等不应报更新"

    def test_reports_when_locked_absent_but_lower_bound_higher(self, tmp_path):
        """锁定版缺失且下界更高 → 仍用下界报更新。"""
        updates = self._run_detect(
            tmp_path,
            installed={"torch": "2.5.0"},
            locked={},  # 无锁定版
            specs={"torch": "torch>=2.6.0"},
        )
        assert "torch" in updates

    def test_returns_empty_in_venv_mode(self, tmp_path):
        """开发态（.venv）必须短路返回空，不触发依赖更新提示。

        回归 Bug：依赖更新检测本应仅在 portable 模式生效，但旧逻辑只检查
        python_exe.exists()。get_embedded_python_executable 优先返回 .venv 的
        python，开发态该路径存在 → 检测继续跑，对 .venv 内已装版本与 uv.lock
        锁定版比较，误报更新。
        """
        updates = self._run_detect(
            tmp_path,
            installed={"mineru": "3.4.0"},  # 落后于锁定版，portable 下会报更新
            locked={"mineru": "3.4.2"},
            mode="venv",  # 开发态必须短路
        )
        # 在 venv 模式下，即使已装 < 锁定版，也必须返回空（开发态由 uv 管理）
        assert updates == {}, (
            "开发态（.venv）下 detect_dependency_updates 必须短路返回空，"
            "依赖更新仅便携模式生效"
        )

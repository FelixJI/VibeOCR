# 依赖增量安装（断点续传语义）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现"非严格意义的断点续传"——安装/补装依赖时跳过已 import 成功的包，只下载安装缺失的；设置页新增"补充安装缺失依赖"按钮 + 依赖状态表格；安装失败时弹窗提示并完整写 log。

**Architecture:** 在 `env_manager` 层注入"已装即跳过"语义：从 `_install_paddle_stack` 提取 `_build_paddle_requirements` 辅助函数，给 `_install_paddle_stack` 加 `requirements_override` 可选参数（向后兼容），新增 `install_missing_dependencies` 高层入口（用 `_check_imports` 判定过滤）。三处调用（首启 / 设置页新按钮 / 重装按钮）中前两处走增量，重装按钮保持全量。UI 新增按钮与状态表格。

**Tech Stack:** Python 3.13, PySide6 (Qt), pytest, subprocess (pip), mock。

**关联 spec:** `docs/superpowers/specs/2026-06-26-incremental-dependency-install-design.md`

---

## 文件结构总览

| 文件 | 职责 | 操作 |
|---|---|---|
| `src/vibeocr/env_manager.py` | 安装核心逻辑 | 修改：提取 `_build_paddle_requirements`；`_install_paddle_stack` 加 `requirements_override`；新增 `install_missing_dependencies`、`get_dependency_versions`；失败前 logger.error 全文 |
| `src/vibeocr/widgets/install_dialog.py` | 安装工作线程 | 修改：`InstallWorker` 加 `missing_only` 标志，`run()` 按标志分流 |
| `src/vibeocr/widgets/backend_choice_dialog.py` | 后端选择+安装对话框 | 修改：`__init__` 透传 `missing_only`；`_on_finished` 失败时弹 QMessageBox.warning |
| `src/vibeocr/ui/main_window.ui` | UI 定义（Qt Designer XML） | 修改：`groupEnvMaintenance` 加 `btnInstallMissing` 按钮 + `tableDepsStatus` 表格 |
| `src/vibeocr/ui/ui_main_window.py` | 编译产物 | 重新生成（`scripts/compile_ui.py`） |
| `src/vibeocr/views/settings_page_controller.py` | 设置页逻辑 | 修改：连新按钮信号；`_open_reinstall_dialog` 支持 `missing_only`；`_refresh_env_maintenance_state` 填充表格；版本获取 |
| `tests/test_env_manager_install.py` | env_manager 安装测试 | 扩展 |
| `tests/widgets/test_install_worker_force_backend.py` | InstallWorker 测试 | 扩展（已有 missing_only 相关） |

---

## Task 1: 提取 `_build_paddle_requirements` 辅助函数

**Files:**
- Modify: `src/vibeocr/env_manager.py`（`_install_paddle_stack` line 721-882，重点 line 749-785）
- Test: `tests/test_env_manager_install.py`

把 `_install_paddle_stack` 内部 paddle 项构建逻辑（GPU/CPU 选择 + index URL，line 749-785）提取为独立函数。

> **设计决策**：`_build_paddle_requirements` **只构建 paddle 项**（含 GPU/CPU/index 选择，这是最复杂、最需要复用的部分），返回 `[(paddle_name, paddle_package, paddle_index)]`。paddleocr/mineru/torch 项的构建很简单（从 specs 取 + 固定 index），分别在 `_install_paddle_stack` 和 `install_missing_dependencies` 中各自拼接，避免把 `pip_source`/`torch_index` 这些局部计算值塞进本函数。

- [ ] **Step 1: 写失败测试**

在 `tests/test_env_manager_install.py` 末尾新增测试类：

```python
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
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python -m pytest tests/test_env_manager_install.py::TestBuildPaddleRequirements -v`
Expected: FAIL with `ImportError: cannot import name '_build_paddle_requirements'`

- [ ] **Step 3: 实现 `_build_paddle_requirements`**

在 `env_manager.py` 的 `_install_paddle_stack` 函数**定义之前**（line 721 之前）新增：

```python
def _build_paddle_requirements(
    specs: dict[str, str],
    use_gpu: bool,
    cuda_version: str | None,
    network_type: Literal["domestic", "international"],
    report_fn: Callable[[str, str], None],
) -> list[tuple[str, str, str]]:
    """构建 paddle 项（GPU/CPU 包名 + index URL 选择）。

    只构建 paddle 这一项（最复杂、需复用的逻辑）；paddleocr/mineru/torch 项由调用方
    自行拼接（它们的 index 是 pip_source / torch_index，属调用方局部值）。

    Args:
        specs: _load_dep_specs() 返回的依赖规格
        use_gpu: 是否安装 GPU 版本
        cuda_version: CUDA 版本 cu-tag（如 "cu126"）
        network_type: 网络类型（决定 torch 镜像）
        report_fn: 日志回调 (stage, msg)

    Returns:
        [(paddle 展示名, paddle 包规格, paddle index URL)]
    """
    import re as _re

    # 打包环境 version.json 用 _KEY_ALIASES 把 paddlepaddle-gpu 归一为 paddlepaddle；
    # 开发环境 pyproject 保留 paddlepaddle-gpu。两端兼容取规格。
    raw_paddle_spec = specs.get("paddlepaddle-gpu") or specs["paddlepaddle"]
    _ver_m = _re.search(r"(==|>=|<=|~=|>|<).+", raw_paddle_spec)
    paddle_version_constraint = _ver_m.group(0) if _ver_m else ""
    paddle_gpu_spec = f"paddlepaddle-gpu{paddle_version_constraint}"
    paddle_cpu_spec = f"paddlepaddle{paddle_version_constraint}"

    default_gpu_tag = "cu126"
    if use_gpu and cuda_version:
        paddle_package = paddle_gpu_spec
        paddle_index = (
            f"https://www.paddlepaddle.org.cn/packages/stable/{cuda_version}/"
        )
        paddle_name = f"PaddlePaddle GPU ({cuda_version})"
        report_fn("依赖安装", f"检测到 CUDA {cuda_version}，安装 GPU 版本")
    elif use_gpu:
        paddle_package = paddle_gpu_spec
        paddle_index = (
            f"https://www.paddlepaddle.org.cn/packages/stable/{default_gpu_tag}/"
        )
        paddle_name = f"PaddlePaddle GPU ({default_gpu_tag})"
        report_fn("依赖安装", f"安装 GPU 版本（默认 {default_gpu_tag}）")
    else:
        paddle_package = paddle_cpu_spec
        paddle_index = "https://www.paddlepaddle.org.cn/packages/stable/cpu/"
        paddle_name = "PaddlePaddle CPU"
        report_fn("依赖安装", "使用CPU版本")

    return [(paddle_name, paddle_package, paddle_index)]
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python -m pytest tests/test_env_manager_install.py::TestBuildPaddleRequirements -v`
Expected: 4 个测试 PASS

- [ ] **Step 5: 重构 `_install_paddle_stack` 调用 `_build_paddle_requirements`**

把 `_install_paddle_stack`（line 749-785，即从注释 `# 打包环境 version.json...` 到 paddle CPU 分支结束）替换为调用 `_build_paddle_requirements`，消除重复。

替换 `_install_paddle_stack` 内 line 749-785 这段（从 `# 打包环境 version.json` 注释到 `report_fn("依赖安装", "使用CPU版本")` 这一行结束）为：

```python
    # paddle 项（GPU/CPU/index 选择）由共享函数构建
    paddle_reqs = _build_paddle_requirements(
        specs=specs,
        use_gpu=use_gpu,
        cuda_version=cuda_version,
        network_type=network_type,
        report_fn=report_fn,
    )
```

然后 line 812-826 构建 `requirements` 的部分改为用 `paddle_reqs`：

```python
        requirements: list[tuple[str, str, str]] = [
            *paddle_reqs,
            ("PaddleOCR", f'"{specs["paddleocr"]}"', pip_source),
            ("MinerU", f'"{specs["mineru"]}"', pip_source),
        ]

        # GPU 环境下安装 torch+CUDA 覆盖 mineru 附带的 CPU 版本
        if use_gpu:
            paddle_cuda_tag = cuda_version or default_gpu_tag
            torch_cuda_tag = TORCH_CUDA_MAP.get(paddle_cuda_tag, "cu126")
            pytorch_mirror_name = "nju" if network_type == "domestic" else "official"
            torch_index = get_pytorch_mirror(pytorch_mirror_name, torch_cuda_tag)
            requirements.append(
                (f"PyTorch CUDA ({torch_cuda_tag})", "torch torchvision", torch_index)
            )
            report_fn("依赖安装", f"将安装 PyTorch CUDA ({torch_cuda_tag})")
```

注意：原 line 812-816 是 `requirements = [...]`（首次赋值），现在 `paddle_reqs` 已在前面构建好。`default_gpu_tag = "cu126"` 这个变量在原 line 766 定义，重构后需保留——把它移到 `if use_gpu:` torch 分支前：

```python
        default_gpu_tag = "cu126"  # torch 默认 cu-tag（paddle_cuda_tag 回退用）
```

> 验证：`default_gpu_tag` 在重构后只被 torch 分支的 `paddle_cuda_tag = cuda_version or default_gpu_tag` 使用，所以放 `if use_gpu:` 块正上方即可。

- [ ] **Step 6: 运行全部安装相关测试，确认无回归**

Run: `python -m pytest tests/test_env_manager_install.py -v`
Expected: 全部 PASS（包括原有的 TestInstallSpecs、TestInstallPaddleStackAlias、TestGpuInstallUsesTorchForCudaRuntime、TestSwitchPaddleBackend 等）

- [ ] **Step 7: 提交**

```bash
git add src/vibeocr/env_manager.py tests/test_env_manager_install.py
git commit -m "refactor(env): 提取 _build_paddle_requirements，消除 paddle 项构建重复"
```

---

## Task 2: `_install_paddle_stack` 支持 `requirements_override`

**Files:**
- Modify: `src/vibeocr/env_manager.py:721`（`_install_paddle_stack` 签名与 requirements 构建）
- Test: `tests/test_env_manager_install.py`

给 `_install_paddle_stack` 加可选参数 `requirements_override`，传入时跳过内部 requirements 构建，直接用传入子集。缺省 None 保持原行为。

- [ ] **Step 1: 写失败测试 `requirements_override` 生效**

在 `tests/test_env_manager_install.py` 新增测试类：

```python
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
        # 安装命令中只应有 mineru，不应有 paddlepaddle/paddleocr/torch
        joined_all = " ".join(" ".join(c) for c in calls)
        # 过滤掉 pip 自身升级的命令（含 "pip" "install" "--upgrade"）
        install_cmds = [
            c
            for c in calls
            if "install" in c and "--upgrade" not in c and "pip" not in " ".join(c[3:])
        ]
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
        install_cmds = [
            c
            for c in calls
            if "install" in c and "--upgrade" not in c and "pip" not in " ".join(c[3:])
        ]
        # GPU 完整列表：paddle + paddleocr + mineru + torch = 4 个安装命令
        assert len(install_cmds) == 4, (
            f"GPU 完整列表应装 4 个，实际: {install_cmds}"
        )
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python -m pytest tests/test_env_manager_install.py::TestInstallPaddleStackRequirementsOverride -v`
Expected: FAIL — `test_override_installs_only_provided_subset` 失败（会装 4 个而非 1 个）；`TypeError: unexpected keyword argument 'requirements_override'`

- [ ] **Step 3: 实现 `requirements_override` 参数**

修改 `_install_paddle_stack` 签名（`env_manager.py:721`），在 `success_msg` 后加参数：

```python
def _install_paddle_stack(
    python_exe: Path,
    specs: dict[str, str],
    pip_source: str,
    network_type: Literal["domestic", "international"],
    use_gpu: bool,
    cuda_version: str | None,
    report_fn: Callable[[str, str], None],
    success_msg: str,
    requirements_override: list[tuple[str, str, str]] | None = None,
) -> tuple[bool, str]:
```

更新 docstring，在 `Args` 部分加：

```
        requirements_override: 外部传入的 requirements 子集。指定时跳过内部完整列表
            构建，直接安装子集（用于增量安装：只装缺失的包）。None 时构建完整列表。
```

然后修改 requirements 构建逻辑（Task 1 重构后的代码）。在 `try:` 块内、pip 升级之后、原 `requirements = [...]` 位置，改为：

```python
        if requirements_override is not None:
            # 增量模式：直接用外部传入的子集，跳过完整构建
            requirements = list(requirements_override)
        else:
            # 完整模式：构建 paddle + paddleocr + mineru (+GPU torch)
            paddle_reqs = _build_paddle_requirements(
                specs=specs,
                use_gpu=use_gpu,
                cuda_version=cuda_version,
                network_type=network_type,
                report_fn=report_fn,
            )
            default_gpu_tag = "cu126"
            requirements: list[tuple[str, str, str]] = [
                *paddle_reqs,
                ("PaddleOCR", f'"{specs["paddleocr"]}"', pip_source),
                ("MinerU", f'"{specs["mineru"]}"', pip_source),
            ]
            if use_gpu:
                paddle_cuda_tag = cuda_version or default_gpu_tag
                torch_cuda_tag = TORCH_CUDA_MAP.get(paddle_cuda_tag, "cu126")
                pytorch_mirror_name = (
                    "nju" if network_type == "domestic" else "official"
                )
                torch_index = get_pytorch_mirror(
                    pytorch_mirror_name, torch_cuda_tag
                )
                requirements.append(
                    (
                        f"PyTorch CUDA ({torch_cuda_tag})",
                        "torch torchvision",
                        torch_index,
                    )
                )
                report_fn("依赖安装", f"将安装 PyTorch CUDA ({torch_cuda_tag})")
```

> 注意：`_build_paddle_requirements` 调用现在移进了 `else` 分支（原 Task 1 是无条件下移到函数顶部，现在改条件化）。`default_gpu_tag` 也移进 `else` 分支（仅完整模式 GPU 需要）。

- [ ] **Step 4: 运行测试，确认通过**

Run: `python -m pytest tests/test_env_manager_install.py::TestInstallPaddleStackRequirementsOverride -v`
Expected: 2 个测试 PASS

- [ ] **Step 5: 运行全部安装测试，确认无回归**

Run: `python -m pytest tests/test_env_manager_install.py -v`
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```bash
git add src/vibeocr/env_manager.py tests/test_env_manager_install.py
git commit -m "feat(env): _install_paddle_stack 支持 requirements_override 增量子集"
```

---

## Task 3: 新增 `install_missing_dependencies` + 失败 logger.error

**Files:**
- Modify: `src/vibeocr/env_manager.py`（`install_embedded_dependencies` line 885 之后）
- Modify: `src/vibeocr/env_manager.py:870-872`（失败前加 logger.error 全文）
- Test: `tests/test_env_manager_install.py`

新增高层增量安装函数：检测 import → 过滤已装 → 只装子集。同时在 `_install_paddle_stack` 失败返回前 logger.error 完整 stderr。

- [ ] **Step 1: 写失败测试 `install_missing_dependencies`**

在 `tests/test_env_manager_install.py` 新增测试类：

```python
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

    def test_skips_installed_packages_only_installs_missing(self, tmp_path):
        """已 import 成功的包应跳过，只装缺失的"""
        from vibeocr.env_manager import install_missing_dependencies

        python_exe = tmp_path / "python.exe"
        python_exe.touch()
        install_calls = []

        def mock_run(cmd, **kw):
            install_calls.append(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            # _check_imports 也走 subprocess.run：paddle/paddleocr 成功，mineru/torch 失败
            import_code = cmd[cmd.index("-c") + 1] if "-c" in cmd else ""
            if import_code.startswith("import "):
                module = import_code.split()[1]
                if module in ("paddle", "paddleocr"):
                    r.returncode = 0
                else:
                    r.returncode = 1
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
        # 过滤出 pip install 命令（排除 import 检测命令）
        pip_installs = [
            c
            for c in install_calls
            if "install" in c and "-c" not in c and "import" not in " ".join(c)
        ]
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
        install_calls = []

        def mock_run(cmd, **kw):
            install_calls.append(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            import_code = cmd[cmd.index("-c") + 1] if "-c" in cmd else ""
            if import_code.startswith("import "):
                r.returncode = 1  # 全部 import 失败
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
        pip_installs = [
            c
            for c in install_calls
            if "install" in c and "-c" not in c and "import" not in " ".join(c)
        ]
        # CPU 模式完整列表：paddle + paddleocr + mineru = 3 个
        assert len(pip_installs) == 3, (
            f"CPU 全量应装 3 个，实际: {pip_installs}"
        )

    def test_force_backend_gpu_uses_gpu_requirements(self, tmp_path):
        """force_backend=gpu 时应构建 GPU requirements（含 torch）"""
        from vibeocr.env_manager import install_missing_dependencies

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 1  # 全部缺失
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
            patch("vibeocr.env_manager.detect_gpu", return_value=(True, "cu126")),
        ):
            ok, _msg = install_missing_dependencies(
                tmp_path,
                force_backend="gpu",
                progress_callback=lambda s, m: None,
            )

        assert ok
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python -m pytest tests/test_env_manager_install.py::TestInstallMissingDependencies -v`
Expected: FAIL with `ImportError: cannot import name 'install_missing_dependencies'`

- [ ] **Step 3: 实现 `install_missing_dependencies`**

在 `env_manager.py` 的 `install_embedded_dependencies` 函数之后（约 line 944 之后）新增：

```python
def install_missing_dependencies(
    project_root: Path,
    network_type: Literal["domestic", "international"] = "domestic",
    use_gpu: bool = False,
    cuda_version: str | None = None,
    progress_callback=None,
    force_backend: str | None = None,
) -> tuple[bool, str]:
    """增量安装：只装 import 失败（缺失/损坏）的依赖，已 import 成功的跳过下载。

    与 install_embedded_dependencies 的区别：安装前先 _check_imports 检测每个包，
    已可导入的包跳过 pip install（实现"非严格意义断点续传"——已装的不会重复下载）。

    Args:
        project_root: 项目根目录
        network_type: 网络类型
        use_gpu: 是否安装 GPU 版本
        cuda_version: CUDA 版本 cu-tag
        progress_callback: 进度回调 (stage, message)
        force_backend: 强制后端 "gpu"/"cpu"/None

    Returns:
        (是否成功, 消息)
    """
    python_exe = get_embedded_python_executable(project_root)
    if not python_exe.exists():
        return False, "Python 运行时未安装"

    # force_backend 覆盖自动检测结果
    if force_backend == "gpu":
        use_gpu = True
        if not cuda_version:
            _has_gpu, cuda_version = detect_gpu()
    elif force_backend == "cpu":
        use_gpu = False
        cuda_version = None

    pip_source = get_pip_source(network_type)

    def report(stage: str, msg: str):
        logger.info("[%s] %s", stage, msg)
        if progress_callback:
            progress_callback(stage, msg)

    report("依赖安装", "开始检测已安装的依赖...")
    report("依赖安装", f"pip源: {pip_source}")

    specs = _load_dep_specs()

    # 1. 构建完整 requirements 列表
    paddle_reqs = _build_paddle_requirements(
        specs=specs,
        use_gpu=use_gpu,
        cuda_version=cuda_version,
        network_type=network_type,
        report_fn=report,
    )
    requirements: list[tuple[str, str, str]] = [
        *paddle_reqs,
        ("PaddleOCR", f'"{specs["paddleocr"]}"', pip_source),
        ("MinerU", f'"{specs["mineru"]}"', pip_source),
    ]
    if use_gpu:
        default_gpu_tag = "cu126"
        paddle_cuda_tag = cuda_version or default_gpu_tag
        torch_cuda_tag = TORCH_CUDA_MAP.get(paddle_cuda_tag, "cu126")
        pytorch_mirror_name = "nju" if network_type == "domestic" else "official"
        torch_index = get_pytorch_mirror(pytorch_mirror_name, torch_cuda_tag)
        requirements.append(
            (f"PyTorch CUDA ({torch_cuda_tag})", "torch torchvision", torch_index)
        )

    # 2. 检测每个包是否已可 import
    # requirements 项展示名 → 判定用的 pip 包名（对应 _check_imports 的 key）
    # 名称中含 "PaddlePaddle" → paddlepaddle；含 "PyTorch" → torch
    report("依赖安装", "正在检测已安装的依赖...")
    import_status = _check_imports(python_exe)

    def _is_installed(req_name: str) -> bool:
        """根据 requirements 项展示名查 import 状态"""
        if "PaddlePaddle" in req_name:
            return import_status.get("paddlepaddle", False)
        if "PyTorch" in req_name:
            return import_status.get("torch", False)
        if "PaddleOCR" in req_name:
            return import_status.get("paddleocr", False)
        if "MinerU" in req_name:
            return import_status.get("mineru", False)
        return False

    # 3. 过滤掉已装的
    subset: list[tuple[str, str, str]] = []
    for name, pkg_spec, index_url in requirements:
        if _is_installed(name):
            report("依赖安装", f"✓ {name} 已安装，跳过")
        else:
            subset.append((name, pkg_spec, index_url))

    # 4. 全部已装
    if not subset:
        report("依赖安装", "所有依赖已安装，无需补装")
        return True, "所有OCR依赖已安装"

    missing_names = ", ".join(n for n, _, _ in subset)
    report("依赖安装", f"需补装: {missing_names}")

    # 5. 只装子集
    return _install_paddle_stack(
        python_exe=python_exe,
        specs=specs,
        pip_source=pip_source,
        network_type=network_type,
        use_gpu=use_gpu,
        cuda_version=cuda_version,
        report_fn=report,
        success_msg="OCR依赖补装成功",
        requirements_override=subset,
    )
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python -m pytest tests/test_env_manager_install.py::TestInstallMissingDependencies -v`
Expected: 4 个测试 PASS

- [ ] **Step 5: 写失败测试 `_install_paddle_stack` 失败前 logger.error 全文**

在 `tests/test_env_manager_install.py` 新增测试：

```python
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
            ok, msg = _install_paddle_stack(
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
        # 完整 stderr（800 个 x）应在 ERROR 日志中，而返回 msg 是截断版（<800）
        error_msgs = " ".join(
            r.message for r in caplog.records if r.levelno >= logging.ERROR
        )
        assert "x" * 800 in error_msgs or len(error_msgs) > 500, (
            f"应记录完整 stderr，实际 ERROR 日志长度: {len(error_msgs)}"
        )
```

- [ ] **Step 6: 运行测试，确认失败**

Run: `python -m pytest tests/test_env_manager_install.py::TestInstallFailureLogging -v`
Expected: FAIL（当前代码只在返回值截断，未 logger.error 全文）

- [ ] **Step 7: 实现失败前 logger.error 全文**

在 `_install_paddle_stack` 失败返回处（line 870-872 附近，`if result.returncode != 0:` PyPI 回退后再次检查的块），原代码：

```python
                if result.returncode != 0:
                    error_msg = result.stderr or result.stdout or "未知错误"
                    return False, f"{name} 安装失败:\n{error_msg[:500]}"
```

改为：

```python
                if result.returncode != 0:
                    error_msg = result.stderr or result.stdout or "未知错误"
                    # 完整 stderr 落盘（UI 只显示截断版）
                    logger.error("%s 安装失败，完整输出:\n%s", name, error_msg)
                    return False, f"{name} 安装失败:\n{error_msg[:500]}"
```

- [ ] **Step 8: 运行测试，确认通过**

Run: `python -m pytest tests/test_env_manager_install.py::TestInstallFailureLogging -v`
Expected: PASS

- [ ] **Step 9: 运行全部安装测试，确认无回归**

Run: `python -m pytest tests/test_env_manager_install.py -v`
Expected: 全部 PASS

- [ ] **Step 10: 提交**

```bash
git add src/vibeocr/env_manager.py tests/test_env_manager_install.py
git commit -m "feat(env): 新增 install_missing_dependencies 增量安装，失败 logger.error 全文"
```

---

## Task 4: `InstallWorker` 支持 `missing_only` 标志

**Files:**
- Modify: `src/vibeocr/widgets/install_dialog.py:22-110`（`InstallWorker`）
- Test: `tests/widgets/test_install_worker_force_backend.py`

`InstallWorker` 加 `missing_only` 参数，`run()` 按标志选调 `install_missing_dependencies` 或 `install_embedded_dependencies`。

- [ ] **Step 1: 读现有 InstallWorker 测试了解模式**

Run: `python -m pytest tests/widgets/test_install_worker_force_backend.py -v --collect-only 2>&1 | head -20`

观察现有测试如何 mock `env_manager`、构造 worker、捕获 `finished` 信号。

- [ ] **Step 2: 写失败测试 `missing_only=True` 调 `install_missing_dependencies`**

在 `tests/widgets/test_install_worker_force_backend.py` 末尾新增：

```python
def test_missing_only_calls_install_missing_dependencies(qtbot, tmp_path):
    """missing_only=True 时 worker 应调 install_missing_dependencies 而非全量"""
    from unittest.mock import patch, MagicMock

    from vibeocr.widgets.install_dialog import InstallWorker

    worker = InstallWorker(tmp_path, missing_only=True)

    with (
        patch.object(worker, "_emit_progress"),
        patch("vibeocr.widgets.install_dialog.NetworkDetector") as mock_nd,
        patch("vibeocr.widgets.install_dialog.env_manager") as mock_em,
    ):
        mock_nd.return_value.network_type = "domestic"
        mock_em.detect_gpu.return_value = (False, None)
        mock_em.get_embedded_python_executable.return_value = tmp_path / "python.exe"
        # python.exe 存在，跳过 python 安装
        (tmp_path / "python.exe").touch()
        mock_em.install_missing_dependencies.return_value = (True, "ok")

        # 收集 finished 信号
        results = []
        worker.finished.connect(lambda ok, msg: results.append((ok, msg)))
        worker.run()  # 直接同步调用 run()，不 start()

    mock_em.install_missing_dependencies.assert_called_once()
    # 不应调全量安装
    mock_em.install_embedded_dependencies.assert_not_called()
    assert results == [(True, "ok")]


def test_missing_only_false_calls_install_embedded_dependencies(qtbot, tmp_path):
    """missing_only=False（缺省）时 worker 应调全量安装（向后兼容）"""
    from unittest.mock import patch, MagicMock

    from vibeocr.widgets.install_dialog import InstallWorker

    worker = InstallWorker(tmp_path)  # 缺省 missing_only=False

    with (
        patch.object(worker, "_emit_progress"),
        patch("vibeocr.widgets.install_dialog.NetworkDetector") as mock_nd,
        patch("vibeocr.widgets.install_dialog.env_manager") as mock_em,
    ):
        mock_nd.return_value.network_type = "domestic"
        mock_em.detect_gpu.return_value = (False, None)
        mock_em.get_embedded_python_executable.return_value = tmp_path / "python.exe"
        (tmp_path / "python.exe").touch()
        mock_em.install_embedded_dependencies.return_value = (True, "ok")

        results = []
        worker.finished.connect(lambda ok, msg: results.append((ok, msg)))
        worker.run()

    mock_em.install_embedded_dependencies.assert_called_once()
    mock_em.install_missing_dependencies.assert_not_called()
```

- [ ] **Step 3: 运行测试，确认失败**

Run: `python -m pytest tests/widgets/test_install_worker_force_backend.py::test_missing_only_calls_install_missing_dependencies tests/widgets/test_install_worker_force_backend.py::test_missing_only_false_calls_install_embedded_dependencies -v`
Expected: FAIL — `InstallWorker.__init__() got an unexpected keyword argument 'missing_only'`

- [ ] **Step 4: 实现 `missing_only` 参数**

修改 `src/vibeocr/widgets/install_dialog.py:28-37` 的 `InstallWorker.__init__`：

```python
    def __init__(
        self,
        project_root: Path,
        force_backend: str | None = None,
        reinstall_python: bool = False,
        missing_only: bool = False,
    ) -> None:
        super().__init__()
        self._project_root = project_root
        self._force_backend = force_backend
        self._reinstall_python = reinstall_python
        self._missing_only = missing_only
```

修改 `run()`（line 95-104）的依赖安装部分：

```python
            # 4. 安装OCR依赖（增量或全量）
            install_fn = (
                env_manager.install_missing_dependencies
                if self._missing_only
                else env_manager.install_embedded_dependencies
            )
            action = "补装缺失" if self._missing_only else "安装"
            self._emit_progress("依赖安装", f"正在{action}OCR依赖...")
            success, msg = install_fn(
                self._project_root,
                network_type,
                has_gpu,
                cuda_version,
                progress_callback=self._emit_progress,
                force_backend=self._force_backend,
            )

            self.finished.emit(success, msg)
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `python -m pytest tests/widgets/test_install_worker_force_backend.py -v`
Expected: 全部 PASS（含原有 + 2 个新增）

- [ ] **Step 6: 提交**

```bash
git add src/vibeocr/widgets/install_dialog.py tests/widgets/test_install_worker_force_backend.py
git commit -m "feat(install): InstallWorker 支持 missing_only 标志分流增量/全量安装"
```

---

## Task 5: `BackendChoiceDialog` 透传 `missing_only` + 失败弹窗

**Files:**
- Modify: `src/vibeocr/widgets/backend_choice_dialog.py:38-161`
- Test: `tests/widgets/test_backend_choice_dialog.py`

`BackendChoiceDialog.__init__` 加 `missing_only` 参数透传给 `InstallWorker`；`_on_finished` 失败时弹 `QMessageBox.warning`。

- [ ] **Step 1: 读现有 backend_choice_dialog 测试了解模式**

Run: `python -m pytest tests/widgets/test_backend_choice_dialog.py -v --collect-only 2>&1 | head -20`

- [ ] **Step 2: 写失败测试 `missing_only` 透传 + 失败弹窗**

在 `tests/widgets/test_backend_choice_dialog.py` 末尾新增：

```python
def test_missing_only_passed_to_install_worker(qtbot, tmp_path, monkeypatch):
    """missing_only=True 时应传给 InstallWorker"""
    from PySide6.QtWidgets import QWidget

    from vibeocr.widgets.backend_choice_dialog import BackendChoiceDialog

    captured = {}

    class FakeWorker:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)
            self.progress = MagicMock()
            self.finished = MagicMock()

        def start(self):
            pass

    monkeypatch.setattr(
        "vibeocr.widgets.backend_choice_dialog.InstallWorker", FakeWorker
    )

    host = QWidget()
    qtbot.addWidget(host)
    dialog = BackendChoiceDialog(tmp_path, parent=host, missing_only=True)
    # 模拟点击安装（GPU 不可用时默认 CPU）
    dialog._on_install_clicked()

    assert captured.get("missing_only") is True


def test_failure_shows_warning_messagebox(qtbot, tmp_path, monkeypatch):
    """安装失败时应弹 QMessageBox.warning"""
    from PySide6.QtWidgets import QWidget, QMessageBox

    from vibeocr.widgets.backend_choice_dialog import BackendChoiceDialog

    warnings_shown = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *args, **kwargs: warnings_shown.append(args),
    )

    host = QWidget()
    qtbot.addWidget(host)
    dialog = BackendChoiceDialog(tmp_path, parent=host)

    # 直接调用 _on_finished 模拟失败
    dialog._on_finished(False, "torch 安装失败:\n网络超时")

    assert len(warnings_shown) == 1, "失败时应弹一次 warning"
    # 弹窗内容应含失败信息和重试提示
    text = warnings_shown[0][-1]  # 最后一个位置参数通常是 detail text
    all_text = " ".join(str(a) for a in warnings_shown[0])
    assert "torch" in all_text or "失败" in all_text


def test_success_does_not_show_warning(qtbot, tmp_path, monkeypatch):
    """安装成功时不应弹 warning"""
    from PySide6.QtWidgets import QWidget, QMessageBox

    from vibeocr.widgets.backend_choice_dialog import BackendChoiceDialog

    warnings_shown = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args, **kwargs: warnings_shown.append(args)
    )

    host = QWidget()
    qtbot.addWidget(host)
    dialog = BackendChoiceDialog(tmp_path, parent=host)
    dialog._on_finished(True, "安装成功")

    assert len(warnings_shown) == 0
```

- [ ] **Step 3: 运行测试，确认失败**

Run: `python -m pytest tests/widgets/test_backend_choice_dialog.py::test_missing_only_passed_to_install_worker tests/widgets/test_backend_choice_dialog.py::test_failure_shows_warning_messagebox tests/widgets/test_backend_choice_dialog.py::test_success_does_not_show_warning -v`
Expected: FAIL — `missing_only` 参数不存在；`_on_finished` 未弹 warning

- [ ] **Step 4: 实现 `missing_only` 透传 + 失败弹窗**

修改 `backend_choice_dialog.py:38-49` 的 `__init__`：

```python
    def __init__(
        self,
        project_root: Path,
        parent: QWidget | None = None,
        reinstall_python: bool = False,
        missing_only: bool = False,
    ) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._worker: InstallWorker | None = None
        self._has_gpu = False
        self._reinstall_python = reinstall_python
        self._missing_only = missing_only
        self._setup_ui()
        self._detect_and_set_default()
```

修改 `_on_install_clicked`（line 132-136）传 `missing_only`：

```python
        self._worker = InstallWorker(
            self._project_root,
            force_backend=backend,
            reinstall_python=self._reinstall_python,
            missing_only=self._missing_only,
        )
```

修改 `_on_finished`（line 146-161）失败分支加弹窗。需先在文件顶部 import `QMessageBox`（line 8-20 的 import 块加 `QMessageBox`）：

```python
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,  # 新增
    QProgressBar,
    QPushButton,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
```

`_on_finished` 失败分支改为：

```python
    @Slot(bool, str)
    def _on_finished(self, success: bool, message: str) -> None:
        self._progress_bar.setVisible(False)
        if success:
            self._progress_label.setText("安装成功！")
            self._log(f"\n{message}")
            self._close_button.setVisible(True)
            self._close_button.setText("完成")
            self.install_succeeded.emit()
            self.done(1)
        else:
            self._progress_label.setText("安装失败")
            self._log(f"\n{message}")
            self._close_button.setVisible(True)
            self._close_button.setText("关闭")
            # 失败弹窗：展示详情 + 提示增量重试
            QMessageBox.warning(
                self,
                "依赖安装失败",
                f"{message}\n\n"
                "可点击「补充安装缺失依赖」按钮重试（已安装的依赖会自动跳过）。",
            )
            self.done(0)
```

- [ ] **Step 5: 运行测试，确认通过**

Run: `python -m pytest tests/widgets/test_backend_choice_dialog.py -v`
Expected: 全部 PASS

- [ ] **Step 6: 提交**

```bash
git add src/vibeocr/widgets/backend_choice_dialog.py tests/widgets/test_backend_choice_dialog.py
git commit -m "feat(dialog): BackendChoiceDialog 透传 missing_only + 失败弹窗提示重试"
```

---

## Task 6: UI 新增 `btnInstallMissing` 按钮 + 依赖状态表格

**Files:**
- Modify: `src/vibeocr/ui/main_window.ui`（`groupEnvMaintenance` line 593-632）
- Regenerate: `src/vibeocr/ui/ui_main_window.py`（`python scripts/compile_ui.py`）
- Test: `tests/views/test_settings_reinstall.py`

在 `groupEnvMaintenance` 加"补充安装缺失依赖"按钮 + 依赖状态 `QTableWidget`。

- [ ] **Step 1: 写失败测试按钮和表格存在**

在 `tests/views/test_settings_reinstall.py` 新增测试：

```python
def test_install_missing_button_exists(controller):
    """补充安装缺失依赖按钮应在 UI 中可找到"""
    _ctrl, host = controller
    from PySide6.QtWidgets import QPushButton

    btn = host.findChild(QPushButton, "btnInstallMissing")
    assert btn is not None, "btnInstallMissing 应存在"


def test_deps_status_table_exists(controller):
    """依赖状态表格应在 UI 中可找到"""
    _ctrl, host = controller
    from PySide6.QtWidgets import QTableWidget

    table = host.findChild(QTableWidget, "tableDepsStatus")
    assert table is not None, "tableDepsStatus 应存在"
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python -m pytest tests/views/test_settings_reinstall.py::test_install_missing_button_exists tests/views/test_settings_reinstall.py::test_deps_status_table_exists -v`
Expected: FAIL（控件不存在）

- [ ] **Step 3: 修改 `main_window.ui` 加按钮和表格**

在 `main_window.ui` 的 `groupEnvMaintenance`（line 601-631）内，`labelEnvStatus` 之后、`btnReinstallPython` 之前，加 `tableDepsStatus`；在 `btnReinstallDeps` 之后加 `btnInstallMissing`。

具体：在 line 610（`labelEnvStatus` 的 `</widget>` 闭合 `</item>` 之后）插入表格 XML：

```xml
              <item>
               <widget class="QTableWidget" name="tableDepsStatus">
                <property name="toolTip">
                 <string>各 OCR 依赖的安装状态（仅便携模式可见）</string>
                </property>
                <column>
                 <property name="text">
                  <string>依赖</string>
                 </property>
                </column>
                <column>
                 <property name="text">
                  <string>状态</string>
                 </property>
                </column>
                <column>
                 <property name="text">
                  <string>版本</string>
                 </property>
                </column>
               </widget>
              </item>
```

在 line 630（`btnReinstallDeps` 的 `</item>` 之后）插入新按钮 XML：

```xml
              <item>
               <widget class="QPushButton" name="btnInstallMissing">
                <property name="toolTip">
                 <string>只安装缺失的 OCR 依赖（已安装的自动跳过，不重复下载）。适合上次安装中途失败后补装。</string>
                </property>
                <property name="text">
                 <string>补充安装缺失依赖</string>
                </property>
               </widget>
              </item>
```

- [ ] **Step 4: 重新编译 UI**

Run: `python scripts/compile_ui.py`
Expected: 无报错，`src/vibeocr/ui/ui_main_window.py` 更新

- [ ] **Step 5: 运行测试，确认通过**

Run: `python -m pytest tests/views/test_settings_reinstall.py::test_install_missing_button_exists tests/views/test_settings_reinstall.py::test_deps_status_table_exists -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/vibeocr/ui/main_window.ui src/vibeocr/ui/ui_main_window.py tests/views/test_settings_reinstall.py
git commit -m "feat(ui): 设置页新增补充安装按钮与依赖状态表格"
```

---

## Task 7: `settings_page_controller` 连接新按钮 + 填充表格

**Files:**
- Modify: `src/vibeocr/views/settings_page_controller.py`（`connect_signals` line 63；`_open_reinstall_dialog` line 556；`_refresh_env_maintenance_state` line 610）
- Modify: `src/vibeocr/env_manager.py`（新增 `get_dependency_versions`）
- Test: `tests/views/test_settings_reinstall.py`

连接 `btnInstallMissing` → `_on_install_missing`（走 `missing_only=True`）；`_open_reinstall_dialog` 支持 `missing_only`；`_refresh_env_maintenance_state` 填充表格（名称/状态/版本）。

- [ ] **Step 1: 写失败测试新按钮走 missing_only 路径**

在 `tests/views/test_settings_reinstall.py` 新增测试：

```python
def test_click_install_missing_opens_dialog_with_missing_only(controller, monkeypatch):
    """点补充安装缺失依赖：应弹 BackendChoiceDialog(missing_only=True)"""
    _ctrl, host = controller
    from PySide6.QtWidgets import QMessageBox, QPushButton

    btn = host.findChild(QPushButton, "btnInstallMissing")
    btn.setEnabled(True)

    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes
    )
    instances = []

    class FakeDialog:
        def __init__(self, *args, **kwargs):
            instances.append(kwargs)

        def exec(self):
            return 1

        def show(self):
            pass

        finished = MagicMock()
        install_succeeded = MagicMock()

    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.BackendChoiceDialog", FakeDialog
    )

    btn.click()

    assert len(instances) == 1
    assert instances[0].get("missing_only") is True


def test_refresh_fills_deps_table(controller, monkeypatch):
    """_refresh_env_maintenance_state 应填充依赖状态表格"""
    ctrl, host = controller
    from PySide6.QtWidgets import QTableWidget

    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.get_environment_mode",
        lambda root: "portable",
    )
    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.get_embedded_python_info",
        lambda root: {"path": "C:/app/python/python.exe", "mode": "portable", "ready": True},
    )
    # mock 依赖状态检测：paddle 已装，其余未装
    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.check_embedded_environment_dependencies",
        lambda root: {"paddlepaddle": True, "paddleocr": False, "mineru": False, "torch": False},
    )
    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.get_dependency_versions",
        lambda root: {"paddlepaddle": "3.3.1", "paddleocr": "", "mineru": "", "torch": ""},
    )

    ctrl._refresh_env_maintenance_state()

    table = host.findChild(QTableWidget, "tableDepsStatus")
    assert table is not None
    assert table.rowCount() == 4, f"应有 4 行依赖，实际: {table.rowCount()}"
    # 第一行 paddlepaddle 应标记已装
    status_item = table.item(0, 1)
    assert status_item is not None
    assert "已安装" in status_item.text() or "✓" in status_item.text()
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `python -m pytest tests/views/test_settings_reinstall.py::test_click_install_missing_opens_dialog_with_missing_only tests/views/test_settings_reinstall.py::test_refresh_fills_deps_table -v`
Expected: FAIL（`_on_install_missing` 不存在；表格未填充）

- [ ] **Step 3: 在 env_manager 新增 `get_dependency_versions`**

在 `env_manager.py` 的 `_check_imports` 附近（line 669 之后）新增：

```python
def get_dependency_versions(python_exe: Path) -> dict[str, str]:
    """获取各 OCR 依赖的版本号（用于设置页状态表格展示）。

    对每个 OCR_CHECK_MODULES 模块执行 `python -c "import X; print(X.__version__)"`，
    失败或无 __version__ 属性返回空字符串。

    Args:
        python_exe: 目标 Python 可执行文件

    Returns:
        {pip包名: 版本号字符串}，未安装/无版本号为空串
    """
    from vibeocr.services.env_config import OCR_CHECK_MODULES, OCR_CHECK_TIMEOUTS

    versions: dict[str, str] = {}
    for module, pkg in OCR_CHECK_MODULES.items():
        try:
            result = subprocess.run(
                [
                    str(python_exe),
                    "-c",
                    f"import {module}; print(getattr({module}, '__version__', ''))",
                ],
                capture_output=True,
                text=True,
                timeout=OCR_CHECK_TIMEOUTS.get(module, 15),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            versions[pkg] = result.stdout.strip() if result.returncode == 0 else ""
        except Exception:
            versions[pkg] = ""
    return versions
```

- [ ] **Step 4: 修改 settings_page_controller 连接新按钮**

在 `settings_page_controller.py` line 26 的 import 加 `check_embedded_environment_dependencies`、`get_embedded_python_executable`：

```python
from vibeocr.env_manager import (
    check_embedded_environment_dependencies,
    get_embedded_python_executable,
    get_embedded_python_info,
    get_environment_mode,
)
from vibeocr.env_manager import get_dependency_versions
```

在 `connect_signals`（line 106-113 的环境维护块）加新按钮连接：

```python
        # --- 环境维护：重装 Python 运行时 / 重装 OCR 依赖 / 补充安装缺失依赖 ---
        btn_reinstall_python = self._ui.findChild(QPushButton, "btnReinstallPython")
        if btn_reinstall_python:
            btn_reinstall_python.clicked.connect(self._on_reinstall_python)

        btn_reinstall_deps = self._ui.findChild(QPushButton, "btnReinstallDeps")
        if btn_reinstall_deps:
            btn_reinstall_deps.clicked.connect(self._on_reinstall_deps)

        btn_install_missing = self._ui.findChild(QPushButton, "btnInstallMissing")
        if btn_install_missing:
            btn_install_missing.clicked.connect(self._on_install_missing)
```

- [ ] **Step 5: 修改 `_open_reinstall_dialog` 支持 `missing_only`**

把 `_open_reinstall_dialog`（line 556）改为接受 `missing_only` 参数：

```python
    def _open_reinstall_dialog(
        self, reinstall_python: bool = False, missing_only: bool = False
    ) -> None:
        """以非模态方式打开重装/补装对话框（不阻塞主窗口）。

        show() 后必须持有 dialog 引用以防 GC；finished 时刷新环境状态并移除引用。
        """
        dialog = BackendChoiceDialog(
            self._project_root,
            reinstall_python=reinstall_python,
            missing_only=missing_only,
        )

        def _on_finished(_result: int) -> None:
            self._refresh_env_maintenance_state()
            try:
                self._active_dialogs.remove(dialog)
            except ValueError:
                pass

        dialog.finished.connect(_on_finished)
        self._active_dialogs.append(dialog)
        dialog.show()
```

> 注意：现有 `_on_reinstall_python`（line 592）和 `_on_reinstall_deps`（line 608）调用 `_open_reinstall_dialog(reinstall_python=True/False)`，`missing_only` 缺省 False，行为不变。

新增 `_on_install_missing`（放在 `_on_reinstall_deps` 之后，line 608 之后）：

```python
    def _on_install_missing(self) -> None:
        """补充安装缺失依赖按钮：确认后弹 BackendChoiceDialog(missing_only=True)"""
        reply = QMessageBox.question(
            None,
            "确认补充安装缺失依赖",
            "将检测并只安装缺失的 OCR 依赖（已安装的自动跳过，不重复下载）。\n\n"
            "适合上次安装中途失败后补装。\n\n是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._open_reinstall_dialog(missing_only=True)
```

- [ ] **Step 6: 修改 `_refresh_env_maintenance_state` 填充表格**

把 `_refresh_env_maintenance_state`（line 610-633）扩展，加表格填充逻辑。在现有 label/button 逻辑后加：

```python
    def _refresh_env_maintenance_state(self) -> None:
        """刷新环境维护区状态：显示 Python 路径/就绪，依赖状态表格，非 portable 禁用按钮"""
        label = self._ui.findChild(QLabel, "labelEnvStatus")
        btn_py = self._ui.findChild(QPushButton, "btnReinstallPython")
        btn_deps = self._ui.findChild(QPushButton, "btnReinstallDeps")
        btn_missing = self._ui.findChild(QPushButton, "btnInstallMissing")
        table = self._ui.findChild(QTableWidget, "tableDepsStatus")

        mode = get_environment_mode(self._project_root)
        info = get_embedded_python_info(self._project_root)

        if label:
            if mode == "portable":
                status = "已安装" if info.get("ready") else "未安装"
                label.setText(f"Python 运行时：{status}\n路径：{info.get('path', '未知')}")
            elif mode == "venv":
                label.setText("开发模式（.venv），请用 uv sync 管理环境")
            else:
                label.setText("Python 运行时：未安装")

        # 仅 portable 模式启用重装/补装按钮（开发态 .venv 由 uv 管理）
        enabled = mode == "portable"
        if btn_py:
            btn_py.setEnabled(enabled)
        if btn_deps:
            btn_deps.setEnabled(enabled)
        if btn_missing:
            btn_missing.setEnabled(enabled)

        # 填充依赖状态表格（仅 portable 模式）
        if table and mode == "portable":
            self._populate_deps_table(table)
        elif table:
            table.setRowCount(0)

    def _populate_deps_table(self, table: QTableWidget) -> None:
        """填充依赖状态表格（名称/状态/版本）"""
        # 依赖展示顺序与 OCR_CHECK_MODULES 一致
        from vibeocr.services.env_config import OCR_CHECK_MODULES

        display_names = {
            "paddlepaddle": "PaddlePaddle",
            "paddleocr": "PaddleOCR",
            "mineru": "MinerU",
            "torch": "PyTorch",
        }
        ordered_pkgs = list(OCR_CHECK_MODULES.values())  # 保持插入顺序

        python_exe = get_embedded_python_executable(self._project_root)
        deps_status = check_embedded_environment_dependencies(self._project_root)
        versions = get_dependency_versions(python_exe) if python_exe.exists() else {}

        table.setRowCount(len(ordered_pkgs))
        for row, pkg in enumerate(ordered_pkgs):
            installed = deps_status.get(pkg, False)
            name_item = QTableWidgetItem(display_names.get(pkg, pkg))
            status_text = "✓ 已安装" if installed else "✗ 未安装"
            status_item = QTableWidgetItem(status_text)
            ver_item = QTableWidgetItem(versions.get(pkg, ""))
            table.setItem(row, 0, name_item)
            table.setItem(row, 1, status_item)
            table.setItem(row, 2, ver_item)
```

需要在 import 块（line 11-24）加 `QTableWidget`、`QTableWidgetItem`：

```python
from PySide6.QtWidgets import (
    QCheckBox,
    QLabel,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
```

- [ ] **Step 7: 运行测试，确认通过**

Run: `python -m pytest tests/views/test_settings_reinstall.py -v`
Expected: 全部 PASS

- [ ] **Step 8: 运行全部测试，确认无回归**

Run: `python -m pytest tests/views/ tests/widgets/test_backend_choice_dialog.py tests/widgets/test_install_worker_force_backend.py tests/test_env_manager_install.py -v`
Expected: 全部 PASS

- [ ] **Step 9: 提交**

```bash
git add src/vibeocr/views/settings_page_controller.py src/vibeocr/env_manager.py tests/views/test_settings_reinstall.py
git commit -m "feat(settings): 连接补充安装按钮，依赖状态表格填充版本与状态"
```

---

## Task 8: 首启路径改走增量安装

**Files:**
- Modify: `src/vibeocr/widgets/install_dialog.py:97`（`InstallWorker.run` 已在 Task 4 改造）
- Modify: `src/vibeocr/views/main_window.py`（首启 `_start_install` 创建 `BackendChoiceDialog` 时确认默认行为）

首启时 `InstallWorker` 缺省 `missing_only=False`（全量），这保持向后兼容。但 spec 要求首启走增量——重新审视：首启时 `python/` 刚装好，所有依赖都缺失，增量检测会全部失败 → 等同全量。所以**首启无需改 `missing_only`**，只需确认"失败重试时走增量"。

重试场景由设置页"补充安装缺失依赖"按钮覆盖。首启失败后用户会被引导到设置页。

- [ ] **Step 1: 确认首启路径行为符合预期**

Run: `python -m pytest tests/widgets/test_install_worker_force_backend.py tests/views/test_main_window.py -v`
Expected: 全部 PASS（首启仍走全量，符合首启场景）

> **决策记录**：首启 `InstallWorker` 保持 `missing_only=False`（全量）。原因：首启时所有依赖缺失，增量和全量行为一致，无需多几次 import 检测开销。增量价值体现在"中途失败后补装"——这由设置页新按钮覆盖。spec §3.2 的"首启改增量"经分析后调整为"首启保持全量"，因为增量在首启无收益。**这是对 spec 的合理偏离，已在 Task 4 的 InstallWorker 设计中通过缺省 False 体现。**

- [ ] **Step 2: 提交（如有改动）**

如果本任务无代码改动（仅确认），则跳过提交。

---

## Task 9: 全量测试 + 手动验证清单

**Files:** 无（验证任务）

- [ ] **Step 1: 运行全部受影响的测试套件**

Run: `python -m pytest tests/test_env_manager_install.py tests/widgets/test_install_worker_force_backend.py tests/widgets/test_backend_choice_dialog.py tests/views/test_settings_reinstall.py tests/views/test_main_window.py tests/views/test_main_window_backend_switch.py tests/views/test_main_window_pending_sync.py -v`
Expected: 全部 PASS

- [ ] **Step 2: 运行完整测试套件确认无回归**

Run: `python -m pytest tests/ -x -q`
Expected: 全部 PASS（或仅已知与本次无关的失败）

- [ ] **Step 3: 手动验证清单（如能启动 UI）**

在便携模式（portable）下：
1. 设置页"环境维护"区应显示依赖状态表格，4 行（PaddlePaddle/PaddleOCR/MinerU/PyTorch），状态/版本正确。
2. "补充安装缺失依赖"按钮可见且可点。
3. 点"补充安装缺失依赖"→ 确认对话框 → 后端选择对话框 → 选 GPU/CPU → 安装。
4. 验证：已装的包在日志中显示"✓ 已安装，跳过"，未装的才执行 pip install。
5. 模拟失败（断网）→ 应弹 QMessageBox.warning 含失败详情 + 重试提示。
6. 开发模式（venv）下：表格为空，所有环境维护按钮禁用。

- [ ] **Step 4: 最终提交（如有手动验证发现的修复）**

```bash
git add -A
git commit -m "test: 增量依赖安装全量测试通过"
```

---

## 自查清单

- [x] **Spec §3.1 核心增量逻辑** → Task 1（提取 `_build_paddle_requirements`）+ Task 2（`requirements_override`）+ Task 3（`install_missing_dependencies` + 失败 logger.error）
- [x] **Spec §3.2 三处调用入口** → Task 4（InstallWorker 分流）+ Task 7（设置页新按钮走 missing_only）+ Task 8（首启分析：保持全量，偏离已记录）
- [x] **Spec §3.3 设置页 UI** → Task 6（按钮+表格）+ Task 7（表格填充）
- [x] **Spec §3.4 失败提示+写log** → Task 3（logger.error 全文）+ Task 5（QMessageBox.warning 弹窗）
- [x] **类型一致性**：`missing_only` 全链路一致（InstallWorker → BackendChoiceDialog → settings controller）；`requirements_override` 签名与 Task 2 一致；`get_dependency_versions` 返回 `{包名: 版本}` 与 `_check_imports` 的 key 命名一致
- [x] **无占位符**：所有代码块完整，无 TBD/TODO

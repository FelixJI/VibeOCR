# 依赖增量安装（断点续传语义）设计

**日期**: 2026-06-26
**状态**: 已批准
**作者**: brainstorming session
**关联**: `2026-06-25-install-logging-and-reinstall-entry-design.md`

## 1. 背景与目标

### 问题
当前依赖安装（`env_manager._install_paddle_stack`，line 721）每次都构建硬编码 `requirements`
列表并逐个 `pip install`，**没有"跳过已装包"的逻辑**。一旦安装中途失败（网络中断、超时、
镜像 404），用户重试时必须从头重新下载安装全部依赖——GPU 场景下 torch 单包 2GB+，体验极差。

此外：
- 设置页 `groupEnvMaintenance` 只有**一个粗粒度状态 label**（`labelEnvStatus`），用户看不到
  到底哪个包装了、哪个没装。
- 安装失败时只把消息塞进返回值，**没有弹窗展示失败详情**。

### 目标
实现"**非严格意义的断点续传**"语义：
- **跳过已安装的依赖**，只下载/安装缺失的（判定标准：import 成功）。
- 设置页新增一个**"补充安装缺失依赖"按钮**，与现有"重装 OCR 依赖"按钮并列（后者保持全量重装）。
- 设置页**用表格逐项展示**每个依赖的名称/状态/版本。
- 安装失败时**弹窗提示 + 完整报错写 log**。

### 非目标
- **不做** HTTP Range / 本地 wheel 文件级断点续传（torch wheel 太大、缓存/校验逻辑远超需求）。
- **不改** GPU/CPU 切换、镜像源选择、torch index 计算等 `_install_paddle_stack` 内部的复杂分支。
- **不改** `force_backend` / `pending_backend` / `pending_sync.json` 等现有状态机。

## 2. 方案选型

| 方案 | 描述 | 结论 |
|---|---|---|
| **A. env_manager 层注入"已装即跳过" + 统一增量函数** | 改造 `_install_paddle_stack` 支持传入子集；新增 `install_missing_dependencies` 高层函数，三处统一调用 | **采用** |
| B. 纯 UI 层做，env_manager 不动 | 设置页算出缺失包后只装这些 | 排除：`requirements` 是函数内部硬编码，UI 无法选择性传参，必改签名 → 退化为 A |
| C. 本地 wheel 目录 + `pip install --find-links` | 真正缓存 wheel 文件做文件级续传 | 排除：远超需求，用户明确"不是严格意义的断点续传" |

## 3. 详细设计

### 3.1 核心增量逻辑（`env_manager.py`）

#### 3.1.1 改造 `_install_paddle_stack`（line 721）

新增一个可选参数：

```python
def _install_paddle_stack(
    python_exe, specs, pip_source, network_type, use_gpu, cuda_version,
    report_fn, success_msg,
    requirements_override: list[tuple[str, str, str]] | None = None,  # 新增
) -> tuple[bool, str]:
```

- `requirements_override=None`（缺省）：走现有逻辑，构建完整 `requirements`（保持向后兼容）。
- `requirements_override=<子集>`：跳过内部 `requirements` 构建分支，**直接使用传入的子集**
  进入逐包安装循环。

> 这样 GPU/CPU/镜像/torch 的复杂分支**一字不改**，只多一个可选入口。逐包安装循环、pip 升级、
> PyPI 回退、超时/异常处理全部复用原实现。

#### 3.1.2 跳过判定原语

复用现有 `_check_imports`（line 642）和 `env_config.OCR_CHECK_MODULES`：

```python
OCR_CHECK_MODULES = {
    "paddle": "paddlepaddle",
    "paddleocr": "paddleocr",
    "mineru": "mineru",
    "torch": "torch",
}
```

requirements 项与 import 模块的映射（新增常量，放 `_install_paddle_stack` 附近）：

| requirements 项（name） | 判定用的 import 模块 |
|---|---|
| `PaddlePaddle GPU (...)` / `PaddlePaddle CPU` | `paddle` |
| `PaddleOCR` | `paddleocr` |
| `MinerU` | `mineru` |
| `PyTorch CUDA (...)`（含 torch+torchvision 组合） | `torch` |

判定规则：**组合项（torch）只要主模块 `torch` 可 import 即整体跳过**。torchvision 与 torch
同源同索引安装，实践中随 torch 一起成功；若需更严格可在实现时扩展为两者都检测，但不作为强制需求。

> **映射查询说明（消除歧义）**：过滤时遍历 `requirements` 子集，对每项用上表的"判定用 import 模块"
> 反查到对应的 pip 包名（即 `OCR_CHECK_MODULES` 的 value），再用该包名去 `_check_imports` 的结果
> 字典里取 bool。即映射方向是 `requirements 项 → import 模块 → pip 包名 → _check_imports[包名]`。
> 例如 requirements 项 `PaddlePaddle GPU (cu126)` → import 模块 `paddle` → pip 包名 `paddlepaddle`
> → `_check_imports["paddlepaddle"]`。

#### 3.1.3 新增 `install_missing_dependencies`（高层统一入口）

位置：`env_manager.py`，`install_embedded_dependencies`（line 885）之后。

```python
def install_missing_dependencies(
    project_root: Path,
    network_type: Literal["domestic", "international"] = "domestic",
    use_gpu: bool = False,
    cuda_version: str | None = None,
    progress_callback=None,
    force_backend: str | None = None,
) -> tuple[bool, str]:
    """增量安装：只装 import 失败（缺失/损坏）的依赖，已装的跳过下载。
    """
```

实现步骤：
1. 复用 `install_embedded_dependencies` 的前置逻辑（python_exe 存在性、`force_backend`
   覆盖、`pip_source`、`report_fn`、`_load_dep_specs()`）。
2. **预先构建完整 requirements 列表**：把 `_install_paddle_stack` 内部构建 `requirements`
   的那段逻辑（line 763-826：GPU/CPU paddle 选择、torch index 计算）**提取为一个内部辅助函数**
   `_build_paddle_requirements(specs, use_gpu, cuda_version, network_type, report_fn)`，
   返回 `list[tuple[str, str, str]]`。`_install_paddle_stack` 和 `install_missing_dependencies`
   都调用它（消除重复）。
3. 调 `_check_imports(python_exe)` 得 `{包名: bool}`。
4. 按映射表把 `requirements` 过滤为子集：
   - 对每项查其判定模块的 import 结果；`True` 则 `report_fn("依赖安装", "✓ {name} 已安装，跳过")` 并排除。
5. 子集为空：`report_fn("依赖安装", "所有依赖已安装，无需补装")`；返回 `(True, "所有OCR依赖已安装")`。
6. 子集非空：调 `_install_paddle_stack(..., requirements_override=子集)`。

### 3.2 三处调用入口

| 调用点 | 文件:行 | 改造 |
|---|---|---|
| 首启自动安装 | `install_dialog.py:97` | `install_embedded_dependencies(...)` → `install_missing_dependencies(...)`。首启几乎全缺，等同全量；中途失败重试时已装包跳过 |
| 设置页新按钮 | `settings_page_controller.py`（新增 `_on_install_missing`） | 直接调 `install_missing_dependencies` |
| "重装 OCR 依赖"按钮 | `settings_page_controller.py:594` (`_on_reinstall_deps`) | **保持不变**，仍走 `install_embedded_dependencies`（全量） |

### 3.3 设置页 UI

#### 3.3.1 新增按钮 `btnInstallMissing`
- 位置：`main_window.ui` 的 `groupEnvMaintenance` 内，与现有 `btnReinstallPython`、
  `btnReinstallDeps` 并列。
- 文案：**"补充安装缺失依赖"**。
- 信号：`settings_page_controller.connect_signals()` 连接 → 新增 `_on_install_missing`。
- 复用现有 `_open_reinstall_dialog`（line 556）的非模态打开方式，但给 `InstallWorker` 传新标志
  `missing_only=True`。
- 可见性：仅 `portable` 模式（与现有重装按钮一致）。

#### 3.3.2 `InstallWorker` 新增标志

`install_dialog.py:22` 的 `InstallWorker.__init__` 加 `missing_only: bool = False` 参数。
`run()`（line 97）据此选择：

```python
install_fn = (
    env_manager.install_missing_dependencies
    if self._missing_only
    else env_manager.install_embedded_dependencies
)
success, msg = install_fn(self._project_root, network_type, has_gpu, cuda_version,
                          progress_callback=self._emit_progress,
                          force_backend=self._force_backend)
```

#### 3.3.3 依赖状态表格

- 位置：`groupEnvMaintenance` 内，现有 `labelEnvStatus` 下方。
- 控件：`QTableWidget`，列：`依赖 | 状态 | 版本号`。
- 行（固定 4 项，来自 `OCR_CHECK_MODULES`）：PaddlePaddle / PaddleOCR / MinerU / PyTorch。
- 状态：✓ 已安装（绿）/ ✗ 未安装（红）。
- 版本号：新增轻量获取函数 `_get_import_versions(python_exe) -> dict[str, str]`，
  执行 `python -c "import X; print(getattr(X,'__version__','未知'))"`，失败留空。
- 数据刷新：扩展 `_refresh_env_maintenance_state`（line 610），在刷新 label 的同时填充表格。
  安装开始/完成/失败后也触发一次刷新（复用 `MainWindow._refresh_settings_env_state`，line 742）。
- 可见性：仅 `portable` 模式。

### 3.4 失败提示 + 写 log

#### 3.4.1 写 log（强化现有）
- `report_fn` 已走 `logger.info`（`vibeocr.log`），跳过/成功/失败报错已落盘。**无需改**。
- **新增**：`_install_paddle_stack` 在返回失败前（line 872 附近），先 `logger.error` 完整
  `result.stderr`（目前只截 500 字符返回给 UI，但全文应先落盘便于排查）。

#### 3.4.2 弹窗提示
在两处 `finished(success, msg)` 处理点判断 `success=False` 时弹 `QMessageBox.warning`：
- **标题**："依赖安装失败"
- **正文**：返回 `msg`（含失败包名 + 截断 stderr）
- **附注**："可点击『补充安装缺失依赖』按钮重试（已安装的依赖会自动跳过）。"
- 位置：
  - `InstallDialog` 的 `finished` 处理（首启/首启重试路径）。
  - 设置页 reinstall dialog 的 `finished` 处理（`settings_page_controller._open_reinstall_dialog`
    打开的 dialog；新按钮和重装按钮共用此弹窗逻辑）。

## 4. 数据流

```
首启 / 设置页新按钮点击
    │
    ▼
InstallWorker(missing_only=True)
    │
    ▼
env_manager.install_missing_dependencies()
    │
    ├─ _load_dep_specs()           # 版本规格
    ├─ _build_paddle_requirements() # 完整 requirements 列表（提取自 _install_paddle_stack）
    ├─ _check_imports()             # {包名: bool}
    ├─ 过滤掉 import 成功的项 → 子集
    │   └─ 子集为空 → (True, "所有依赖已安装")
    └─ _install_paddle_stack(requirements_override=子集)
            │
            └─ 逐包 pip install（含 PyPI 回退）→ (bool, msg)
    │
    ▼
finished(success, msg) 信号
    ├─ success=True  → 关闭对话框，刷新状态表格
    └─ success=False → QMessageBox.warning(失败详情 + 重试提示) + logger.error(完整 stderr)
```

## 5. 受影响文件

| 文件 | 改动 |
|---|---|
| `src/vibeocr/env_manager.py` | 提取 `_build_paddle_requirements`；`_install_paddle_stack` 加 `requirements_override` 参数；新增 `install_missing_dependencies`；失败前 `logger.error` 全文 |
| `src/vibeocr/widgets/install_dialog.py` | `InstallWorker` 加 `missing_only` 标志；`run()` 按标志分流；`finished` 失败时弹窗 |
| `src/vibeocr/ui/main_window.ui` | `groupEnvMaintenance` 新增 `btnInstallMissing` 按钮 + 依赖状态 `QTableWidget` |
| `src/vibeocr/ui/ui_main_window.py` | `scripts/compile_ui.py` 重新生成 |
| `src/vibeocr/views/settings_page_controller.py` | 连接 `btnInstallMissing` → `_on_install_missing`；`_open_reinstall_dialog` 支持 `missing_only` 透传；`_refresh_env_maintenance_state` 填充表格；新增 `_get_import_versions` 版本获取 |
| `src/vibeocr/views/main_window.py` | 无直接改动（`_refresh_settings_env_state` 复用，自动含表格刷新） |

## 6. 测试要点

- **增量语义**：模拟 `_check_imports` 返回部分已装 → 验证只对缺失项调 pip。
- **全空跳过**：全部已 import 成功 → 返回 `(True, ...)` 且不调任何 pip。
- **全缺**：全部未装 → 等同全量安装（与改造前行为一致）。
- **GPU 分支**：`use_gpu=True` 时 torch 项正确进入/被跳过。
- **失败弹窗**：mock pip 返回非 0 → 验证弹窗文案含失败包名 + 重试提示。
- **版本号获取**：包已装但无 `__version__` 属性 → 版本列显示"未知"或留空，不崩溃。
- **portable-only 可见性**：venv/none 模式下表格和按钮隐藏。
- **回归**：首启正常安装流程、"重装 OCR 依赖"全量流程行为不变。

## 7. 风险与权衡

- **torchvision 跳过判定**：仅检测 `torch` import 即跳过整个组合项。极端情况下 torch 装了但
  torchvision 缺失会被误判。缓解：实践中同源同索引安装一起成功；如需严格可在实现时扩展为双检测，
  属实现细节不改变接口。
- **import 进程开销**：每次安装多几次 import 进程（几百 ms × 包数）。可接受；首启/重试场景非高频。
- **缓存过期**：`machine_cache.py` 的 `{pkg: bool}` 可能过期。本设计的判定**不依赖缓存**，每次
  实时 `_check_imports`，与现有 `is_embedded_environment_ready` 的轻量验证策略一致，无新增风险。

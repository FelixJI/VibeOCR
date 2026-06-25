# 安装日志接入 logging + 设置页重装入口

- 日期：2026-06-25
- 状态：已确认，待实现

## 背景

打包后客户报"依赖安装失败"，但 `vibeocr.log` 里看不到任何安装过程。根因：`env_manager` 的安装函数（`install_embedded_python` / `install_embedded_dependencies` / `switch_paddle_backend`）全程用 `print()`，输出仅进安装对话框的 `QTextEdit`，不写日志文件（日志体系只接 `logging` 模块）。同时设置页缺少"重装依赖/Python"入口，客户遇到环境损坏时无法自助修复，只能重装整个应用。

## 目标

1. **安装过程日志落盘**：所有安装/重装相关输出从 `print` 改为 `logging`，写入 `vibeocr.log`（走现有 `RotatingFileHandler`），便于客户报错时远程排查。
2. **设置页重装入口**：在"应用设置"页新增"环境维护"分组，提供两个独立按钮：
   - **重装 Python 运行时**（删 `python/` 后重下 Python + 重装 OCR 依赖）
   - **重装 OCR 依赖**（不动 `python/`，仅 pip 重装 paddle/torch/mineru 栈）

## 非目标

- 不改首启安装流程（仍走 `BackendChoiceDialog`）。
- 不改日志文件路径/rotate 策略（沿用 `log_service.setup_logging`）。
- 不做"卸载并清理"（只删不装）——YAGNI。
- 不改 OCR worker 子进程的独立 logging 配置。

## 删除范围说明（安全约束）

这是本次最关键的安全点，必须在 UI 文案与代码注释中明确：

| 操作 | 删除范围 | **不删除** |
|------|----------|-----------|
| **重装 Python 运行时** | 仅 `project_root/python/` 整个目录（`shutil.rmtree(..., ignore_errors=True)`） | `.venv`（开发态）、`config/`、`resources/`、`logs/`、用户设置、模型缓存、机器检测缓存 |
| **重装 OCR 依赖** | **不删除任何目录**，仅对现有 `python/` 跑 `pip install --upgrade --force-reinstall`（paddle 栈） | 同上全部不碰 |

- 打包态 `project_root` = exe 同级目录（`get_project_root()` L1127-1129）；开发态 = 仓库根（`.venv` 模式，`python/` 不存在，重装 Python 按钮在开发态禁用或提示用 `.venv`）。
- `python/` 清空后 OCR 依赖随之消失，故"重装 Python 运行时"必须**连带重装 OCR 依赖**（只装 Python 不装依赖无意义）。

## 架构

```
设置页-应用设置 ─→ groupEnvMaintenance（新增分组）
                    ├─ btnReinstallPython  ──→ 确认对话框（说明删除范围）
                    │                          → BackendChoiceDialog(reinstall_python=True)
                    │                              → InstallWorker(reinstall_python=True)
                    │                                  → reinstall_embedded_python()  [删 python/ + 装 Python]
                    │                                  → install_embedded_dependencies()
                    └─ btnReinstallDeps    ──→ 确认对话框（说明不删目录）
                                               → BackendChoiceDialog(reinstall_python=False)
                                                   → InstallWorker(reinstall_python=False)
                                                       → install_embedded_dependencies()  [pip 重装]
```

两个按钮均复用现有 `BackendChoiceDialog`（首启合并对话框，内含 GPU/CPU 选择 + InstallDialog 进度区），避免新造对话框。

## 组件

### 1. `env_manager.py` — print 改 logging

- 顶部加 `import logging` + `logger = logging.getLogger(__name__)`。
- 将以下函数中的 `print(...)` 改为 `logger.info(...)`（失败/异常改 `logger.error` / `logger.warning`）：
  - `install_embedded_python`（L328-469）：L361-363, L386, L402, L436, L461-467
  - `download_file_with_progress`（L292-325）：L297, L315-316, L320, L324
  - `install_embedded_dependencies` 的 `report` 闭包（L865-868）
  - `switch_paddle_backend` 的 `report` 闭包（L1063-1066）
- **转换规则**：
  - `print(f"\r[{desc}] 进度: {p}% (...)")` → `logger.info("[%s] 进度: %d%% (%dMB/%dMB)", desc, p, ...)`（去掉 `\r` 和 `end=""`，日志不需要原地刷新）
  - 普通信息 `print(f"[{stage}] {msg}")` → `logger.info("[%s] %s", stage, msg)`
  - `print(... 失败: {e})` → `logger.error(... 失败: %s", e)`
- `progress_callback` 机制**保留不动**（UI 进度仍走回调），logging 是额外并行输出。
- `switch_paddle_backend` 内 `pip uninstall`（L1073-1088）的 subprocess 输出也一并 `logger.info`。

### 2. `env_manager.py` — 新增 `reinstall_embedded_python`

仿 `install_embedded_python` 签名，加 `progress_callback` 参数：

```python
def reinstall_embedded_python(
    project_root: Path,
    network_type: Literal["domestic", "international"] = "domestic",
    progress_callback=None,
) -> tuple[bool, str]:
    """强制删除现有 python/ 目录后重新安装 Python 运行时。

    删除范围：仅 project_root/python/ 整个目录。
    不删除：.venv、config/、resources/、logs/、模型缓存、机器检测缓存。
    """
```

- 实现：`shutil.rmtree(project_root / "python", ignore_errors=True)` → 调 `install_embedded_python(project_root, network_type)`（其内部已含下载/解压/半成品清理/pip 自检）。
- 通过 `progress_callback` 上报"正在清理旧目录..."阶段。

### 3. `widgets/install_dialog.py` — `InstallWorker` 加 `reinstall_python` 参数

- `InstallWorker.__init__(self, project_root, force_backend=None, reinstall_python=False)`
- `run()` 增加分支：
  - `reinstall_python=True`：先 `env_manager.reinstall_embedded_python(project_root, network_type, progress_callback=emit)`，失败则 emit finished 退出；成功后继续 `install_embedded_dependencies(...)`（与现有流程合并）。
  - `reinstall_python=False`：保持现有逻辑（检查 python/ 是否存在，不存在则 `install_embedded_python`，存在则跳过直接装依赖）。
- **额外**：`progress` 信号回调时也 `logger.info` 一份（确保 UI 显示的进度也落盘，即便 env_manager 漏改某处）。

### 4. `widgets/backend_choice_dialog.py` — 透传 `reinstall_python`

- `BackendChoiceDialog.__init__` 增加 `reinstall_python: bool = False` 参数，传给内部创建的 `InstallWorker`。
- 对话框标题/副文案根据 `reinstall_python` 动态调整（"重装 Python 运行时" / "重装 OCR 依赖"）。

### 5. `ui/main_window.ui` + `ui_main_window.py` — 应用设置页新增"环境维护"分组

在 `pageAppSettings` 的 `groupAppSettings`（L474-590）之后、`spacerAppPage`（L593）之前，插入：

```xml
<widget class="QGroupBox" name="groupEnvMaintenance">
  <property name="title"><string>环境维护</string></property>
  <layout class="QVBoxLayout" name="envMaintenanceLayout">
    <item><widget class="QLabel" name="labelEnvStatus"><string>Python 运行时：检测中...</string></widget></item>
    <item><widget class="QPushButton" name="btnReinstallPython">
      <property name="text"><string>重装 Python 运行时</string></property>
      <property name="toolTip"><string>删除 python/ 目录后重新下载安装 Python 运行时及 OCR 依赖。仅删除 python/，不影响配置、模型缓存和日志。</string></property>
    </widget></item>
    <item><widget class="QPushButton" name="btnReinstallDeps">
      <property name="text"><string>重装 OCR 依赖</string></property>
      <property name="toolTip"><string>使用 pip 重新安装 paddle/torch/mineru 等 OCR 依赖，不删除任何目录。</string></property>
    </widget></item>
  </layout>
</widget>
```

- 重新生成 `ui_main_window.py`（`pyside6-uic`，或手动同步）。
- `labelEnvStatus` 运行时显示：Python 路径 + 是否就绪（读 `get_embedded_python_info`）。

### 6. `views/settings_page_controller.py` — 连接按钮信号

- `connect_signals()` 中连接：
  - `btnReinstallPython.clicked` → `_on_reinstall_python`
  - `btnReinstallDeps.clicked` → `_on_reinstall_deps`
- `_on_reinstall_python`：
  1. 弹 `QMessageBox.question` 确认，文案明确删除范围："将删除 `python/` 目录（含所有 OCR 依赖）后重新下载安装。配置、模型缓存、日志不受影响。是否继续？"
  2. 确认 → 弹 `BackendChoiceDialog(self._project_root, reinstall_python=True)`（模态）
  3. 完成后刷新 `labelEnvStatus` + 状态栏
- `_on_reinstall_deps`：
  1. 弹确认："将使用 pip 重新安装 OCR 依赖（paddle/torch/mineru），不删除任何文件。是否继续？"
  2. 确认 → 弹 `BackendChoiceDialog(self._project_root, reinstall_python=False)`
- 重装进行中：禁用两个按钮（通过对话框模态性自然阻断；对话框关闭后恢复）。

## 数据流

```
用户点"重装 Python 运行时"
  → 确认对话框（说明删除范围）
  → BackendChoiceDialog(reinstall_python=True)
      → 选 GPU/CPU
      → InstallWorker(reinstall_python=True, force_backend=选择)
          → reinstall_embedded_python(): rmtree(python/) + install_embedded_python()  [全程 logger.info]
          → install_embedded_dependencies(force_backend=选择)  [全程 logger.info]
          → finished 信号
      → 对话框显示成功/失败
  → settings_page_controller 刷新 labelEnvStatus
  → 全程 logger.info 写入 vibeocr.log
```

## 边界情况

- **重装进行中重复点击**：对话框模态，自然阻断。
- **网络失败**：`reinstall_embedded_python` 已删 `python/`，下载失败则 `python/` 不存在 → `InstallWorker` emit 失败信息，`labelEnvStatus` 显示"未安装"。用户可再次点重装。
- **开发态（`.venv` 模式）**：`python/` 不存在，"重装 Python 运行时"按钮应禁用或提示"开发环境请用 uv sync 管理 .venv"；"重装 OCR 依赖"按钮同样指向 `.venv`，可禁用并提示。判断：`get_environment_mode() == "none"` 或 `"venv"` 时禁用两按钮（仅 `"portable"` 模式启用）。
- **解压失败**：`install_embedded_python` 内部已 `shutil.rmtree` 清理半成品（L439-441），不会残留损坏目录。
- **pip 失败**：`install_embedded_dependencies` 返回 `(False, msg)`，对话框显示错误，`python/` 仍在（仅依赖未装），用户可点"重装 OCR 依赖"重试。

## 测试

- `tests/env_manager/test_install_logging.py`（新建或扩展）：
  - mock `logging.getLogger`，调用 `install_embedded_python` / `install_embedded_dependencies`，断言 `logger.info` 被调用且 message 含预期阶段文本。
  - mock `urlopen`/`subprocess`，验证下载/解压/pip 各阶段有对应日志。
- `tests/env_manager/test_reinstall_python.py`（新建）：
  - mock `shutil.rmtree` + `install_embedded_python`，调用 `reinstall_embedded_python`，断言 rmtree 以 `python/` 为参数被调用，再断言 `install_embedded_python` 被调用。
  - 验证 `progress_callback` 收到"清理"阶段。
- `tests/widgets/test_install_dialog.py`（扩展）：
  - `InstallWorker(reinstall_python=True)` → mock env_manager，断言先调 `reinstall_embedded_python` 再调 `install_embedded_dependencies`。
  - `reinstall_python=False` → 保持现有行为。
- `tests/views/test_settings_reinstall.py`（新建）：
  - 点击 `btnReinstallPython` → 弹确认 → mock `QMessageBox` 返回 Yes → 断言 `BackendChoiceDialog(reinstall_python=True)` 被创建。
  - 开发态（`get_environment_mode` mock 为 `venv`）→ 两按钮禁用。
  - `labelEnvStatus` 显示正确的 Python 状态。

## 实现顺序（writing-plans 会细化）

1. `env_manager.py`：加 logger + print→logging（最小改动，立即让日志落盘）
2. `env_manager.py`：新增 `reinstall_embedded_python`
3. `install_dialog.py`：`InstallWorker` 加 `reinstall_python` 参数
4. `backend_choice_dialog.py`：透传 `reinstall_python`
5. `main_window.ui` + `ui_main_window.py`：应用设置页加"环境维护"分组
6. `settings_page_controller.py`：连接按钮 + 确认对话框 + 状态刷新
7. 测试

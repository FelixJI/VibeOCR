# VibeOCR WinUI 3 + WebView2 + Python Worker 正式实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Each task must be completed, tested, reviewed, and committed before the next task starts.

**Goal:** 先修复当前 Python/PySide6 正式版中仍存在的构建、启动、路径和退出问题，再以旁路方式实现 C# WinUI 3 + WebView2 + Python Worker；只有完整功能对等、兼容迁移和健康门禁全部通过后，才把正式入口一次性切换到 WinUI 3，切换后不允许回退旧 UI。

**Architecture:** WinUI 3 是唯一目标桌面壳，WebView2 只承载复杂预览/编辑，Python WorkerHost 复用 OCR、PDF、二维码、导出和依赖管理能力。控制面使用 Named Pipe 上的 length-prefixed JSON-RPC，大对象通过具备所有权和校验信息的共享内存传递。旁路开发使用 `data/profiles/winui-dev`，不得写正式配置；正式切换由幂等 migrator 完成。

**Tech Stack:** Python 3.13、pytest 9.1.1、PyInstaller 6.21.0、.NET SDK 10.0.301、C# 14、Windows App SDK 2.2.0、WinUI 3、WebView2 Evergreen、xUnit、WinUI Unit Test App、GitHub Actions。

**Source design:** `docs/superpowers/specs/2026-07-11-winui3-worker-migration-design.md`

---

## 0. 不可变约束与执行规则

1. 发布形态固定为 `framework-dependent`、`unpackaged`、`win-x64`，目标框架为 `net10.0-windows10.0.17763.0`。
2. 迁移方式固定为旁路迁移；Phase 0–4 期间 `src/vibeocr/main.py` 仍是正式入口，WinUI 旁路版不得修改正式 profile。
3. 切换门槛是功能矩阵 100% 对等，不以“核心功能可用”代替完整对等。
4. 必须兼容既有 Python runtime、模型缓存、用户配置、快捷键和历史输出；迁移只增加 schema，不破坏原数据。
5. 正式切换后不提供旧 PySide6 UI 启动入口。迁移前备份只用于数据修复，不能作为 UI 回退通道。
6. 所有代码任务遵守 Red → Green → Refactor：先提交能稳定失败的测试，再写最小实现；本计划中的“提交节点”是最小提交边界，不得跨任务合并。
7. 每个 Phase 使用独立 `codex/` 分支或独立 worktree；Phase 合并前必须通过该 Phase 的全量门禁。
8. 不提交真实用户数据、`output/`、模型、runtime、构建产物或基准机器的隐私路径。
9. 下文单行 `Commit` 的完整含义固定为：先执行 `git add` 暂存该任务 `Files` 清单中的全部 Create/Modify/Add 路径，再执行所列 `git commit`；如果 `git diff --cached --name-only` 出现清单外文件，必须先移出暂存区。

## 1. 总体里程碑与硬门禁

| Phase | 交付物 | 进入下一阶段的硬门禁 |
|---|---|---|
| 0 | 当前正式版修复与可复现基线 | Python 全量测试通过；无 `QThread.terminate()` 生产调用；构建 manifest 可验证；T0–T6 可测 |
| 1 | Python WorkerHost 与版本化契约 | Python/C# golden contract 一致；取消/超时/崩溃恢复通过；共享内存无泄漏 |
| 2 | 可启动 WinUI 壳与平台能力 | Win10 1809/Win11 启动；runtime 缺失修复；单实例/托盘/热键/截图/DPI spike 全通过 |
| 3 | 单图 OCR 完整闭环 | 文件/剪贴板/截图输入、预览编辑、OCR、复制和导出对等 |
| 4 | 其余完整功能 | 批量、二维码、PDF、依赖、后端、设置、更新、托盘全部对等 |
| 5 | 数据迁移与正式切换 | 功能矩阵 100%；迁移幂等；冷启动 p95 或体积至少改善 30%；切换包健康门禁通过 |

---

# Phase 0：先解决当前发现的问题

## Task 0.1：冻结当前基线与回归入口

**Files:**

- Create: `scripts/run_phase0_gate.ps1`
- Create: `tests/fixtures/startup/baseline.schema.json`
- Create: `docs/quality/phase0-gate.md`
- Modify: `.gitignore`

**Red:** 新增一个脚本自检模式 `./scripts/run_phase0_gate.ps1 -ValidateOnly`；在脚本不存在时命令失败。

**Green:** 脚本必须依次执行锁文件同步、定向回归、全量 pytest、Ruff、Pyright，并把基准 JSON 写到被忽略的 `reports/local/`。不得把本机绝对路径写入报告。

```powershell
param([switch]$ValidateOnly)
$ErrorActionPreference = "Stop"
uv sync --frozen --group dev
uv run pytest -q
uv run ruff check src tests scripts
uv run pyright
```

**Verify:**

```powershell
./scripts/run_phase0_gate.ps1 -ValidateOnly
```

Expected: exit code 0，输出每个门禁命令但不运行耗时测试；schema 校验成功。

**Commit:**

```powershell
git add .gitignore scripts/run_phase0_gate.ps1 tests/fixtures/startup/baseline.schema.json docs/quality/phase0-gate.md
git commit -m "test: define phase 0 quality gate"
```

## Task 0.2：锁定 GitHub 构建输入并生成产物清单

**Files:**

- Create: `requirements/build-shell.lock`
- Create: `requirements/build-shell.in`
- Create: `src/vibeocr/build_manifest.py`
- Create: `tests/test_build_manifest.py`
- Modify: `.github/workflows/release.yml`
- Modify: `scripts/bump_version.py`
- Modify: `tests/test_bump_version.py`

**Red:** 在 `tests/test_build_manifest.py` 写三组失败测试：锁文件每行必须是精确 `==`；staging 拒绝 `output/`、`.venv/`、`data/profiles/`；manifest 必须记录相对路径、字节数、SHA-256 且能拒绝篡改。为 `bump_version.py` 增加 clean staging 与 manifest 调用断言。

**Green:**

- `requirements/build-shell.in` 只列 CI 壳的直接依赖；用 `uv pip compile requirements/build-shell.in --generate-hashes -o requirements/build-shell.lock` 生成完整传递依赖锁。已确认核心解析版本至少包括 `pyinstaller==6.21.0`、`pyside6==6.11.1`、`pillow==12.3.0`、`numpy==2.3.5`、`httpx==0.28.1`。
- Release workflow 改为 `python -m pip install --require-hashes -r requirements/build-shell.lock`；锁文件用 `uv pip compile --generate-hashes` 维护。
- `bump_version.py` 只从显式 allowlist staging，不从工作树整目录复制。
- ZIP 根目录写入 `artifact-manifest.json`，并在上传前执行 `python -m vibeocr.build_manifest verify <zip>`。

```python
@dataclass(frozen=True)
class ManifestEntry:
    path: str
    size: int
    sha256: str

def create_manifest(root: Path, allowed_roots: tuple[str, ...]) -> dict: ...
def verify_archive(archive: Path) -> None: ...
```

**Verify:**

```powershell
uv run pytest tests/test_build_manifest.py tests/test_bump_version.py -q
uv run python -m vibeocr.build_manifest verify dist/VibeOCR-*-win64.zip
```

Expected: tests all pass；干净构建 ZIP 约 160 MB 的现状被记录但不硬编码；manifest 中不存在 `output/`。

**Commit:**

```powershell
git add requirements/build-shell.in requirements/build-shell.lock src/vibeocr/build_manifest.py tests/test_build_manifest.py .github/workflows/release.yml scripts/bump_version.py tests/test_bump_version.py
git commit -m "build: make portable artifacts reproducible"
```

## Task 0.3：修复启动分析器并建立 T0–T6 指标

**Files:**

- Create: `src/vibeocr/startup_metrics.py`
- Create: `tests/test_startup_metrics.py`
- Create: `tests/test_profile_startup.py`
- Modify: `scripts/profile_startup.py`
- Modify: `src/vibeocr/main.py`

**Red:** 证明当前 `profile_imports()` 没有测量真实 import；为重复事件、乱序事件、缺失 T6、路径脱敏和 p50/p95 汇总写失败测试。

**Green:** 定义并只定义以下事件：T0 进程入口、T1 Python bootstrap 完成、T2 Qt/WinUI 壳创建、T3 首窗可见、T4 WorkerHost ready、T5 OCR backend ready、T6 首次可交互。正式版默认不落盘，设置 `VIBEOCR_STARTUP_TRACE=<path>` 才输出 JSONL。

```python
class StartupEvent(StrEnum):
    PROCESS_START = "T0"
    RUNTIME_READY = "T1"
    SHELL_CREATED = "T2"
    FIRST_WINDOW = "T3"
    WORKER_READY = "T4"
    BACKEND_READY = "T5"
    INTERACTIVE = "T6"
```

**Verify:**

```powershell
uv run pytest tests/test_startup_metrics.py tests/test_profile_startup.py -q
uv run python scripts/profile_startup.py --runs 10 --output reports/local/python-startup.json
```

Expected: 10 次独立进程样本；报告包含 T0–T6、p50、p95；`profile_imports` 报告包含真实模块 import 时间。

**Commit:**

```powershell
git add src/vibeocr/startup_metrics.py tests/test_startup_metrics.py tests/test_profile_startup.py scripts/profile_startup.py src/vibeocr/main.py
git commit -m "perf: add trustworthy startup milestones"
```

## Task 0.4：建立 AppPaths 单一边界

**Files:**

- Create: `src/vibeocr/app_paths.py`
- Create: `tests/test_app_paths.py`
- Modify: `src/vibeocr/env_manager.py`
- Modify: `src/vibeocr/services/env_config.py`
- Modify: `tests/test_env_manager_bundled_paths.py`
- Modify: `tests/services/test_env_config.py`

**Red:** 覆盖 source、PyInstaller onedir、旁路 WinUI profile、正式 portable profile、带空格路径和只读安装目录。断言 import `vibeocr.app_paths` 不加载 PySide6。

**Green:** `resolve_app_paths()` 是路径真源；旧 helper 仅作兼容委托并标注废弃。`profile="winui-dev"` 必须解析到 `data/profiles/winui-dev`，不得触碰正式配置。

```python
@dataclass(frozen=True, slots=True)
class AppPaths:
    install_root: Path
    data_root: Path
    runtime_root: Path
    model_cache_root: Path
    output_root: Path
    config_file: Path

def resolve_app_paths(executable: Path, *, profile: str) -> AppPaths: ...
```

**Verify:**

```powershell
uv run pytest tests/test_app_paths.py tests/test_env_manager_bundled_paths.py tests/services/test_env_config.py -q
```

Expected: all pass；旁路 profile 测试确认正式配置文件 mtime/content 不变。

**Commit:**

```powershell
git add src/vibeocr/app_paths.py tests/test_app_paths.py src/vibeocr/env_manager.py src/vibeocr/services/env_config.py tests/test_env_manager_bundled_paths.py tests/services/test_env_config.py
git commit -m "refactor: centralize portable application paths"
```

## Task 0.5：让预加载任务具备所有权与协作取消

**Files:**

- Modify: `src/vibeocr/managers/subprocess_manager.py`
- Modify: `src/vibeocr/views/settings_page_controller.py`
- Modify: `tests/managers/test_subprocess_manager.py`
- Modify: `tests/views/test_settings_preload.py`

**Red:** 新增测试证明 `SubprocessManager.shutdown()` 会先取消 `_preload_task` 再关闭 service；设置页 shutdown 会取消 `_manual_preload_task`；任务结束后引用清零；迟到 signal 不更新已销毁 UI。

**Green:** 为两类 preload task 注入 `threading.Event`，在每个昂贵步骤前后检查；controller/manager 持有任务所有权。禁止新增 `QThread.terminate()`。

```python
def cancel(self) -> None:
    self._cancelled.set()

def _raise_if_cancelled(self) -> None:
    if self._cancelled.is_set():
        raise PreloadCancelled
```

**Verify:**

```powershell
uv run pytest tests/managers/test_subprocess_manager.py tests/views/test_settings_preload.py -q
rg -n "\.terminate\(" src/vibeocr
```

Expected: tests pass；`rg` 只允许注释/防御性测试命中，不允许生产调用。

**Commit:**

```powershell
git add src/vibeocr/managers/subprocess_manager.py src/vibeocr/views/settings_page_controller.py tests/managers/test_subprocess_manager.py tests/views/test_settings_preload.py
git commit -m "fix: cancel owned preload tasks during shutdown"
```

## Task 0.6：移除 PDF 缩略图线程强制终止

**Files:**

- Modify: `src/vibeocr/managers/pdf_session_manager.py`
- Modify: `src/vibeocr/views/tabs/pdf_tab.py`
- Modify: `src/vibeocr/workers/pdf_render_thumb_ipc_worker.py`
- Modify: `src/vibeocr/services/pdf_backend_client.py`
- Modify: `tests/managers/test_pdf_session_manager.py`
- Modify: `tests/views/tabs/test_pdf_tab.py`
- Modify: `tests/workers/test_pdf_render_thumb_ipc_worker.py`

**Red:** 用阻塞 fake HTTP 请求复现 timeout；断言 cancel 后不调用 `terminate()`、不访问已销毁 UI、未完成 worker 被放入 draining 集合，最终 finished 时回收。

**Green:** HTTP 请求必须有连接/读取总超时；cancel 停止派发新请求并 `cancel_futures=True`；超时后断开 UI signal、保留 worker 所有权等待自然退出，应用 shutdown 由统一 coordinator 有界等待。`_wait_thread()` 返回 `bool`，不得杀线程。

```python
def _wait_thread(self, worker: QThread, timeout_ms: int) -> bool:
    if not worker.isRunning():
        return True
    return worker.wait(timeout_ms)
```

**Verify:**

```powershell
uv run pytest tests/managers/test_pdf_session_manager.py tests/views/tabs/test_pdf_tab.py tests/workers/test_pdf_render_thumb_ipc_worker.py -q
rg -n "QThread\.terminate|\.terminate\(" src/vibeocr
```

Expected: tests pass；无生产代码强制终止线程。

**Commit:**

```powershell
git add src/vibeocr/managers/pdf_session_manager.py src/vibeocr/views/tabs/pdf_tab.py src/vibeocr/workers/pdf_render_thumb_ipc_worker.py src/vibeocr/services/pdf_backend_client.py tests/managers/test_pdf_session_manager.py tests/views/tabs/test_pdf_tab.py tests/workers/test_pdf_render_thumb_ipc_worker.py
git commit -m "fix: drain PDF thumbnail workers cooperatively"
```

## Task 0.7：提取不依赖 Qt 的应用服务边界

**Files:**

- Create: `src/vibeocr/application/__init__.py`
- Create: `src/vibeocr/application/contracts.py`
- Create: `src/vibeocr/application/ocr_facade.py`
- Create: `src/vibeocr/application/pdf_facade.py`
- Create: `src/vibeocr/application/settings_facade.py`
- Create: `tests/application/test_import_boundary.py`
- Create: `tests/application/test_ocr_facade.py`
- Create: `tests/application/test_pdf_facade.py`
- Modify: `src/vibeocr/views/tabs/single_recognition_tab.py`
- Modify: `src/vibeocr/views/tabs/pdf_tab.py`

**Red:** import-boundary 测试在清空 `sys.modules` 后导入 `vibeocr.application`，断言没有 `PySide6`；facade 测试以 fake adapter 验证参数、取消 token、错误映射和结果 DTO。

**Green:** UI 只能调用 facade；facade 只依赖 dataclass/Protocol 和现有 service，不发 Qt signal，不接触 widget。

```python
class OcrApplication(Protocol):
    def recognize(self, request: OcrRequest, cancel: CancelToken) -> OcrResult: ...

class PdfApplication(Protocol):
    def open(self, request: PdfOpenRequest, cancel: CancelToken) -> PdfSessionDto: ...
```

**Verify:**

```powershell
uv run pytest tests/application tests/views/tabs/test_single_recognition_tab.py tests/views/tabs/test_pdf_tab.py -q
uv run python -c "import sys; import vibeocr.application; assert 'PySide6' not in sys.modules"
```

Expected: application boundary is UI-free；现有 UI 行为回归通过。

**Commit:**

```powershell
git add src/vibeocr/application tests/application src/vibeocr/views/tabs/single_recognition_tab.py src/vibeocr/views/tabs/pdf_tab.py
git commit -m "refactor: expose UI-free application facades"
```

## Task 0.8：执行 Phase 0 门禁并记录基线

**Files:**

- Create: `docs/quality/baselines/2026-07-11-python-shell.md`
- Modify: `docs/packaging_startup_review_2026-07-11.md`

**Steps:** 在干净 worktree 执行完整门禁和 GitHub 等价构建；记录测试总数、机器信息脱敏摘要、ZIP 大小、文件数、T0–T6 p50/p95。明确区分“当前 GitHub 干净构建约 160 MB”与“历史本地脏工作区 728 MB 反例”。

**Verify:**

```powershell
./scripts/run_phase0_gate.ps1
uv run python scripts/bump_version.py --rebuild 0.4.22 --force
uv run python -m vibeocr.build_manifest verify dist/VibeOCR-v0.4.22-win64.zip
git status --short
```

Expected: 门禁全绿；ZIP 无 `output/`；只出现预期报告改动和被忽略的本地产物。

**Commit:**

```powershell
git add docs/quality/baselines/2026-07-11-python-shell.md docs/packaging_startup_review_2026-07-11.md
git commit -m "docs: record stabilized Python shell baseline"
```

---

# Phase 1：版本化 WorkerHost 契约

## Task 1.1：建立跨语言 JSON Schema 与 golden fixtures

**Files:**

- Create: `contracts/v1/envelope.schema.json`
- Create: `contracts/v1/methods.schema.json`
- Create: `contracts/v1/errors.json`
- Create: `contracts/v1/golden.json`
- Create: `tests/contracts/test_json_schema.py`
- Create: `docs/protocol/v1.md`

**Red:** malformed envelope、未知字段、错误 protocol version、缺失 request/task id、非法共享内存 descriptor 必须失败；每个公开 method 至少一个 request/response golden。

**Green:** Envelope 固定 `protocol_version`、`request_id`、`task_id`、`method`、`payload`、`deadline_unix_ms`；响应固定 `result` 或 `error` 二选一；事件固定 `event` 和 `sequence`。

```json
{"protocol_version":1,"request_id":"uuid","task_id":"uuid","method":"ocr.recognize","payload":{},"deadline_unix_ms":0}
```

**Verify:** `uv run pytest tests/contracts/test_json_schema.py -q`；Expected: all golden valid, all negative fixtures rejected.

**Commit:** `git commit -m "feat(protocol): define version 1 worker contracts"`

## Task 1.2：实现 Python DTO、错误和 length-prefixed framing

**Files:**

- Create: `src/vibeocr/worker_host/__init__.py`
- Create: `src/vibeocr/worker_host/contracts.py`
- Create: `src/vibeocr/worker_host/errors.py`
- Create: `src/vibeocr/worker_host/framing.py`
- Create: `tests/worker_host/test_contracts.py`
- Create: `tests/worker_host/test_framing.py`

**Red:** 覆盖半包、粘包、0 长度、超过 8 MiB 控制帧、无效 UTF-8、断流和未知错误码。

**Green:** 4-byte little-endian unsigned length + UTF-8 JSON；控制帧上限 8 MiB；DTO 拒绝多余字段；错误码稳定映射。

```python
async def read_frame(reader: AsyncByteReader, *, max_bytes: int = 8 << 20) -> bytes: ...
async def write_frame(writer: AsyncByteWriter, payload: bytes) -> None: ...
```

**Verify:** `uv run pytest tests/worker_host/test_contracts.py tests/worker_host/test_framing.py -q`.

**Commit:** `git commit -m "feat(worker): implement protocol framing and DTOs"`

## Task 1.3：实现当前用户隔离的 Named Pipe server

**Files:**

- Create: `src/vibeocr/worker_host/security.py`
- Create: `src/vibeocr/worker_host/named_pipe.py`
- Create: `tests/worker_host/test_security.py`
- Create: `tests/worker_host/test_named_pipe.py`

**Red:** 不同 session token、错误 Windows SID、第二客户端抢占、pipe name 注入必须拒绝；断线必须释放句柄。Windows-only integration test 用当前用户真实 pipe。

**Green:** 使用 Python 标准库 `ctypes` 调 Win32 API，避免为唯一平台 API 引入整套运行时依赖；DACL 只允许当前用户 SID，握手再校验 256-bit session token。Pipe 名只接受 worker 生成的 UUID。

```python
@dataclass(frozen=True)
class PipeEndpoint:
    name: str
    session_token: str

class NamedPipeServer:
    def accept(self, timeout_ms: int) -> PipeConnection: ...
```

**Verify:** `uv run pytest tests/worker_host/test_security.py tests/worker_host/test_named_pipe.py -q`；Expected: Windows integration tests pass and handles return to baseline.

**Commit:** `git commit -m "feat(worker): secure control channel with named pipes"`

## Task 1.4：实现共享内存描述符、所有权和回收

**Files:**

- Create: `src/vibeocr/worker_host/shared_payload.py`
- Create: `tests/worker_host/test_shared_payload.py`
- Modify: `contracts/v1/envelope.schema.json`
- Modify: `contracts/v1/golden.json`

**Red:** 覆盖 owner/client unlink 竞态、CRC/SHA mismatch、越界 size、TTL 回收、peer crash、重复 release；每个测试结束检查无残留 segment。

**Green:** descriptor 固定 `name`、`size`、`media_type`、`sha256`、`owner`、`expires_unix_ms`；只有 owner unlink，reader 只 close 并发送 release；启动和退出执行带 namespace 的 orphan sweep。

```python
class SharedPayloadStore:
    def put(self, data: bytes, media_type: str) -> SharedPayloadRef: ...
    def read(self, ref: SharedPayloadRef) -> bytes: ...
    def release(self, ref: SharedPayloadRef) -> None: ...
```

**Verify:** `uv run pytest tests/worker_host/test_shared_payload.py -q`；Expected: 测试内部完成 20 轮 create/read/release，zero leaked segments。

**Commit:** `git commit -m "feat(worker): transfer large payloads through shared memory"`

## Task 1.5：实现 dispatcher、任务状态机、取消和 deadline

**Files:**

- Create: `src/vibeocr/worker_host/dispatcher.py`
- Create: `src/vibeocr/worker_host/task_registry.py`
- Create: `src/vibeocr/worker_host/session.py`
- Create: `tests/worker_host/test_dispatcher.py`
- Create: `tests/worker_host/test_task_registry.py`

**Red:** 覆盖 unknown method、重复 request id、queued/running cancel、deadline、不可重试 mutation、迟到结果、worker exception 和 session disconnect。

**Green:** 状态机仅允许 `queued -> running -> completed|failed|cancelled`；terminal 后不再发业务事件；`cancel` 幂等；读取类可按设计重试，PDF mutation/save/update/dependency/backend switch 不自动重试。

```python
class TaskRegistry:
    def create(self, request: RpcRequest) -> TaskHandle: ...
    def cancel(self, task_id: UUID) -> CancelResult: ...
    def complete(self, task_id: UUID, outcome: Outcome) -> None: ...
```

**Verify:** `uv run pytest tests/worker_host/test_dispatcher.py tests/worker_host/test_task_registry.py -q`.

**Commit:** `git commit -m "feat(worker): add cancellable RPC task lifecycle"`

## Task 1.6：接入 UI-free application facade 并提供 WorkerHost 入口

**Files:**

- Create: `src/vibeocr/worker_host/handlers/ocr.py`
- Create: `src/vibeocr/worker_host/handlers/pdf.py`
- Create: `src/vibeocr/worker_host/handlers/qrcode.py`
- Create: `src/vibeocr/worker_host/handlers/settings.py`
- Create: `src/vibeocr/worker_host/main.py`
- Create: `tests/worker_host/test_handlers.py`
- Create: `tests/worker_host/test_process_lifecycle.py`
- Modify: `pyproject.toml`

**Red:** fake facades 验证 DTO 映射；真实子进程验证 `hello -> ready -> ping -> shutdown`；父进程消失、客户端断线和超时均应有界退出。

**Green:** 添加 console entry point `vibeocr-worker = vibeocr.worker_host.main:main`；handler 不 import PySide6；启动参数只接受 pipe、token、profile 和 parent PID。

```python
def main(argv: Sequence[str] | None = None) -> int: ...
```

**Verify:**

```powershell
uv run pytest tests/worker_host tests/contracts -q
uv run vibeocr-worker --self-test
```

Expected: all pass；self-test 输出单行 machine-readable JSON 且 exit 0。

**Commit:** `git commit -m "feat(worker): expose application services through WorkerHost"`

---

# Phase 2：WinUI 3 壳与平台能力

## Task 2.1：创建固定版本的 .NET solution

**Files:**

- Create: `global.json`
- Create: `Directory.Build.props`
- Create: `Directory.Packages.props`
- Create: `src/dotnet/VibeOCR.slnx`
- Create: `src/dotnet/VibeOCR.Contracts/VibeOCR.Contracts.csproj`
- Create: `src/dotnet/VibeOCR.Platform/VibeOCR.Platform.csproj`
- Create: `src/dotnet/VibeOCR.App/VibeOCR.App.csproj`
- Create: `tests/dotnet/VibeOCR.Contracts.Tests/VibeOCR.Contracts.Tests.csproj`
- Create: `tests/dotnet/VibeOCR.Platform.Tests/VibeOCR.Platform.Tests.csproj`
- Create: `src/dotnet/VibeOCR.Contracts/packages.lock.json`
- Create: `src/dotnet/VibeOCR.Platform/packages.lock.json`
- Create: `src/dotnet/VibeOCR.App/packages.lock.json`
- Create: `tests/dotnet/VibeOCR.Contracts.Tests/packages.lock.json`
- Create: `tests/dotnet/VibeOCR.Platform.Tests/packages.lock.json`

**Red:** `dotnet test src/dotnet/VibeOCR.slnx` 在项目不存在时失败。

**Green:** 固定 SDK `10.0.301`、Windows App SDK `2.2.0`；App 为 unpackaged x64 framework-dependent；Contracts/Platform 不依赖 WinUI；启用 nullable、warnings as errors、deterministic builds。首次执行 `dotnet restore src/dotnet/VibeOCR.slnx --use-lock-file` 生成并审查 `packages.lock.json`；随后所有本地/CI restore 只用 `--locked-mode`。

```xml
<TargetFramework>net10.0-windows10.0.17763.0</TargetFramework>
<WindowsPackageType>None</WindowsPackageType>
<SelfContained>false</SelfContained>
<RuntimeIdentifier>win-x64</RuntimeIdentifier>
```

**Verify:** `dotnet restore src/dotnet/VibeOCR.slnx --locked-mode` then `dotnet test src/dotnet/VibeOCR.slnx -c Release`；Expected: build/test success without package drift.

**Commit:** `git commit -m "build(winui): scaffold framework-dependent solution"`

## Task 2.2：生成 C# 契约并执行双语言 golden tests

**Files:**

- Create: `src/dotnet/VibeOCR.Contracts/RpcEnvelope.cs`
- Create: `src/dotnet/VibeOCR.Contracts/RpcMethods.cs`
- Create: `src/dotnet/VibeOCR.Contracts/Requests.cs`
- Create: `src/dotnet/VibeOCR.Contracts/Responses.cs`
- Create: `src/dotnet/VibeOCR.Contracts/SharedPayloadRef.cs`
- Create: `src/dotnet/VibeOCR.Contracts/ProtocolJsonContext.cs`
- Create: `tests/dotnet/VibeOCR.Contracts.Tests/GoldenContractTests.cs`
- Modify: `.github/workflows/release.yml`

**Red:** C# 逐项读取 `contracts/v1/golden.json`，序列化后做语义 JSON 比较；未知字段、版本和错误码负例必须失败。

**Green:** 使用 `System.Text.Json` source generation；C# enum 的 wire value 与 Python 完全一致；CI 同时运行 Python schema tests 和 C# golden tests。

**Verify:** `dotnet test tests/dotnet/VibeOCR.Contracts.Tests -c Release` and `uv run pytest tests/contracts -q`.

**Commit:** `git commit -m "feat(protocol): share golden contracts with C sharp"`

## Task 2.3：实现 WorkerHostClient 与崩溃诊断

**Files:**

- Create: `src/dotnet/VibeOCR.Platform/Worker/FrameCodec.cs`
- Create: `src/dotnet/VibeOCR.Platform/Worker/WorkerHostClient.cs`
- Create: `src/dotnet/VibeOCR.Platform/Worker/WorkerProcessSupervisor.cs`
- Create: `src/dotnet/VibeOCR.Platform/Worker/SharedPayloadClient.cs`
- Create: `tests/dotnet/VibeOCR.Platform.Tests/WorkerHostClientTests.cs`
- Create: `tests/dotnet/VibeOCR.Platform.Tests/WorkerProcessSupervisorTests.cs`

**Red:** fake pipe 覆盖半包/粘包、取消、deadline、断线、worker 非零退出和 stale event；真实 Python WorkerHost smoke test 标记 Windows integration。

**Green:** `WorkerHostClient.CallAsync<T>` 关联 request/task；`CancellationToken` 发送协议 cancel；supervisor 捕获 stdout/stderr 到滚动日志并限次重启只读操作。

```csharp
Task<TResponse> CallAsync<TRequest,TResponse>(string method, TRequest request, CancellationToken ct);
```

**Verify:** `dotnet test tests/dotnet/VibeOCR.Platform.Tests -c Release --filter Worker`；Expected: unit and Windows integration pass.

**Commit:** `git commit -m "feat(winui): connect to Python WorkerHost"`

## Task 2.4：实现 runtime/bootstrapper 与路径兼容

**Files:**

- Create: `src/dotnet/VibeOCR.Platform/Bootstrap/PrerequisiteDetector.cs`
- Create: `src/dotnet/VibeOCR.Platform/Bootstrap/PortableLayout.cs`
- Create: `src/dotnet/VibeOCR.Bootstrapper/VibeOCR.Bootstrapper.csproj`
- Create: `src/dotnet/VibeOCR.Bootstrapper/Program.cs`
- Create: `tests/dotnet/VibeOCR.Platform.Tests/PortableLayoutTests.cs`
- Create: `tests/dotnet/VibeOCR.Platform.Tests/PrerequisiteDetectorTests.cs`

**Red:** 覆盖 .NET Desktop Runtime/Windows App Runtime/WebView2/Python runtime 缺失、带空格路径、现有 runtime/model/output/config 目录和 winui-dev profile。

**Green:** bootstrapper 只检测、引导安装/修复并启动 App；不静默下载，不覆盖现有数据；App 与 Python `AppPaths` 对同一 fixture 的结果必须一致。

**Verify:** `dotnet test tests/dotnet/VibeOCR.Platform.Tests -c Release --filter "PortableLayout|Prerequisite"`.

**Commit:** `git commit -m "feat(winui): bootstrap framework and portable prerequisites"`

## Task 2.5：实现 WinUI 导航壳、诊断页与旁路 profile

**Files:**

- Create: `src/dotnet/VibeOCR.App/App.xaml`
- Create: `src/dotnet/VibeOCR.App/App.xaml.cs`
- Create: `src/dotnet/VibeOCR.App/MainWindow.xaml`
- Create: `src/dotnet/VibeOCR.App/MainWindow.xaml.cs`
- Create: `src/dotnet/VibeOCR.App/Views/DiagnosticsPage.xaml`
- Create: `src/dotnet/VibeOCR.App/ViewModels/DiagnosticsViewModel.cs`
- Create: `tests/dotnet/VibeOCR.App.Tests/VibeOCR.App.Tests.csproj`
- Create: `tests/dotnet/VibeOCR.App.Tests/packages.lock.json`
- Create: `tests/dotnet/VibeOCR.App.Tests/ShellTests.cs`

**Red:** view-model tests 验证 worker 未就绪、runtime 缺失、协议不兼容、修复动作和脱敏诊断导出。WinUI Unit Test App 验证导航和窗口可见。

**Green:** 首窗先显示，WorkerHost 后台连接；默认强制 `winui-dev` profile；诊断页展示 T0–T6、版本、协议、runtime 和 worker 状态。

**Verify:** `dotnet test src/dotnet/VibeOCR.slnx -c Release` plus WinUI Unit Test App on Win10/Win11 runner.

**Commit:** `git commit -m "feat(winui): add side-by-side shell and diagnostics"`

## Task 2.6：完成平台能力 spike

**Files:**

- Create: `src/dotnet/VibeOCR.Platform/Windows/SingleInstanceService.cs`
- Create: `src/dotnet/VibeOCR.Platform/Windows/GlobalHotkeyService.cs`
- Create: `src/dotnet/VibeOCR.Platform/Windows/TrayIconService.cs`
- Create: `src/dotnet/VibeOCR.Platform/Windows/ScreenCaptureService.cs`
- Create: `tests/dotnet/VibeOCR.Platform.Tests/WindowsPlatformTests.cs`
- Create: `tests/manual/platform-spike-checklist.md`

**Red:** 自动化覆盖 single-instance forwarding、热键注册冲突/释放、托盘 dispose、多屏负坐标与 125/150/200% DPI 坐标变换。

**Green:** 封装 Win32/WinRT，UI 不直接 P/Invoke；所有句柄实现 `IDisposable/IAsyncDisposable`；截图返回共享内存兼容 BGRA descriptor。

**Verify:** `dotnet test tests/dotnet/VibeOCR.Platform.Tests -c Release --filter WindowsPlatform`；人工清单在 Win10 1809 与当前 Win11 各通过一次。

**Commit:** `git commit -m "feat(winui): validate required Windows desktop capabilities"`

---

# Phase 3：单图 OCR 完整闭环

## Task 3.1：实现输入、OCR 会话与取消

**Files:**

- Create: `src/dotnet/VibeOCR.App/Features/Recognition/RecognitionViewModel.cs`
- Create: `src/dotnet/VibeOCR.App/Features/Recognition/InputService.cs`
- Create: `src/dotnet/VibeOCR.App/Views/RecognitionPage.xaml`
- Create: `tests/dotnet/VibeOCR.App.Tests/RecognitionViewModelTests.cs`
- Modify: `contracts/v1/golden.json`

**Red:** 文件、剪贴板、截图、拖放、重复启动、取消、worker crash、错误码本地化全部失败测试先行。

**Green:** view model 只依赖 `IWorkerHostClient` 与 input abstraction；会话 generation 丢弃迟到结果；输入二进制走 shared payload。

**Verify:** `dotnet test tests/dotnet/VibeOCR.App.Tests -c Release --filter Recognition` and `uv run pytest tests/worker_host -q`.

**Commit:** `git commit -m "feat(winui): implement cancellable single-image OCR"`

## Task 3.2：实现 WebView2 资源隔离与 typed bridge

**Files:**

- Create: `src/dotnet/VibeOCR.App/Web/PreviewHost.cs`
- Create: `src/dotnet/VibeOCR.App/Web/WebMessageRouter.cs`
- Create: `src/dotnet/VibeOCR.App/WebAssets/index.html`
- Create: `src/dotnet/VibeOCR.App/WebAssets/src/bridge.ts`
- Create: `src/dotnet/VibeOCR.App/WebAssets/src/preview.ts`
- Create: `src/dotnet/VibeOCR.App/WebAssets/package.json`
- Create: `src/dotnet/VibeOCR.App/WebAssets/package-lock.json`
- Create: `tests/dotnet/VibeOCR.App.Tests/WebMessageRouterTests.cs`
- Create: `tests/web/bridge.test.ts`

**Red:** 拒绝任意导航、外部脚本、未知 message/version、超大消息和 HTML 注入；bridge request/response correlation 测试先失败。

**Green:** 虚拟 host 只映射只读打包资源；CSP 禁止远程源和 inline script；消息用版本化 DTO，图片不走 JSON base64。

**Verify:** `npm ci --prefix src/dotnet/VibeOCR.App/WebAssets; npm test --prefix src/dotnet/VibeOCR.App/WebAssets; dotnet test tests/dotnet/VibeOCR.App.Tests -c Release --filter WebMessage`.

**Commit:** `git commit -m "feat(winui): host secure WebView2 preview bridge"`

## Task 3.3：迁移预览编辑与结果渲染

**Files:**

- Create: `src/dotnet/VibeOCR.App/WebAssets/src/editor/canvas.ts`
- Create: `src/dotnet/VibeOCR.App/WebAssets/src/editor/command-stack.ts`
- Create: `src/dotnet/VibeOCR.App/WebAssets/src/editor/geometry.ts`
- Create: `src/dotnet/VibeOCR.App/WebAssets/src/result/renderer.ts`
- Create: `src/dotnet/VibeOCR.App/WebAssets/src/result/sanitizer.ts`
- Create: `tests/web/editor.test.ts`
- Create: `tests/web/result-renderer.test.ts`
- Create: `tests/fixtures/parity/editor-cases.json`
- Modify: `src/dotnet/VibeOCR.App/Views/RecognitionPage.xaml`

**Red:** 用 fixture 覆盖缩放/旋转/裁剪/标注/撤销重做、Markdown、表格、公式、纯文本、XSS 和 Unicode；结果与现有 PySide 行为做语义比较。

**Green:** 编辑 command stack 保持可序列化；所有用户文本用 DOM text node/可信 sanitizer；WebView 状态变更经 typed bridge 返回 view model。

**Verify:** `npm test --prefix src/dotnet/VibeOCR.App/WebAssets`；Expected: editor/result parity fixtures 100% pass.

**Commit:** `git commit -m "feat(winui): migrate preview editor and result rendering"`

## Task 3.4：完成复制、导出与单图 E2E

**Files:**

- Create: `src/dotnet/VibeOCR.App/Features/Recognition/ResultActions.cs`
- Create: `tests/e2e/winui/single-recognition.spec.ps1`
- Create: `tests/fixtures/parity/single-recognition/input.png`
- Create: `tests/fixtures/parity/single-recognition/expected.json`
- Modify: `src/dotnet/VibeOCR.App/Views/RecognitionPage.xaml`
- Create: `docs/quality/feature-parity.md`

**Red:** clipboard busy 重试、文件覆盖确认、Unicode 路径、HTML/Markdown/text export、取消和错误提示测试先行。

**Green:** 导出业务仍由 Python facade；C# 只负责 picker/clipboard/命令状态；E2E 对同一 fixture 比较旧 UI 与 WinUI 的规范化结果和输出文件 hash/结构。

**Verify:** `powershell -File tests/e2e/winui/single-recognition.spec.ps1`；Expected: all single-image parity rows marked PASS.

**Commit:** `git commit -m "test(winui): close single-recognition parity loop"`

---

# Phase 4：完整功能对等

## Task 4.1：迁移批量识别

**Files:**

- Create: `src/dotnet/VibeOCR.App/Features/Batch/BatchItemViewModel.cs`
- Create: `src/dotnet/VibeOCR.App/Features/Batch/BatchViewModel.cs`
- Create: `src/dotnet/VibeOCR.App/Features/Batch/BatchCommands.cs`
- Create: `src/dotnet/VibeOCR.App/Views/BatchPage.xaml`
- Create: `tests/dotnet/VibeOCR.App.Tests/BatchViewModelTests.cs`
- Create: `tests/e2e/winui/batch.spec.ps1`
- Modify: `contracts/v1/golden.json`

**Tests/implementation:** 先覆盖队列排序、并发预算、单项/全部取消、进度、失败继续、导出和重启后不恢复临时任务；再通过 WorkerHost adapter 复用现有 batch service。迟到事件必须按 generation 丢弃。

**Verify:** `dotnet test tests/dotnet/VibeOCR.App.Tests/VibeOCR.App.Tests.csproj -c Release --filter Batch; powershell -File tests/e2e/winui/batch.spec.ps1`；Expected: unit 与 E2E 全通过，取消后无迟到 UI 更新。

**Commit:** `git commit -m "feat(winui): reach batch recognition parity"`

## Task 4.2：迁移二维码生成与识别

**Files:**

- Create: `src/dotnet/VibeOCR.App/Features/QrCode/QrCodeViewModel.cs`
- Create: `src/dotnet/VibeOCR.App/Features/QrCode/QrCodeCommands.cs`
- Create: `src/dotnet/VibeOCR.App/Views/QrCodePage.xaml`
- Create: `tests/dotnet/VibeOCR.App.Tests/QrCodeViewModelTests.cs`
- Create: `tests/e2e/winui/qrcode.spec.ps1`
- Modify: `contracts/v1/golden.json`

**Tests/implementation:** 覆盖图片/剪贴板输入、多码结果、无结果、URL 安全提示、二维码/条码生成和保存；Python service 保持算法真源。

**Verify:** `dotnet test tests/dotnet/VibeOCR.App.Tests/VibeOCR.App.Tests.csproj -c Release --filter QrCode; uv run pytest tests/services/test_qrcode_decode_service.py tests/services/test_qrcode_service.py -q; powershell -File tests/e2e/winui/qrcode.spec.ps1`；Expected: all pass。

**Commit:** `git commit -m "feat(winui): reach QR code parity"`

## Task 4.3：迁移 PDF 会话、缩略图和导出

**Files:**

- Create: `src/dotnet/VibeOCR.App/Features/Pdf/PdfViewModel.cs`
- Create: `src/dotnet/VibeOCR.App/Features/Pdf/PdfPageViewModel.cs`
- Create: `src/dotnet/VibeOCR.App/Features/Pdf/PdfCommands.cs`
- Create: `src/dotnet/VibeOCR.App/Views/PdfPage.xaml`
- Create: `src/dotnet/VibeOCR.App/WebAssets/src/pdf/viewer.ts`
- Create: `src/dotnet/VibeOCR.App/WebAssets/src/pdf/thumbnail-list.ts`
- Create: `tests/dotnet/VibeOCR.App.Tests/PdfViewModelTests.cs`
- Create: `tests/web/pdf.test.ts`
- Create: `tests/e2e/winui/pdf.spec.ps1`
- Modify: `contracts/v1/golden.json`

**Tests/implementation:** 覆盖打开/分页/缩略图虚拟化、旋转/删除/纠偏/OCR/文字层/保存/另存/导出、并发 mutation 拒绝、取消、崩溃后 session 失效。mutation 不自动重试。

**Verify:** `uv run pytest tests/services/test_pdf_*.py tests/integration/test_pdf_*.py -q; dotnet test tests/dotnet/VibeOCR.App.Tests/VibeOCR.App.Tests.csproj -c Release --filter Pdf; npm test --prefix src/dotnet/VibeOCR.App/WebAssets -- --runInBand; powershell -File tests/e2e/winui/pdf.spec.ps1 -Iterations 100`；Expected: 全通过且无句柄、进程、共享内存持续增长。

**Commit:** `git commit -m "feat(winui): reach PDF workflow parity"`

## Task 4.4：迁移设置、依赖安装和后端切换

**Files:**

- Create: `src/dotnet/VibeOCR.App/Features/Settings/SettingsViewModel.cs`
- Create: `src/dotnet/VibeOCR.App/Features/Settings/DependencyViewModel.cs`
- Create: `src/dotnet/VibeOCR.App/Features/Settings/BackendViewModel.cs`
- Create: `src/dotnet/VibeOCR.App/Views/SettingsPage.xaml`
- Create: `tests/dotnet/VibeOCR.App.Tests/SettingsViewModelTests.cs`
- Create: `tests/e2e/winui/dependencies.spec.ps1`
- Modify: `contracts/v1/golden.json`

**Tests/implementation:** 覆盖配置 schema round-trip、GPU 检测、runtime 安装/取消、CPU/GPU 切换、预热、网络/镜像错误、重启要求；安装与切换绝不自动重试。旁路 profile 测试再次确认正式数据未变。

**Verify:** `dotnet test tests/dotnet/VibeOCR.App.Tests/VibeOCR.App.Tests.csproj -c Release --filter Settings; uv run pytest tests/managers/test_dependency_manager.py tests/views/test_settings_*.py -q; powershell -File tests/e2e/winui/dependencies.spec.ps1 -Backend cpu`；GPU runner 另执行同一脚本的 `-Backend gpu`；Expected: 两种环境均通过。

**Commit:** `git commit -m "feat(winui): reach settings and backend parity"`

## Task 4.5：迁移托盘、快捷键、开机启动、关于和更新 UI

**Files:**

- Create: `src/dotnet/VibeOCR.App/Features/Shell/ShellViewModel.cs`
- Create: `src/dotnet/VibeOCR.App/Features/Shell/HotkeyViewModel.cs`
- Create: `src/dotnet/VibeOCR.App/Features/Shell/TrayCommands.cs`
- Create: `src/dotnet/VibeOCR.App/Views/AboutPage.xaml`
- Create: `src/dotnet/VibeOCR.App/Features/Update/UpdateViewModel.cs`
- Create: `src/dotnet/VibeOCR.App/Features/Update/UpdateCommands.cs`
- Create: `tests/dotnet/VibeOCR.App.Tests/ShellFeatureTests.cs`
- Create: `tests/e2e/winui/shell.spec.ps1`

**Tests/implementation:** 覆盖托盘显隐/退出、全局热键冲突与持久化、开机启动、单实例参数转发、版本/许可证/链接、检查更新/下载/校验/取消。此任务只迁移 UI 与 orchestration，不切换正式 updater 入口。

**Verify:** `dotnet test tests/dotnet/VibeOCR.App.Tests/VibeOCR.App.Tests.csproj -c Release --filter ShellFeature; powershell -File tests/e2e/winui/shell.spec.ps1`；Expected: automated tests pass，人工 accessibility/keyboard/high-contrast 清单全部签核。

**Commit:** `git commit -m "feat(winui): complete desktop shell parity"`

## Task 4.6：冻结功能对等矩阵

**Files:**

- Modify: `docs/quality/feature-parity.md`
- Create: `tests/parity/validate_matrix.py`
- Create: `tests/parity/feature-parity.schema.json`
- Modify: `.github/workflows/release.yml`

**Red:** validator 对缺少 owner、旧 UI 证据、新 UI 证据、自动化命令、状态或 `PASS` 以外切换候选行报错。

**Green:** 每一现有菜单、按钮、快捷键、输入、输出、错误和取消路径均一行；CI 执行矩阵 validator。Phase 4 可存在 `BLOCKED`，但 Phase 5 切换前必须全 `PASS`。

**Verify:** `uv run python tests/parity/validate_matrix.py docs/quality/feature-parity.md --require-pass`；Expected at Phase 4 end: exit 0 and 100% PASS.

**Commit:** `git commit -m "test(winui): enforce complete feature parity matrix"`

---

# Phase 5：兼容迁移、性能验收与正式切换

## Task 5.1：实现幂等配置与数据 migrator

**Files:**

- Create: `src/vibeocr/migration/__init__.py`
- Create: `src/vibeocr/migration/profile_migrator.py`
- Create: `tests/migration/test_profile_migrator.py`
- Create: `tests/fixtures/migration/v0-unversioned-minimal.json`
- Create: `tests/fixtures/migration/v0-unversioned-complete.json`
- Create: `tests/fixtures/migration/v1-current.json`
- Create: `src/dotnet/VibeOCR.Platform/Migration/ProfileMigrationClient.cs`
- Create: `tests/dotnet/VibeOCR.Platform.Tests/ProfileMigrationClientTests.cs`

**Red:** 覆盖每个历史 schema fixture、重复运行、部分失败、磁盘不足、只读文件、未知字段、快捷键冲突、旧 runtime/model/output 路径；原文件必须保持不变。

**Green:** 写临时文件、fsync、原子 replace；正式 config 增加 `schema_version`；先生成带 hash 的备份，再迁移；重复运行返回 already_migrated。备份只供修复读取，不提供旧 UI 启动。

**Verify:** Python/C# migration tests pass；所有 fixture 连续迁移两次结果字节一致。

**Commit:** `git commit -m "feat(migration): preserve existing portable user data"`

## Task 5.2：建立 framework-dependent unpackaged 发布布局

**Files:**

- Create: `scripts/build_winui_release.ps1`
- Create: `scripts/verify_winui_artifact.ps1`
- Modify: `scripts/bump_version.py`
- Modify: `.github/workflows/release.yml`
- Modify: `tests/test_bump_version.py`
- Create: `tests/build/test_winui_layout.py`

**Red:** fixture 验证 App/Bootstrapper/WorkerHost/runtime/model/config/output 布局；拒绝 .NET self-contained runtime、重复 WebView2 SDK、PySide6 UI 模块、开发 profile、测试/缓存/output 内容进入新 artifact。

**Green:** `dotnet publish --self-contained false -r win-x64`；Python worker 只包含 worker 所需模块，不包含 PySide6 UI；bootstrapper、WinUI App、WorkerHost 与 manifest 位于稳定相对路径；复用现有 Python runtime/model cache。

```powershell
dotnet publish src/dotnet/VibeOCR.App -c Release -r win-x64 --self-contained false
```

**Verify:** clean worktree 连续构建两次，解压文件清单和每文件 hash 一致（版本/签名时间戳除外）；artifact verifier exit 0.

**Commit:** `git commit -m "build(winui): package framework-dependent portable release"`

## Task 5.3：执行性能、体积和稳定性门禁

**Files:**

- Create: `scripts/benchmark_winui_startup.ps1`
- Create: `scripts/compare_release_metrics.py`
- Create: `tests/test_compare_release_metrics.py`
- Create: `docs/quality/baselines/winui-cutover.md`

**Red:** comparator 对样本少于 30、机器 fingerprint 不同、p95/体积数据缺失、两项改善均低于 30%、内存/句柄显著回退时报错。

**Green:** 同机、重启后、冷缓存条件分别采集旧/新各至少 30 次；比较 T0–T3、T0–T4、T0–T6、ZIP、解压体积、首窗 RSS、idle RSS、handle count。通过条件：ZIP 或冷启动 T0–T3 p95 至少改善 30%，且另一项不出现未批准显著回退。

**Verify:** `uv run pytest tests/test_compare_release_metrics.py -q; uv run python scripts/compare_release_metrics.py --old reports/local/python-startup.json --new reports/local/winui-startup.json --require-gate`；Expected: exit 0。

**Commit:** `git commit -m "perf(winui): prove cutover performance gate"`

## Task 5.4：切换 updater 与正式入口，不保留旧 UI 回退

**Files:**

- Modify: `scripts/updater_main.py`
- Modify: `scripts/update_replacer.py`
- Modify: `src/vibeocr/services/update_service.py`
- Modify: `tests/test_updater_main.py`
- Modify: `tests/test_update_replacer.py`
- Modify: `tests/services/test_update_service.py`
- Create: `tests/e2e/winui/upgrade.spec.ps1`
- Modify: `.github/workflows/release.yml`
- Modify: `scripts/bump_version.py`

**Red:** 覆盖旧版升级到切换版、迁移失败进入修复页、WinUI 启动健康失败进入修复页、文件占用、hash 错误和断电恢复；明确断言任何失败路径都不启动 `src/vibeocr/main.py` 或旧 PySide6 exe。

**Green:** 更新顺序固定为校验包 → 关闭旧进程 → 原子替换 → migrator → prerequisite check → WinUI health handshake → 正式启动。失败只进入 bootstrapper repair mode；保留数据和诊断包。

**Verify:** `uv run pytest tests/test_updater_main.py tests/test_update_replacer.py tests/services/test_update_service.py -q; powershell -File tests/e2e/winui/upgrade.spec.ps1 -FromVersion 0.4.22 -Archive dist/VibeOCR-*-win64.zip; ./scripts/verify_winui_artifact.ps1 -Archive dist/VibeOCR-*-win64.zip`；Expected: all pass，发布布局不存在旧 UI executable/launcher。

**Commit:** `git commit -m "feat(update): cut over permanently to WinUI shell"`

## Task 5.5：最终发布候选与切换审批

**Files:**

- Create: `docs/releases/winui-cutover-checklist.md`
- Modify: `docs/quality/feature-parity.md`
- Modify: `CHANGELOG.md`

**Steps:**

1. 从干净签名 worktree 构建 release candidate。
2. Win10 1809 x64 与当前 Win11 各执行安装/解压、runtime 缺失修复、旧版升级、全功能 E2E、重启、卸载/删除目录场景。
3. CPU-only 与受支持 GPU 环境各执行依赖安装、后端切换、预热、OCR/PDF/批量取消。
4. 验证所有用户数据、runtime、模型缓存、快捷键、历史输出可见且未改写原始历史文件。
5. 运行 8 小时稳定性 soak：循环 OCR/PDF、worker crash injection、休眠唤醒、网络中断；无孤儿进程/共享内存/句柄持续增长。

**Verify:**

```powershell
./scripts/run_phase0_gate.ps1
uv run pytest -q
dotnet test src/dotnet/VibeOCR.slnx -c Release
npm test --prefix src/dotnet/VibeOCR.App/WebAssets
uv run python tests/parity/validate_matrix.py docs/quality/feature-parity.md --require-pass
./scripts/verify_winui_artifact.ps1 -Archive dist/VibeOCR-*-win64.zip
```

Expected: all commands exit 0；矩阵 100% PASS；无旧 UI 回退入口；性能/体积门禁通过；审批清单由开发、测试和发布负责人签字。

**Commit:**

```powershell
git add docs/releases/winui-cutover-checklist.md docs/quality/feature-parity.md CHANGELOG.md
git commit -m "release: approve permanent WinUI cutover"
```

---

## 2. 每阶段评审与合并纪律

每个 Phase 结束时执行以下顺序，不得以口头确认代替证据：

1. `git status --short`：只允许该 Phase 的预期文件。
2. 运行该 Phase 所列全部测试，再运行受影响的既有回归套件。
3. 使用 `superpowers:requesting-code-review` 做代码评审；P0/P1 问题必须清零。
4. 使用 `superpowers:verification-before-completion` 重新运行最终门禁并记录原始输出。
5. 更新 `docs/quality/feature-parity.md` 和阶段基线，不允许把未完成项标成 PASS。
6. 合并后删除旁路分支前，确认构建产物和用户数据不在 Git 状态中。

## 3. 明确排除的错误方向

- 不改用 MAUI：目标仅 Windows，MAUI 增加抽象和包体但不能提升本项目的 Windows 桌面能力；WinUI 3 是正式目标。
- 不把全部 Python 业务一次性重写为 C#：先通过稳定 WorkerHost 契约隔离，后续按性能证据逐模块替换。
- 不采用 localhost HTTP 作为新控制面：避免端口、firewall 和鉴权复杂度；现有 PDF 内部 HTTP 可在 worker 内暂存，不能暴露给 WinUI。
- 不用 base64 传图片/PDF：大对象只能走共享内存或明确的受控文件路径。
- 不用 `QThread.terminate()`、`Thread.Abort`、强杀正常取消路径。
- 不将 .NET、Windows App Runtime 或 WebView2 Fixed Runtime打进 framework-dependent 主包；由 bootstrapper 检测和修复。
- 不因切换失败启动旧 UI；只进入修复流程并保全数据。

## 4. 完成定义

只有同时满足以下条件，本计划才算完成：

- Phase 0 当前问题全部修复且 Python 正式版持续可发布；
- WorkerHost 协议 v1 有跨语言 golden contract、取消、deadline、安全和资源回收证据；
- WinUI 3 在 Windows 10 1809 与 Windows 11 的 framework-dependent unpackaged 部署通过；
- 功能矩阵 100% PASS，包含所有失败、取消、更新和依赖管理路径；
- 既有 runtime、模型、配置、快捷键和历史输出完成幂等兼容迁移；
- 新包未包含旧 PySide6 UI，也没有用户可见或隐藏的旧 UI 回退入口；
- ZIP 体积或冷启动首窗 p95 至少改善 30%，稳定性指标无未批准回退；
- 最终全量测试、发布候选 E2E、soak 和人工平台检查均有可追溯证据。

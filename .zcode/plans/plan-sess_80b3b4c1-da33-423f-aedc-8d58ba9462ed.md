# 修复依赖更新检测：便携版检测不到 mineru 3.4.0 → 3.4.2 的更新

## 根因

`detect_dependency_updates()`（`src/vibeocr/env_manager.py:2372-2455`）把**已装版本**与 `>=3.4.0` 约束的**下界**比较，而非 uv.lock 中锁定的**实际版本** `3.4.2`：

```python
required_ver = _extract_lower_bound("mineru[core]>=3.4.0")  # → "3.4.0"
if Version("3.4.0") < Version("3.4.0"):  # False → 永远不报更新
```

锁定版本（3.4.2）记录在 `uv.lock`，但从未传入便携版运行时。便携用户只收到 `version.json`，而它只携带下界约束。

## 方案：保留 version.json + 新增锁定版本字段

打包时从 `uv.lock` 解析每个追踪包的锁定版本，写入 `version.json` 的新字段 `dep_locked_versions`。运行时检测优先用锁定版比较，缺失时回退到下界（向后兼容旧 version.json）。开发环境直接读 uv.lock。

## 改动清单

### 1. `scripts/bump_version.py` — 写入锁定版本

- **新增辅助函数** `_read_uv_lock_versions()`：用 `tomllib` 解析 `uv.lock`，返回 `{name: version}`（如 `{"mineru": "3.4.2", "torch": "2.12.1+cu126"}`）。处理 `paddlepaddle-gpu` 的存在（便携用 GPU 版）。
- **修改 `_generate_version_versions()`（约 line 959-970）**：调用上述函数，对 `dep_versions` 的每个 key（注意 `_KEY_ALIASES`：`paddlepaddle` 在 lock 里是 `paddlepaddle-gpu`，需反向查找），取锁定版本写入新字段 `dep_locked_versions`。仅写入找到的包（找不到则省略该 key，运行时回退下界）。
- 锁定版本可能带 local label（torch `2.12.1+cu126`）—— 直接原样写入，`packaging.version.Version` 能正确解析比较。

### 2. `src/vibeocr/env_manager.py` — 运行时优先用锁定版

**a) 新增 `_load_locked_versions() -> dict[str, str]`（紧邻 `_load_dep_specs` 之后）**：
- 打包态（无 pyproject.toml）：读 `version.json` 的 `dep_locked_versions` 字段。缺字段返回 `{}`（向后兼容）。
- 开发态（有 pyproject.toml）：直接解析 `uv.lock`（用与 bump_version 相同的逻辑；可抽到共享 util，或各自实现，因 dev 锁文件就在仓库根，简单解析即可）。`uv.lock` 不存在时返回 `{}`。
- 加缓存（仿 `_dep_specs_cache`）。

**b) 修改 `detect_dependency_updates()`（line 2372-2455）**：
- 调用 `locked = _load_locked_versions()`。
- 在循环内（line 2429 附近）：`required_ver = locked.get(pkg) or _extract_lower_bound(spec_str)`。优先锁定版，回退下界。
- 其余比较逻辑（`Version(...)` 比较）不变。

### 3. 测试（test_env_manager_install.py + test_bump_version.py）

**a) `detect_dependency_updates` 单元测试（当前零覆盖）**：
- `test_detect_update_when_locked_newer_than_installed`：锁定版 3.4.2 vs 已装 3.4.0 → 报更新（**回归用例，复现本次 bug**）。
- `test_no_update_when_installed_equals_locked`：3.4.2 vs 3.4.2 → 不报。
- `test_fallback_to_lower_bound_when_no_locked`：无 dep_locked_versions 字段 → 用下界 3.4.0（兼容旧 version.json）。
- `test_local_version_comparison`：已装 `2.12.1+cu126` vs 锁定 `2.12.1+cu126` → 不报（torch local label）。

**b) `_generate_version_json` 测试（test_bump_version.py:358 类内）**：
- `test_dep_locked_versions_from_uv_lock`：mock/构造 uv.lock fixture，断言 `dep_locked_versions["mineru"] == "3.4.2"`。
- 验证 `paddlepaddle` key 对应 `paddlepaddle-gpu` 的锁定版本。

### 4. 验证步骤
1. `uv run pytest tests/test_env_manager_install.py tests/test_bump_version.py -v` 全绿。
2. 手测：在开发环境模拟 portable（删 `_dep_specs_cache`、构造 version.json with `dep_locked_versions`），调用 `detect_dependency_updates` 确认 mineru 3.4.0→3.4.2 被检出。

## 不改动
- `version.json` 现有字段（version/dep_versions/dep_extras/removed/python_version）保持不变，新字段是纯新增。
- `update_replacer.py` 的 pending_sync 流程不涉及锁定版本（它只搬运 dep_versions diff），无需改动。
- `_load_dep_specs` 不变。
- 安装流程（`install_embedded_dependencies` 等）仍用 spec 约束串走 pip，不受影响——锁定版仅用于"是否需要更新"的检测。
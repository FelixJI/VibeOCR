# 管道缓存 TTL 重构与 Bug 修复设计

**日期**：2026-07-21
**类型**：功能增强 + Bug 修复
**影响范围**：`vibeocr-backend`（worker / services）、`vibeocr-client-py`（machine_cache / env_manager）、`vibeocr-pyside`（设置页 UI、ConfigManager）

---

## 一、背景与动机

### 1.1 现状问题

经代码调研发现 5 个独立问题：

1. **TTL 回收机制耦合于读超时**：`evict_idle()` 仅在 worker 主循环"处理完消息"或"`read_message(timeout=300)` 超时"两个分支触发。`DEFAULT_TTL_SECONDS=300` 与 `read_message(timeout=300.0)` 是两个独立的魔法数字，凑巧相等让默认场景看起来工作，但：
   - 用户改 TTL 为 1 分钟 → 实际仍 5 分钟才回收
   - 用户改 TTL 为 10 分钟 → 实际 15 分钟才回收（要等两个读超时周期）

2. **所有管道统一 TTL**：当前 `pipeline_ttl_seconds` 是单值，无法对轻管道（OCR/表格/公式）与重管道（PP-StructureV3/PaddleOCR-VL）做差异化策略。轻管道本应持久停留。

3. **MinerU 误入 FIFO 并存上限**：MinerU 是 HTTP 客户端（`httpx.post` 调本地 `mineru-api` 子进程），不占显存，但被 `get_heavy_pipelines()` 标为 `heavy=True`，挤占了 paddle 重管道的显存预算。

4. **文案误导**：`settings_page_controller.py:985` 的 toast/debug log 说"已刷新（依赖缓存 + 模型缓存）"，但 `refresh_cache` 实际不刷新任何"模型"。

5. **`refresh_cache` 名实不符**：`machine_cache.py:363-390` 只清空 `dependencies`/`hardware_info` 重写空壳，**不重新检测**。用户点"刷新"预期看到最新检测结果，实际只是清空了表格。

### 1.2 次要问题

- `.vibeocr/model_cache.json` 是历史遗留孤儿文件（866 字节），全代码库无引用，`CHANGELOG.md:1028,1061` 显示历史上有过 `model_cache` 模块，已被 `pipeline_cache_manager` + `machine_cache.pipeline_success` 取代。
- `generate_machine_id` 持 `_machine_id_lock` 跑 2 个 wmic 子进程（最长 10 秒），多处并发时排队阻塞。

### 1.3 决策小结

经 brainstorming 对齐的关键决策（全部用户确认）：

| 决策点 | 选择 |
|---|---|
| TTL 配置粒度 | **按管道单独配置**（6 个管道各有自己的 TTL） |
| 数据模型 | **方案 B：彻底重构为 `pipeline_ttls: dict[str, int]`**（废弃单值） |
| TTL 回收机制 | **后台定时线程**（30s tick，空缓存阻塞唤醒） |
| 持久语义 | 不受 TTL 回收，**但受 FIFO 并存上限约束** |
| RPC 协议演进 | **硬切**（不保留旧格式兼容分支） |
| 并存上限分档 | **≤8GB=1, >8GB=2, 未知=1, CPU=1** |
| MinerU 处置 | 不计入 `max_heavy`，TTL 默认持久 |
| Bug 修复范围 | 全部 4 个（文案/refresh_cache/锁争用/死文件） |

---

## 二、数据模型（Part 1）

### 2.1 配置存储格式

`app_settings.json` 新增字段：

```json
{
  "pipeline_ttls": {
    "OCR": 0,
    "TABLE_RECOGNITION": 0,
    "FORMULA_RECOGNITION": 0,
    "PP-StructureV3": 300,
    "MinerU": 0,
    "PaddleOCR-VL": 300
  }
}
```

字段语义：
- key：`OCRPipeline.value` 字符串（如 `"OCR"`、`"PP-StructureV3"`、`"MinerU"`）
- value：TTL 秒数，**`0 = 持久停留`**，`>0 = 闲置 N 秒后回收`

废弃字段：`pipeline_ttl_seconds`（单值）。

### 2.2 默认值策略

| 管道 | 默认 TTL | 理由 |
|---|---|---|
| 通用 OCR | 0（持久） | 轻模型，常驻收益高 |
| 表格识别 | 0（持久） | 轻模型 |
| 公式识别 | 0（持久） | 轻模型 |
| PP-StructureV3 | 300（5 分钟） | paddle 重模型，显存大户 |
| PaddleOCR-VL | 300（5 分钟） | paddle 重模型，显存大户 |
| MinerU | 0（持久） | HTTP 客户端，回收无益 |

### 2.3 ConfigManager API

```python
def get_pipeline_ttls() -> dict[str, int]
    """返回完整 6 管道 dict；缺失管道补默认值。"""

def set_pipeline_ttl(pipeline_name: str, ttl: int) -> bool
    """设置单个管道 TTL（0=持久）。"""

def set_pipeline_ttls(ttls: dict[str, int]) -> bool
    """批量设置。"""
```

废弃：`get_pipeline_ttl_seconds`、`set_pipeline_ttl_seconds`。

### 2.4 迁移逻辑

`_migrate_legacy_ttl()` 在 ConfigManager 初始化时自动执行：

- 旧字段 `pipeline_ttl_seconds` 存在：
  - 所有 paddle 重管道（PP-StructureV3、PaddleOCR-VL）继承该值
  - 所有轻管道（OCR、表格、公式）= 0（持久）
  - MinerU = 0（持久）
  - 迁移后从配置删除旧字段
- 完全新用户（无 `pipeline_ttls` 也无旧字段）：写入 2.2 节默认值
- 部分配置（`pipeline_ttls` 缺失某管道）：缺失项补默认值

---

## 三、回收机制与后台线程（Part 2）

### 3.1 MinerU 与 paddle 的本质差异

| 维度 | paddle 系 | MinerU |
|---|---|---|
| 缓存对象 | 进程内 `PaddleOCR`/`PPStructure` 对象 | `httpx` 客户端代理 + mineru-api 子进程 URL |
| 释放动作 | `del pipeline` + `paddle.device.cuda.empty_cache()` 真释放显存 | 仅 `del` 代理对象，mineru-api 进程仍运行 |
| 回收收益 | 释放数 GB 显存 | 几乎零收益 |
| 重建成本 | 数秒到数十秒 | 毫秒级（若 API 存活）；否则需重启 mineru-api（数十秒） |

结论：**MinerU 的 TTL 回收无意义，默认持久**；**MinerU 不占并存上限名额**（它是 HTTP 客户端，不占显存）。

### 3.2 OCRPipeline 元数据扩展（`pipelines.py`）

新增 `cache_kind` 字段区分回收路径：

```python
_PIPELINE_METADATA = {
    OCRPipeline.OCR:                 {..., "cache_kind": "paddle",  "heavy": False},
    OCRPipeline.TABLE_RECOGNITION:   {..., "cache_kind": "paddle",  "heavy": False},
    OCRPipeline.FORMULA_RECOGNITION: {..., "cache_kind": "paddle",  "heavy": False},
    OCRPipeline.PP_STRUCTURE_V3:     {..., "cache_kind": "paddle",  "heavy": True},
    OCRPipeline.PADDLEOCR_VL:        {..., "cache_kind": "paddle",  "heavy": True},
    OCRPipeline.DOCUMENT_PARSING:    {..., "cache_kind": "mineru",  "heavy": True},
}
```

新增辅助函数：

```python
def get_paddle_pipelines() -> list[OCRPipeline]
    """走 paddle 回收路径的管道（del + empty_cache）。"""

def get_mineru_pipelines() -> list[OCRPipeline]
    """走 mineru 回收路径的管道（仅移除代理）。"""
```

`get_heavy_pipelines()` 语义收窄为"**paddle 重管道**"——MinerU 不再进 `max_heavy` 计数。

### 3.3 后台 tick 线程设计

在 worker 子进程内新增独立守护线程，由 `PipelineCacheManager` 持有：

```python
class PipelineCacheManager:
    def __init__(
        self,
        service: OCRService,
        ttls: dict[str, int],
        max_heavy: int | None = None,
        tick_interval: float = 30.0,
    ):
        self._ttls = dict(ttls)
        self._stop_event = threading.Event()
        self._wakeup_event = threading.Event()
        self._tick_interval = tick_interval
        self._thread = threading.Thread(
            target=self._tick_loop,
            name="PipelineTTLWatcher",
            daemon=True,
        )
        self._thread.start()

    def _tick_loop(self) -> None:
        """每 tick_interval 秒做一次 evict_idle；空缓存时阻塞唤醒，不周期空转。"""
        while not self._stop_event.is_set():
            if not self._service._pipelines:
                # 空缓存：阻塞等新管道加载，避免周期空转
                self._wakeup_event.wait(timeout=60.0)
                self._wakeup_event.clear()
                continue
            try:
                self.evict_idle()
            except Exception as e:
                logger.warning("[CacheManager] tick evict_idle 失败: %s", e)
            # 可中断的 sleep
            self._stop_event.wait(self._tick_interval)

    def touch(self, pipeline_name: str, now: float | None = None) -> None:
        self._last_used[pipeline_name] = now if now is not None else time.time()
        self._wakeup_event.set()  # 唤醒后台线程

    def shutdown(self) -> None:
        self._stop_event.set()
        self._wakeup_event.set()
        self._thread.join(timeout=2.0)
```

### 3.4 `evict_idle` 重构

```python
def evict_idle(self, now: float | None = None) -> list[str]:
    now = now if now is not None else time.time()
    evicted = []
    for name in list(self._service._pipelines.keys()):
        ttl = self._ttls.get(name, 0)
        if ttl <= 0:
            continue  # 0 = 持久，跳过
        last = self._last_used.get(name, 0.0)
        if last + ttl < now:
            self._release_one(name)
            evicted.append(name)
    return evicted

def _release_one(self, pipeline_name: str) -> None:
    pipeline = self._service._pipelines.pop(pipeline_name, None)
    self._last_used.pop(pipeline_name, None)
    # 按 cache_kind 分流：paddle 才 empty_cache
    if pipeline is not None and self._is_paddle(pipeline_name):
        self._empty_cache()

def _is_paddle(self, pipeline_name: str) -> bool:
    from vibeocr.core.pipelines import get_paddle_pipelines
    return pipeline_name in {p.value for p in get_paddle_pipelines()}
```

### 3.5 主循环改造

`ocr_worker.py`：

- 删除 `:695-699`（消息处理后调 `evict_idle`）
- 删除 `:701-708`（读超时分支调 `evict_idle`）
- `read_message(timeout=300.0)`（`:289-291`）**保持不变**（这是性能/响应参数，与 TTL 解耦）
- `finally` 块（`:715` 附近）新增 `ocr_service.cache_manager.shutdown()` 避免线程泄漏

### 3.6 并存上限分档（`compute_max_heavy_by_vram`）

```python
VRAM_TIER_8GB = 8192

def compute_max_heavy_by_vram(total_vram_mb: int) -> int:
    """按显存计算 paddle 重管道并存上限。"""
    if total_vram_mb <= 0:
        return 1  # 未知，保守
    if total_vram_mb <= VRAM_TIER_8GB:
        return 1
    return 2
```

`_detect_max_heavy` 中 CPU 模式仍固定返回 1。

### 3.7 ttls 属性与校验

```python
@property
def ttls(self) -> dict[str, int]:
    return dict(self._ttls)

@ttls.setter
def ttls(self, value: dict[str, int]) -> None:
    validated = {}
    valid_names = {p.value for p in get_all_pipelines()}
    for name, ttl in value.items():
        if name not in valid_names:
            logger.warning("[CacheManager] 忽略未知管道 TTL: %s", name)
            continue
        validated[name] = max(0, int(ttl))
    self._ttls = validated
```

---

## 四、Worker RPC 协议变更（Part 3）

### 4.1 协议演进策略：硬切

不保留旧格式兼容分支。主进程与 worker 子进程配对启动，不存在跨版本通信。

"硬切"的精确含义：worker 只读取 payload 中的 `pipeline_ttls` 字段，**忽略**任何残留的旧 `ttl_seconds` 字段（不主动报错，旧字段自然失效）。新格式 `pipeline_ttls` 缺失或类型错误时返回 MSG_ERROR。

### 4.2 MSG_SET_TTL payload 新格式

```python
{
    "pipeline_ttls": {
        "OCR": 0,
        "TABLE_RECOGNITION": 0,
        "FORMULA_RECOGNITION": 0,
        "PP-StructureV3": 300,
        "MinerU": 0,
        "PaddleOCR-VL": 300
    }
}
```

废弃旧字段 `ttl_seconds`（单值）。

### 4.3 Worker 端处理（`ocr_worker.py:635-652`）

```python
elif msg_type == MSG_SET_TTL:
    import json
    try:
        payload = json.loads(data.decode("utf-8")) if data else {}
        ttls = payload.get("pipeline_ttls")
        if not isinstance(ttls, dict):
            raise ValueError("pipeline_ttls 缺失或非 dict")
        ocr_service.cache_manager.ttls = ttls
        logger.info("[Worker] 每管道 TTL 更新: %s", ttls)
        protocol.write_message(MSG_ACK, b"ok", sender="worker")
        protocol.wait_for_read(timeout=5.0)
    except Exception as e:
        logger.error("[Worker] 设置 TTL 失败: %s", e)
        protocol.write_message(MSG_ERROR, str(e).encode("utf-8"), sender="worker")
        protocol.wait_for_read(timeout=5.0)
```

### 4.4 MSG_CACHE_STATUS 响应升级

`status()` 返回值变化（`pipeline_cache_manager.py:213-225`）：

```python
def status(self) -> dict[str, object]:
    loaded = sorted(str(name) for name in self._service._pipelines)
    return {
        "pipeline_ttls": dict(self._ttls),  # 新：每管道
        "max_heavy": self._max_heavy,
        "loaded_pipelines": loaded,
        "last_used_unix_ms": {
            name: int(self._last_used[name] * 1000)
            for name in loaded
            if name in self._last_used
        },
    }
```

UI 设置页从 `status["pipeline_ttls"]` 读取来反映 worker 当前真实状态。

### 4.5 接口签名演进

| 旧签名 | 新签名 |
|---|---|
| `OCRService.set_pipeline_ttl(ttl_seconds: int)` | `OCRService.set_pipeline_ttls(ttls: dict[str, int])` |
| `OCRServiceSubprocess.set_pipeline_ttl(ttl_seconds: int)` | `OCRServiceSubprocess.set_pipeline_ttls(ttls: dict)` |
| `OCRWorkerProcess.set_ttl(ttl_seconds: int)` | `OCRWorkerProcess.set_ttls(ttls: dict)` |
| `worker_host/handlers/pipeline_cache.py` 的 `set_pipeline_ttl` | `set_pipeline_ttls` |
| `worker_host/composition.py:606` 的 `pipeline_ttl_seconds` | `pipeline_ttls` |
| `method_validation.py:585-622` schema 校验 | 同步更新 |

### 4.6 下发时机

1. **Worker 启动 / 重连时**：批量下发完整 `pipeline_ttls`（`subprocess_manager.py:181-185`）
2. **用户在设置页改某个管道 TTL**：触发 `_sync_configured_pipeline_ttl()` → 同样批量下发完整 dict
3. **preload_pipelines RPC**：原带 `ttl_seconds` 参数改为 `pipeline_ttls` 字典

---

## 五、UI 设计（Part 4）

### 5.1 布局

```
┌─ 管道缓存（内存/显存停留时间） ─────────────────────────┐
│                                                         │
│  模型停留时间：闲置后自动从显存/内存释放                  │
│  ─ 持久停留：除非手动释放或退出程序                       │
│  ─ N 分钟：闲置超时自动回收                               │
│                                                         │
│  通用 OCR          [持久停留 ▼]                          │
│  表格识别          [持久停留 ▼]                          │
│  公式识别          [持久停留 ▼]                          │
│  PP-StructureV3    [ 5 分钟 ▼]   ← paddle 重模型         │
│  文档P (VL)        [ 5 分钟 ▼]   ← paddle 重模型         │
│  文档M (MinerU)    [持久停留 ▼]   ← HTTP 客户端，回收无益 │
│                                                         │
│  [释放重模型] [全部释放]                                  │
│  运行时状态: 已加载 OCR, PP-StructureV3 · 上限 2/显存    │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

### 5.2 控件：每管道一个 QComboBox

下拉预设档（避免用户输入奇怪值）：

| 显示文本 | 对应 TTL 秒数 |
|---|---|
| "持久停留" | 0 |
| "1 分钟" | 60 |
| "3 分钟" | 180 |
| "5 分钟" | 300 |
| "10 分钟" | 600 |
| "15 分钟" | 900 |
| "30 分钟" | 1800 |

交互：用户选 ComboBox → `currentIndexChanged` → `ConfigManager.set_pipeline_ttl(name, value)` → RPC 下发完整 dict → worker 应用 → toast 反馈。

### 5.3 MinerU 行的特殊提示

MinerU 那行的 tooltip：
> "MinerU 是 HTTP 服务客户端，回收代理对象不释放底层进程资源。默认持久停留。改短 TTL 几乎无收益。"

### 5.4 与原有控件衔接

| 原控件 | 处置 |
|---|---|
| `spinPipelineTtl` + `chkEnablePipelineTtl` | **删除**——被 6 个 ComboBox 取代 |
| `_restore_pipeline_ttl_state`（`:1556-1571`） | 改名为 `_restore_pipeline_ttl_combos`，遍历 6 个 ComboBox 从 config 恢复 |
| `_on_pipeline_ttl_changed`（`:1573-1594`） | 改名为 `_on_pipeline_ttl_combo_changed(pipeline_name)`，单个管道触发 |
| `_sync_configured_pipeline_ttl`（`:1618`） | 保留，内部改为下发完整 dict |
| `btnReleaseHeavy` / `btnReleaseAll` | 保留不变 |
| `labelCacheStatus` | **拆分**为 `labelMachineCacheStatus`（环境检测状态）+ `labelPipelineCacheStatus`（管道运行时状态） |

---

## 六、Bug 修复细节（Part 5）

### 6.1 Bug 1：修误导文案

**位置**：`settings_page_controller.py:985`

```python
# 当前
logger.debug("[缓存] 已刷新（依赖缓存 + 模型缓存）")

# 改为
logger.debug("[缓存] 已刷新机器/依赖缓存")
```

toast 文案同步：
```python
# 当前
self._show_settings_toast("缓存已刷新")

# 改为
self._show_settings_toast("机器/依赖缓存已重置（下次启动时重新检测）")
```

### 6.2 Bug 2：`refresh_cache` 名实相符

**当前**（`machine_cache.py:363-390`）只清空 `dependencies`/`hardware_info` 重写空壳，不重新检测。

**修复**：让"刷新"真触发完整检测。改造调用链（`check_embedded_environment_dependencies` 的精确签名以代码现状为准，核心是传 `use_cache=False` 强制全量检测）：

```python
# settings_page_controller.py:_refresh_machine_cache_operation（改）
def _refresh_machine_cache_operation(self) -> tuple[bool, str]:
    """真正重检测：清缓存 → 触发完整环境检测 → 读回 cache info。"""
    from vibeocr import env_manager
    from vibeocr.machine_cache import clear_cache, get_cache_info

    # 1. 先清旧缓存，强制下次检测为"全量"
    clear_cache(self._project_root)
    # 2. 跑完整检测（耗时几十秒；在 QRunnable 后台线程里安全）
    #    以实际函数签名为准，关键是传 use_cache=False
    env_manager.check_embedded_environment_dependencies(
        self._project_root,
        use_cache=False,
    )
    return True, get_cache_info(self._project_root)
```

`refresh_cache` 函数本身**改名为 `reset_cache_to_empty`**（语义清晰：只重置不检测），供测试和迁移用。

**UI 反馈**（耗时几十秒，按钮 disable 期间显示进度）：
```python
self._update_cache_status("正在重新检测环境（可能需要数十秒）...")
```

### 6.3 Bug 3：`machine_id` 锁争用优化

**问题**：`generate_machine_id`（`machine_cache.py:145-170`）首次调用持 `_machine_id_lock` 跑 2 个 wmic 子进程（最长 10 秒），多处并发时排队阻塞。

**修复方案**：

1. 新增 `warmup_machine_id()` 函数：
```python
def warmup_machine_id(project_root: Path | None = None) -> None:
    """启动期后台预热机器码，避免后续 GUI 操作感知 wmic 延迟。
    
    安全在任何线程调用。若 _cached_machine_id 已设置则立即返回。
    """
    generate_machine_id()
```

2. MainWindow 启动序列已用 FunctionTask 后台跑 `is_cache_valid`（`main_window.py:748-797`），隐含预热——保持不变。

3. 调用点审查结论（全部安全，均在后台线程）：
   - `dependency_manager.py:45`：`DependencyCheckTask.run()` 由独立线程池后台执行 ✅
   - `main_window.py:767`：`FunctionTask(lambda: is_cache_valid(...))` 后台 ✅
   - `main_window.py:1152`：`_check_pending_backend`，调用方传后台快照，仅在 `cached_data=None` 兼容路径才同步调（保留作测试/独立调用回退）⚠️
   - `settings_page_controller.py:1528`：`_update_cache_status` 的 operation 闭包，由 `_run_cache_operation` 后台 ✅

无需重构调用点，仅需加注释明确"安全在任何线程"。

### 6.4 Bug 4：清理死代码文件

```bash
rm .vibeocr/model_cache.json
```

孤儿文件，全代码库无引用，未入 git。

---

## 七、测试策略（Part 6）

### 7.1 测试矩阵

| 层级 | 内容 | 工具 | 优先级 |
|---|---|---|---|
| A. Manager 单元测试 | TTL/FIFO/shutdown 行为 | pytest + fake service/clock | 高 |
| B. 配置迁移测试 | 旧字段 → 新字段迁移 | pytest + tmpdir | 高 |
| C. 后台线程测试 | tick/阻塞唤醒/shutdown | pytest + 注入短 tick | 中 |
| D. RPC 协议测试 | MSG_SET_TTL 序列化 | pytest | 中 |
| E. Bug 修复回归 | 每个 bug 一个用例 | pytest | 中 |
| F. UI 集成测试 | 手动验证清单 | 人工 | 低 |

### 7.2 A. Manager 单元测试

扩展 `tests/services/test_pipeline_cache_manager.py`，用 `FakeService` 替身避免加载真实 paddle：

```python
class FakeService:
    def __init__(self):
        self._pipelines = {}

class TestPipelineCacheManager:
    def test_persistent_pipeline_never_evicted_by_ttl():
        # ttl=0 的管道 evict_idle 不回收

    def test_ttl_evicts_after_expiry():
        # ttl=300 的管道超时后被回收

    def test_per_pipeline_independent_ttl():
        # 不同管道 TTL 独立，过期时间不同步

    def test_mineru_not_counted_in_max_heavy():
        # MinerU 不占并存上限名额

    def test_fifo_evicts_oldest_when_capacity_exceeded():
        # FIFO 按 last_used 升序淘汰最旧

    def test_release_only_calls_empty_cache_for_paddle():
        # 回收 MinerU 时不调 paddle.device.cuda.empty_cache()
```

关键点：`evict_idle(now=...)` 和 `enforce_capacity(new, now=...)` 已支持 `now` 注入，无需等待真实时间。

### 7.3 B. 配置迁移测试

```python
class TestTTLConfigMigration:
    def test_migrate_legacy_single_value(tmp_path):
        # 旧 pipeline_ttl_seconds=600 → 重管道全 600，轻管道全 0，MinerU=0

    def test_default_for_fresh_user(tmp_path):
        # 全新用户：轻=0, MinerU=0, paddle 重=300

    def test_partial_dict_filled_with_defaults(tmp_path):
        # 只配了部分管道，缺失项补默认值
```

### 7.4 C. 后台线程测试

注入短 tick 间隔（`tick_interval=0.05`）避免等待 30 秒：

```python
class TestBackgroundTickThread:
    def test_tick_evicts_after_ttl_when_idle():
        # 空缓存时线程阻塞，有管道后 30s 内触发回收

    def test_empty_cache_does_not_wake_periodically():
        # 空缓存时不周期唤醒（用 wakeup_event.wait 阻塞）

    def test_shutdown_joins_thread_cleanly():
        # shutdown 后线程在 2s 内退出
```

`PipelineCacheManager.__init__` 接受 `tick_interval` 参数（默认 30s，测试注入 0.05s）。

### 7.5 D. RPC 协议测试

```python
class TestMSG_SET_TTL_Payload:
    def test_new_payload_serialization():
        # pipeline_ttls 字典序列化/反序列化

    def test_worker_handles_new_payload():
        # worker 收到新格式 payload 后正确应用 ttls

    def test_hardcut_rejects_old_payload():
        # 硬切：旧格式 ttl_seconds 直接拒绝
```

### 7.6 E. Bug 修复回归

```python
class TestBugFixes:
    def test_refresh_cache_triggers_real_detection(tmp_path, monkeypatch):
        # Bug 2: refresh_cache 真触发检测

    def test_machine_id_warmup_caches_result(monkeypatch):
        # Bug 3: warmup_machine_id 调一次后不再跑 wmic
```

Bug 1（文案）和 Bug 4（删文件）不写自动化测试——前者靠代码审查，后者一次性手动删除。

### 7.7 F. UI 手动验证清单

```
[ ] 启动后设置页显示 6 个管道的 ComboBox，默认值正确
[ ] 改 OCR 为"5 分钟" → 立即下发 → worker 日志显示"TTL 更新"
[ ] 改 PP-StructureV3 为"持久" → 用 PP-StructureV3 OCR 一次 → 等待 10 分钟 → 模型仍在
[ ] 改 PP-StructureV3 为"1 分钟" → OCR 一次 → 等 70 秒 → 模型被回收（labelPipelineCacheStatus 更新）
[ ] MinerU tooltip 显示"HTTP 客户端，回收无益"
[ ] 8GB 卡用户：只能并存 1 个 paddle 重模型（手动切换测试）
[ ] 老用户配置文件含 pipeline_ttl_seconds → 启动后迁移为每管道，行为不变
```

### 7.8 现有测试的更新

- `tests/services/test_pipeline_cache_manager.py`——扩展，注入 `ttls` dict 代替单 `ttl_seconds`
- `tests/integration/test_pipeline_cache_lifecycle.py`——适配新协议
- 所有引用 `pipeline_ttl_seconds` 的测试——改用 `pipeline_ttls`

---

## 八、门控合规（项目强制要求）

本项目有两道门禁脚本（`scripts/run_phase0_gate.ps1`、`scripts/run_phase1_gate.ps1`）+ 多个架构守卫测试。本节列出本次改动必须满足的**所有门控点**，实施计划必须按此顺序验证。

### 8.1 Phase 0 门禁（`scripts/run_phase0_gate.ps1`）

四道阻断式检查，任一失败即阻断：

| 步骤 | 要求 | 本次改动关联 |
|---|---|---|
| `uv sync --frozen --group dev` | 锁文件同步 | 不改依赖，无影响 |
| `uv run pytest -q` | **0 failed** | 必须更新所有现有 TTL 相关测试（见第七节） |
| `uv run ruff check src tests scripts` | **0 errors** | 新代码须符合 ruff 规则（见 8.5） |
| `uv run pyright` | **0 errors** | 新代码须有完整类型注解；`reportUnusedImport="error"`、`reportDuplicateImport="error"` |

### 8.2 Phase 1 门禁（`scripts/run_phase1_gate.ps1`）

WorkerHost 契约 + 生命周期门禁，六道阻断式检查：

| 步骤 | 要求 | 本次改动关联 |
|---|---|---|
| `pytest tests/contracts tests/worker_host -q` | 0 failed | **必改**：methods.schema.json + golden.json（见 8.3） |
| `ruff check .../worker_host ...` | 0 errors | method_validation.py 改动须合规 |
| `pyright --pythonpath ... worker_host ...` | 0 errors | method_validation.py 改动须有类型注解 |
| `python -m vibeocr.worker_host.main --self-test` | 0 | WorkerHost 自检通过 |
| `dotnet restore ... --locked-mode` | 0 | C# 锁文件不破坏 |
| `dotnet test VibeOCR.Contracts.Tests -c Release` | 0 failed | **必改**：C# golden 须与 Python golden 一致（见 8.4） |

### 8.3 协议契约三方一致性（架构守卫强约束）

`tests/architecture/test_protocol_method_consistency.py` 强制三处 method table 完全一致：

1. `packages/vibeocr-contracts-py/src/vibeocr/protocol/v1/methods.schema.json` — JSON Schema 白名单
2. `src/dotnet/VibeOCR.Contracts/RpcMethods.cs` — C# `RpcMethods.All`
3. `packages/vibeocr-backend/src/vibeocr/worker_host/method_validation.py` — Python `PUBLIC_METHODS`

**本次改动保持 method 名 `pipeline_cache.set_ttl` 不变**（避免三处增删），只改 payload schema。具体改动点：

| 文件:行 | 当前内容 | 改为 |
|---|---|---|
| `methods.schema.json:621` (status response required) | `["ready", "ttl_seconds", "max_heavy", "loaded_pipelines", "last_used_unix_ms"]` | `["ready", "pipeline_ttls", "max_heavy", "loaded_pipelines", "last_used_unix_ms"]` |
| `methods.schema.json:624` | `"ttl_seconds": { "type": "integer", "minimum": 0 }` | `"pipeline_ttls": { "type": "object", "additionalProperties": { "type": "integer", "minimum": 0 } }` |
| `methods.schema.json:642-643` (set_ttl request) | `required: ["ttl_seconds"]`, `ttl_seconds: integer` | `required: ["pipeline_ttls"]`, `pipeline_ttls: object<str→int≥0>` |
| `methods.schema.json:647-650` (set_ttl response) | `required: ["updated", "ttl_seconds"]` | `required: ["updated", "pipeline_ttls"]` |
| `methods.schema.json:715,719` (settings.snapshot response) | `ttl_seconds: integer` | `pipeline_ttls: object<str→int≥0>` |
| `golden.json:588,601,608,677` | `"ttl_seconds": <int>` 样例 | 替换为 `"pipeline_ttls": {...}` 完整 6 管道样例 |
| `method_validation.py:580-590` (status response) | `_integer(p["ttl_seconds"], ...)` | 改为校验 `pipeline_ttls` 是 dict 且每 value≥0 |
| `method_validation.py:606-622` (set_ttl req/resp) | `_integer(p["ttl_seconds"], ...)` | 同上，dict 校验 |
| `method_validation.py:663-675` (`_response_settings` settings.snapshot) | `_integer(p["ttl_seconds"], ...)` | 同上，dict 校验 |
| `composition.py:193-194,606,615` (`SettingsSnapshot.ttl_seconds`) | `ttl_seconds: int` 字段 + 读取 `pipeline_ttl_seconds` | 改为 `pipeline_ttls: dict` + 读取 `pipeline_ttls` |

### 8.4 C# GoldenContractTests 同步

`tests/dotnet/VibeOCR.Contracts.Tests/GoldenContractTests.cs` 消费同一份 `golden.json`。改 `golden.json` 后：

- 重新跑 `dotnet test VibeOCR.Contracts.Tests` 确认 C# 端 golden 通过
- 若 C# 端有反序列化强类型（`TtlSeconds` 属性之类），须同步改名
- `RpcMethods.cs:27` 的 `SetPipelineCacheTtl = "pipeline_cache.set_ttl"` 常量名**保持不变**（method 名不变）

### 8.5 Ruff 规则合规

`pyproject.toml` 启用的关键规则（新代码易触犯）：

- `F` (Pyflakes)：未使用变量/导入 → `reportUnusedImport="error"` 拦截
- `B` (bugbear)：mutable default args（`RUF012` 已 ignore 类属性，但函数默认值仍查）
- `TCH` (type-checking)：TYPE_CHECKING 块外的 runtime import 会被建议移入
- `PTH` (use-pathlib)：优先 `Path` 而非 `os.path` 字符串操作
- `RET` (return)：所有路径 return 风格一致
- `COM` (commas)：trailing comma 强制

### 8.6 UI 线程阻塞架构守卫

`tests/architecture/test_ui_thread_blocking_boundaries.py` 用 AST 扫描指定 UI 入口函数，禁止调用特定阻塞函数（如 `_ensure_mineru_models_blocking`、`reset_cancel`）。

**本次新增/修改的 UI 入口函数**：
- `_on_pipeline_ttl_combo_changed`（新）：只允许调 `ConfigManager.set_pipeline_ttl` + `_run_cache_operation`（已有模式），不得直接调 `env_manager.*` 或同步 RPC
- `_refresh_machine_cache_operation`（改）：仍在 `_run_cache_operation` 后台执行，符合现有规则
- 实施时核查：若新函数被加入 UI 入口扫描规则，确保不引入禁用调用

### 8.7 其他架构守卫

- `test_backend_no_ui_imports.py`：backend 不得 import PySide6/Qt → 本次改动不引入
- `test_contracts_ui_free.py`：contracts 不得 import UI → `pipelines.py` 加 `cache_kind` 仍是 stdlib-only
- `test_worker_host_ui_free.py`：worker_host 不得 import UI → method_validation 改动不引入
- `test_workspace_physical_packages.py`：workspace 包结构 → 本次不动 workspace 配置

### 8.8 门禁自验证命令

实施过程中每个里程碑后，依次执行：

```powershell
# Phase 0（最频繁）
./scripts/run_phase0_gate.ps1

# Phase 1（改了 schema/golden 后必跑）
./scripts/run_phase1_gate.ps1

# 单独跑契约一致性守卫
uv run pytest tests/architecture/test_protocol_method_consistency.py -v

# 单独跑 UI 阻塞守卫
uv run pytest tests/architecture/test_ui_thread_blocking_boundaries.py -v
```

### 8.9 报告脱敏要求（易忽略）

`run_phase0_gate.ps1` 写报告后自检：**不得包含本机绝对路径**（`UserProfile`、绝对盘符路径）。本次新增测试若打印路径，须用 `Path.relative_to` 或 project_root 相对化。

---

## 九、风险与权衡

| 风险 | 缓解 |
|---|---|
| 后台线程崩溃导致永不回收 | tick_loop 内 `try/except` 包裹 evict_idle；线程是 daemon，worker 退出时强制结束 |
| 配置迁移破坏老用户 | 迁移逻辑有测试覆盖；迁移后旧字段从配置删除；默认值策略保守 |
| 硬切协议导致主/worker 版本不匹配 | 主/worker 配对启动，CI 构建保证一致性；不存在跨版本通信 |
| 后台线程在测试中难控制 | `tick_interval` 参数可注入；shutdown 有 timeout join |
| `refresh_cache` 真重检测耗时几十秒 | 后台线程执行；按钮 disable + 进度文案；用户已被告知 |
| **协议契约三方不同步**（新增） | 阶段 5 必须**原子提交**：schema + golden + method_validation + C# 同 commit |
| **Phase 1 门禁新增失败点**（新增） | method 名不变降低风险；payload schema 改动有 golden 测试兜底 |
| **C# golden 反序列化强类型**（新增） | 实施时先 grep C# 端 `Ttl` 相关属性，确认无强类型或同步改名 |

---

## 十、实施顺序（按门控分级）

按"风险递增 + 门控覆盖度递增"排序，每个里程碑结束跑对应门禁：

| 阶段 | 内容 | 完成后跑 |
|---|---|---|
| 1 | `pipelines.py` 加 `cache_kind` + 辅助函数（纯增量，向后兼容） | Phase 0 |
| 2 | `ConfigManager` 新 API + 迁移逻辑（保留旧 API 直到迁移完成） | Phase 0 |
| 3 | `PipelineCacheManager` 重构（每管道 TTL + 后台线程 + MinerU 分流） | Phase 0 |
| 4 | `ocr_worker.py` 删懒回收 + 加 shutdown + `_release_one` 分流 | Phase 0 |
| 5 | 协议契约三方同步（schema + golden + method_validation + C#） | **Phase 0 + Phase 1** |
| 6 | RPC 客户端层（`OCRWorkerProcess.set_ttls` + subprocess + composition） | Phase 0 |
| 7 | UI 层（6 ComboBox + label 拆分 + 文案修复） | Phase 0 + UI 守卫 |
| 8 | Bug 修复（refresh_cache + machine_id warmup + 删死文件） | Phase 0 |
| 9 | 全量回归 + 手动 UI 验证清单 | **Phase 0 + Phase 1 全绿** |

**关键约束**：阶段 5（协议契约）必须**一次性原子提交**——schema、golden、method_validation、C# golden 在同一个 commit，否则任一门禁单独跑都会红。

---

## 十一、附录

### 11.1 关键文件清单

**Python 后端**：
- `packages/vibeocr-contracts-py/src/vibeocr/contracts/pipelines.py`：元数据扩展（`cache_kind`）
- `packages/vibeocr-contracts-py/src/vibeocr/protocol/v1/methods.schema.json`：payload schema 升级
- `packages/vibeocr-contracts-py/src/vibeocr/protocol/v1/golden.json`：golden 样例升级
- `packages/vibeocr-backend/src/vibeocr/services/pipeline_cache_manager.py`：核心重构
- `packages/vibeocr-backend/src/vibeocr/services/ocr_service.py`：`get_or_create_pipeline` 联动
- `packages/vibeocr-backend/src/vibeocr/services/ocr_service_subprocess.py`：RPC 接口
- `packages/vibeocr-backend/src/vibeocr/services/ocr_worker_process.py`：RPC 客户端
- `packages/vibeocr-backend/src/vibeocr/workers/ocr_worker.py`：主循环改造 + shutdown
- `packages/vibeocr-backend/src/vibeocr/worker_host/composition.py`：启动初始化
- `packages/vibeocr-backend/src/vibeocr/worker_host/handlers/pipeline_cache.py`：handler
- `packages/vibeocr-backend/src/vibeocr/worker_host/method_validation.py`：schema 校验

**Python 客户端**：
- `packages/vibeocr-client-py/src/vibeocr/machine_cache.py`：Bug 2/3 修复
- `packages/vibeocr-client-py/src/vibeocr/env_manager.py`：refresh_cache 联动

**PySide UI**：
- `apps/vibeocr-pyside/src/vibeocr/managers/config_manager.py`：新 API + 迁移
- `apps/vibeocr-pyside/src/vibeocr/managers/subprocess_manager.py`：下发时机
- `apps/vibeocr-pyside/src/vibeocr/views/settings_page_controller.py`：UI + Bug 1

**C# 合约**：
- `src/dotnet/VibeOCR.Contracts/RpcMethods.cs`：method 名保持不变（确认无需改）
- `tests/dotnet/VibeOCR.Contracts.Tests/GoldenContractTests.cs`：跟随 golden.json

### 11.2 废弃项清单

**配置层**：
- `pipeline_ttl_seconds`（ConfigManager 字段，迁移后删除）

**Manager 常量**：
- `DEFAULT_TTL_SECONDS = 300`（单值常量）
- `VRAM_TIER_6GB`、`VRAM_TIER_12GB` 常量（改为 `VRAM_TIER_8GB`）
- `FALLBACK_MAX_HEAVY = 2`（改为 1）

**函数改名**：
- `machine_cache.refresh_cache` → `reset_cache_to_empty`
- `OCRService.set_pipeline_ttl` → `set_pipeline_ttls`
- `OCRServiceSubprocess.set_pipeline_ttl` → `set_pipeline_ttls`
- `OCRWorkerProcess.set_ttl` → `set_ttls`

**协议层（payload 字段）**：
- `ttl_seconds`（request/response 单值字段，全部位置）
- 替换为 `pipeline_ttls`（dict）

**死文件**：
- `.vibeocr/model_cache.json`（孤儿文件，删除）

### 11.3 门禁依赖的外部工具

实施前须确认本机具备（Phase 1 门禁要求）：
- `.venv/Scripts/python.exe`、`ruff.exe`、`pyright.exe`
- `$env:ProgramFiles/dotnet/dotnet.exe`
- `tests/dotnet/VibeOCR.Contracts.Tests/VibeOCR.Contracts.Tests.csproj`
- `NuGet.Config`

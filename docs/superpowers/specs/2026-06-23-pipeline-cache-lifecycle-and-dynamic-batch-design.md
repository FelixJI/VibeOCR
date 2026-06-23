# 管道缓存生命周期管理 + 动态批量大小 设计

> 日期：2026-06-23
> 状态：设计阶段
> 关联问题：8G 显存设备上 OCR 调用时显存被填满

## 1. 背景与问题

排查发现（见 `2026-06-23` 对话记录）：OCR 调用链本身是干净的——每次只调用 `options.pipeline` 指定的单一管道，没有"多余调用"。显存/内存被填满的根因是：

1. **预加载把多个重管道同时常驻**：启动后 `ConfigManager.get_preload_pipelines()` 指定的所有管道一次性加载到同一个 worker 子进程，互不释放。PP-StructureV3 / PaddleOCR-VL 各自内含布局检测/表格/公式/OCR 等多个子模型，叠加易撑爆显存。
2. **切换管道不卸载旧管道**：`OCRService._pipelines` 是纯缓存字典，只有 `_reset()`（测试用）和关闭时才清空。用户从 OCR 切到 PP-StructureV3，旧模型不从显存卸载。
3. **没有任何主动显存回收**：全代码无 `paddle.device.cuda.empty_cache()`。
4. **PDF 批量 `BATCH_SIZE=10` 固定**：与显存/内存无关，CPU 模式下 10 页 A4@300 ≈ 260MB+ 数组（含内部多份副本），小内存设备会溢出。

## 2. 设计目标

三项需求，统一在"管道缓存生命周期管理"主题下：

| # | 需求 | 核心机制 |
|---|---|---|
| 1 | 重管道并存上限，按显存分档，FIFO 淘汰 | worker 内 `PipelineCacheManager` |
| 2 | 重管道 TTL 闲置回收（默认 5 分钟，可配置）+ 释放按钮（单释放/全释放） | worker 内定时检查 + 设置页按钮经 RPC 触发 |
| 3 | PDF 批量 BATCH_SIZE 按资源动态缩放 | 内存/显存分离规则 |

## 3. 关键架构事实

- **管道缓存（`OCRService._pipelines`）和 PaddleX 模型显存都在 worker 子进程**（`vibeocr.workers.ocr_worker`）。
- **MinerU 是主进程内独立 `subprocess.Popen` 的本地 API 进程**（`MinerUService._api_process`），不在 worker 的 `_pipelines` 里，但占本地资源，纳入重管道管理。
- **主进程与 worker 之间是单条串行共享内存 RPC 通道**（worker 单线程主循环）。
- **`GPUMemoryMonitor`（pynvml）已存在**（`src/vibeocr/utils/gpu_memory_monitor.py`），能读 GPU total/free/used，有 `estimate_batch_size()`。当前只在 `batch_queue_manager`（批量识别 tab）使用，PDF 路径未用。
- **配置存储**：`ConfigManager` + `<project_root>/config/app_settings.json`，仿 `preload_enabled` 加字段即可。
- **设置页 UI**：`ui_main_window.py` 的 `pageModelManagement`（模型管理页），控件经 `settings_page_controller.py` 的 `findChild` 挂载。已有"立即预加载"按钮（`btnPreloadNow`）的后台线程模式可照搬。
- **`pynvml` 是项目依赖；`psutil` 不是**。读系统 RAM 用标准库 `ctypes`（Windows `GlobalMemoryStatusEx`），不引入新依赖。

## 4. 设计决策（已与用户确认）

1. **TTL 过期回收和 FIFO 淘汰在 worker 子进程内执行**（模型就地 del + empty_cache，无需跨进程 RPC 传递模型对象）。
2. **重管道并存上限按显存总量分档**（PP-StructureV3 + PaddleOCR-VL + MinerU 一起计数）。
3. **MinerU 纳入 TTL 回收 + 释放按钮范围**，并存计数也包含它（它独立进程，但 TTL/释放通过 `MinerUService.shutdown()`）。
4. **通用 OCR（轻管道）不受 TTL 回收**（默认管道、高频使用），但"全部释放"按钮会清掉它。
5. **CPU/GPU 模式的 BATCH_SIZE 规则分离**（分别约束 RAM / 显存）。

## 5. 详细设计

### 5.1 重管道定义

在 `src/vibeocr/core/pipelines/__init__.py` 的 `_PIPELINE_METADATA` 增加一个 `heavy` 布尔字段，标记需要纳入生命周期管理的重管道：

| 管道 | heavy | 说明 |
|---|---|---|
| OCR | False | 轻管道，默认常驻 |
| PP-StructureV3 | True | worker 进程内，占显存 |
| MinerU (DOCUMENT_PARSING) | True | 独立 Popen 进程，占本地资源 |
| PaddleOCR-VL | True | worker 进程内，占显存（最大） |
| TABLE_RECOGNITION | False | 轻量级独立表格识别 |
| FORMULA_RECOGNITION | False | 轻量级独立公式识别 |

提供查询函数 `get_heavy_pipelines() -> list[OCRPipeline]`。

### 5.2 并存上限规则（需求 1）

**显存总量分档**（PP-StructureV3 + PaddleOCR-VL + MinerU 一起计）：

| GPU 显存总量 | 重管道并存上限 |
|---|---|
| ≤ 6 GB | 1 |
| ≤ 12 GB | 2 |
| > 12 GB | 3 |

- CPU 模式（无 GPU）：固定上限 1（CPU 上重管道资源占用大，串行更稳）。
- 显存总量读取：复用 `GPUMemoryMonitor().get_status().total`；pynvml 不可用时回退到"假设 8GB"档（上限 2）。

**FIFO 淘汰**：当加载新重管道会导致并存数超过上限时，淘汰 `last_used` 最早的重管道（FIFO）。

### 5.3 TTL 闲置回收（需求 2）

**默认 TTL：300 秒（5 分钟）**，可在设置页调整（QSpinBox，范围 60-3600 秒）。

**执行时机**：worker 主循环（`ocr_worker.py` 的 `while True` 循环）在每次处理完一条消息后，顺带调用 `PipelineCacheManager.evict_idle(now)`。worker 单线程，不会被并发打断；`read_message` 的 300 秒长超时正好与 TTL 检查节奏匹配（超时返回后也检查一次）。

**回收动作**：
- PaddleX 管道（PP-V3/VL）：`del self._pipelines[name]` + `paddle.device.cuda.empty_cache()`（仅 GPU 模式）。
- MinerU：经新增 RPC 命令通知主进程 `MinerUService().shutdown()`（因为 MinerU 进程在主进程）。

**OCR 不受 TTL**：`evict_idle` 只检查 heavy 管道。

### 5.4 释放显存按钮（需求 2）

设置页 `groupPreload` 区域新增两个按钮：
- `btnReleaseHeavy`（"释放重管道"）：仅释放 heavy 管道（PP-V3/VL/MinerU），保留 OCR。
- `btnReleaseAll`（"全部释放"）：释放所有管道（含 OCR）。

**实现路径**（照搬 `btnPreloadNow` 的后台线程模式）：
1. 前置校验（OCR 就绪、subprocess 就绪）。
2. `QMessageBox.question` 二次确认（释放会中断正在进行的批量任务）。
3. 禁用按钮 + 状态 label 提示"正在释放…"。
4. 后台 `QRunnable` 调用：
   - 新增 RPC 命令 `MSG_RELEASE_PIPELINES`（携带 `heavy_only: bool`）发给 worker；
   - worker 内 `PipelineCacheManager.release(heavy_only)` 执行 del + empty_cache；
   - MinerU 部分由主进程直接调 `MinerUService().shutdown()`。
5. Qt Signal 回主线程恢复按钮、更新状态。

**额外入口**：主窗口工具栏/边缘工具栏可考虑加快捷释放按钮（可选，第一版仅设置页）。

### 5.5 动态 BATCH_SIZE（需求 3）

#### 5.5.1 规则（内存/显存分离）

`PdfOcrWorker.BATCH_SIZE` 从固定常量改为**根据模式动态计算**。

**单页峰值成本估算**：
- 每像素 3 字节（RGB），`predict(list)` 内部对每页复制多份（预处理/检测/识别/各阶段工作区）。
- GPU 模式放大系数 **5×**：A4@300 (8.7M 像素) ≈ 130MB/页峰值。
- CPU 模式放大系数 **8×**：A4@300 ≈ 210MB/页峰值（oneDNN 工作区 + 多线程缓冲更大）。

**GPU 模式**：
```
free_mb = GPUMemoryMonitor().get_status().free     # pynvml
per_page_peak_mb = avg_pixels * 3 * 5 / 1_048_576
batch = max(1, int(free_mb * 0.5 / per_page_peak_mb))
夹到 [1, 10]
```

**CPU 模式**：
```
free_mb = get_available_ram_mb()                   # ctypes, 无新依赖
per_page_peak_mb = avg_pixels * 3 * 8 / 1_048_576
batch = max(1, int(free_mb * 0.3 / per_page_peak_mb))
夹到 [1, 6]
```

- CPU 用 0.3 安全系数（要留 RAM 给系统/UI/Qt），上限 6（低于 GPU 的 10，因为 RAM 更紧张且与系统共享）。

#### 5.5.2 实测对照表

| 设备 | 模式 | 可用资源 | per_page_peak | 计算 batch | 取值 |
|---|---|---|---|---|---|
| 8G 显存 | GPU | free 6G | 130MB | 6144×0.5/130≈23 | **10** |
| 4G 显存 | GPU | free 3G | 130MB | 3072×0.5/130≈11 | **10** |
| 2G 显存 | GPU | free 1.5G | 130MB | 1536×0.5/130≈5 | **5** |
| 16G RAM | CPU | free 8G | 210MB | 8192×0.3/210≈11→夹6 | **6** |
| 8G RAM | CPU | free 4G | 210MB | 4096×0.3/210≈5 | **5** |
| 4G RAM | CPU | free 2G | 210MB | 2048×0.3/210≈2 | **2** |
| 2G RAM | CPU | free 1G | 210MB | 1024×0.3/210≈1 | **1** |

#### 5.5.3 avg_pixels 来源

`PdfOcrWorker.run` 在分批前，从已渲染的 `pages` 列表计算 `avg_pixels = mean(img.shape[0]*img.shape[1])`（渲染已完成，shape 已知，零额外开销）。

#### 5.5.4 新增 RAM 读取工具

`src/vibeocr/utils/system_memory.py`（新文件）：
```python
def get_available_ram_mb() -> int:
    """获取可用物理内存（MB），跨平台，仅用标准库。"""
    # Windows: ctypes.windll.kernel32.GlobalMemoryStatusEx（项目主要平台）
    # Linux: 读取 /proc/meminfo 的 MemAvailable
    # 其他平台 / 失败: 回退保守值 2048（2GB），保证 batch 至少为 1-2
```

### 5.6 PipelineCacheManager（worker 内新增组件）

`src/vibeocr/services/pipeline_cache_manager.py`（新文件），在 worker 子进程内实例化：

```python
class PipelineCacheManager:
    """管道缓存生命周期管理（worker 子进程内）。

    接管 OCRService._pipelines 的生命周期：
    - 记录每个重管道的 last_used 时间戳
    - FIFO 淘汰（超并存上限时）
    - TTL 闲置回收（evict_idle）
    - 显式释放（release）
    """

    def __init__(self, service, ttl_seconds=300, max_heavy=None):
        self._service = service          # OCRService 单例
        self._last_used: dict[str, float] = {}
        self._ttl = ttl_seconds
        self._max_heavy = max_heavy or self._compute_max_heavy()

    def touch(self, pipeline_name: str, now: float) -> None:
        """记录管道使用时间（每次 get_or_create_pipeline 后调用）。"""
        self._last_used[pipeline_name] = now

    def evict_idle(self, now: float) -> list[str]:
        """淘汰闲置超时的重管道，返回被释放的管道名列表。"""
        ...

    def enforce_capacity(self, new_pipeline: str, now: float) -> list[str]:
        """加载新重管道前，FIFO 淘汰至不超上限，返回被释放的列表。"""
        ...

    def release(self, heavy_only: bool) -> list[str]:
        """显式释放管道。heavy_only=True 只释放重管道。"""
        ...
```

**集成点**：
- `OCRService.get_or_create_pipeline` 在创建新管道后调用 `cache_manager.touch(name)` 和 `enforce_capacity(name)`。
- worker 主循环每次 `read_message` 返回后（含超时）调用 `cache_manager.evict_idle(time.time())`。
- `OCRService` 的 `_release_for_test` / 释放逻辑委托给 cache_manager。

### 5.7 配置项

在 `ConfigManager` 新增（仿 `preload_enabled` 模式，存 `app_settings.json`）：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `pipeline_ttl_seconds` | int | 300 | 重管道闲置回收 TTL（秒），范围 [60, 3600]，0=禁用 |
| `max_heavy_pipelines` | int | null | 手动覆盖并存上限（null=按显存自动分档） |

`AppSettings._DEFAULTS` 同步加这两个键。

### 5.8 设置页 UI 变更

`ui_main_window.py` 的 `groupPreload` 内，在 `btnPreloadNow` 下方新增：
- `QLabel "重管道闲置回收（分钟）"` + `QSpinBox spinPipelineTtl`（范围 1-60，单位分钟，显示用，存储转秒）。
- `QHBoxLayout` 放两个按钮：`btnReleaseHeavy`（"释放重管道"）、`btnReleaseAll`（"全部释放"）。
- 复用 `labelPreloadStatus` 显示释放结果。

`settings_page_controller.py` 新增：
- `connect_signals` 里 `findChild` + `connect` 新控件。
- `_restore_pipeline_ttl_state`：从 ConfigManager 恢复 spin 值。
- `_on_pipeline_ttl_changed`：保存到 ConfigManager；同时经 RPC 通知 worker 更新 TTL（新增 `MSG_SET_TTL` 命令）。
- `_on_release_heavy_clicked` / `_on_release_all_clicked`：照搬 `_on_preload_now_clicked` 模式。

### 5.9 新增 RPC 命令

在 `shared_memory_v2.py` 的 `MessageType` 枚举新增：
- `RELEASE_PIPELINES`（payload: `{"heavy_only": bool}`）→ worker 调 `cache_manager.release()`。
- `SET_TTL`（payload: `{"ttl_seconds": int}`）→ worker 更新 cache_manager 的 TTL。
- `RELEASE_MINERU` → 主进程直接调 `MinerUService().shutdown()`（无需 worker，主进程本地动作）。

`OCRServiceSubprocess` 新增对应方法 `release_pipelines(heavy_only)` / `set_pipeline_ttl(seconds)`，经 `_paddlex_manager.execute` 下发。

## 6. 错误处理

- **释放时正有批量任务在跑**：RPC 命令会排队（worker 单线程串行），等当前任务完成后再释放。释放按钮弹窗提示"释放将在当前任务完成后生效"。
- **pynvml 不可用**：并存上限回退到假设 8GB 档（上限 2）；BATCH_SIZE 的 GPU 分支回退到 CPU 规则（按 RAM）。
- **RAM 读取失败**：回退保守值 2048MB（batch≈1-2）。
- **empty_cache 失败**：捕获异常记日志，不中断（模型 del 已完成，显存最终会被 GC 回收）。
- **MinerU shutdown 失败**：捕获异常，不影响 PaddleX 管道释放。

## 7. 测试策略

| 组件 | 测试 |
|---|---|
| `PipelineCacheManager` | FIFO 淘汰（超上限淘汰最旧）、TTL 回收（超时释放、未超时保留）、release(heavy_only) 区分、OCR 不被 TTL 回收 |
| `system_memory.get_available_ram_mb` | Windows/Linux 路径、失败回退 |
| 动态 BATCH_SIZE | GPU 模式按显存、CPU 模式按 RAM、夹到上下限、avg_pixels 计算 |
| 显存分档上限 | 6G/12G/24G 各档正确、CPU 固定 1、pynvml 不可用回退 |
| ConfigManager 新字段 | 读写 `pipeline_ttl_seconds` / `max_heavy_pipelines`、默认值 |
| RPC 新命令 | RELEASE_PIPELINES / SET_TTL 往返、worker 内正确执行 |
| 设置页 | TTL spin 保存/恢复、释放按钮触发 RPC、二次确认弹窗 |

## 8. 实施顺序（建议）

1. **基础设施**：`system_memory.py` + RAM 读取测试。
2. **动态 BATCH_SIZE**（需求 3，最独立）：改 `PdfOcrWorker` + 测试。可单独验证。
3. **PipelineCacheManager + TTL/FIFO**（需求 1+2 核心）：新文件 + worker 集成。
4. **RPC 命令**：RELEASE_PIPELINES / SET_TTL + worker 处理。
5. **ConfigManager 新字段**。
6. **设置页 UI**：TTL spin + 释放按钮 + controller 逻辑。
7. **MinerU 集成**：TTL/释放纳入 MinerUService.shutdown()。

## 9. 不在本次范围

- 工具栏快捷释放按钮（第一版仅设置页）。
- 显存使用量实时显示 UI（仅内部用 GPUMemoryMonitor）。
- 预加载列表与并存上限的联动校验（预加载时若超过上限，仅记日志警告，不阻断）。

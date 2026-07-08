# PDF 文字层处理：2 Bug 修复 + 3 性能优化

**核心原则（贯穿性能优化）：** 传输层（共享内存）必须 ≥ 计算层（GPU 一次能 predict 的张数），让传输永远不卡计算。SHM 10MB 还是 100MB 对系统内存影响微不足道，但卡住就是浪费 GPU。

当前三层批的失衡：计算批=8（GPU 显存决定，`pipeline_ocr.py:51` `text_recognition_batch_size=8`）｜传输批≈3（`budget=0.7×16MB≈11.8MB`，`ocr_service_subprocess.py:308`）｜页批=16（硬编码）。**传输批(3) < 计算批(8) → GPU 永远喂不饱，这是慢的主因之一。**

---

## 第一批：2 个 UI Bug（低风险、改动小）

### Bug A — 取消识别后已识别页格子颜色不刷新

**根因：** `_cancel_ocr`（`pdf_session_manager.py:810-817`）用裸 `w.wait(5000)` 阻塞，不 spin Qt 事件循环 → 取消前那一刻发出的 `page_done` 信号搁置在主线程队列。而 `_on_ocr_finished`（`pdf_tab.py:995-1006`）刻意不全量重建网格（注释 1002-1003 说"逐页已变绿"），该假设在取消时破产。

**修复（两处，互补）：**

1. **`pdf_session_manager.py` `_cancel_ocr`** — 裸 `w.wait(5000)` 换成已有的 `_wait_thread(worker, timeout_ms)`（`:983-1005`），后者 spin `QCoreApplication.processEvents()`，让排队信号排干。保留 5s 超时语义（显式传 5000，超时 `terminate()` 兜底——取消是用户主动行为，可接受）。

2. **`pdf_tab.py` `_on_ocr_finished`** — 加兜底：新增 `_sync_layer_grid_from_model()`，遍历 `_layer_status_grid` 现有 item（不 clear+重建，保留选中），按各 item 的 `_LAYER_ROLE`（page_index）从 `session.pdf_document.get_page(idx)` 重读 `has_text_layer`/`deskewed` 并 `setData`。在 `_on_ocr_finished` 末尾调用（替代仅刷新 summary label）。这样无论信号是否丢失，格子颜色一定与 model 一致。

**测试：**
- 扩展 `tests/integration/test_pdf_ocr_orchestration.py::test_cancel_ocr`（当前只断言 `is_ocr_running is False`）：用 mock OCR 让首页写层成功，多页任务中途 cancel；断言 `ocr_done` 触发 + 已成功页 `pdf_document.get_page(idx).has_text_layer is True`。
- 新增 `_sync_layer_grid_from_model` 单测：混合 has_text_layer 的 model，调一次后断言各 item `_HAS_LAYER_ROLE` 与 model 一致、选中状态未被清。

### Bug B — 未识别格子预览 bbox 残留

**根因：** `PreviewCanvas.set_pixmap`（`pdf_preview_window.py:78-85`）不清除 `_ocr_blocks`/`_highlight_layers`。从"有 OCR 块的页 A"切到"无文字层页 B"，旧 bbox 按页 B 尺寸重算后画出。`set_highlight_layers`（`:158`）会 `_clear_ocr_blocks()`，但 `set_pixmap` 两个源都不清——不对称。

**修复：** `set_pixmap` 内清空所有高亮数据（与 `set_highlight_layers` 对称）：
```
def set_pixmap(self, pixmap):
    self._pixmap = pixmap
    self._scale = 1.0
    self._clear_ocr_blocks()
    self._highlight_layers = []
    self._page_rect = None
    self._update_size()
    self.update()
```
- 去掉原 `if self._ocr_blocks is not None: self._compute_ocr_block_rects()`（裸 pixmap 不应有残留 bbox）。
- `set_ocr_blocks`（`:112-118`）直接赋 `_pixmap` 不走 `set_pixmap`，不受影响。
- `set_highlight`（`:497-509`）先 `set_pixmap`（清空）再 `set_highlight_layers`（重设），顺序安全。

**测试：** `tests/views/test_pdf_preview_window.py` 新增：
- `test_set_pixmap_clears_ocr_blocks`：`set_ocr_blocks` → `set_pixmap` → 断言 `_ocr_blocks is None`、`_highlight_layers == []`。
- `test_set_pixmap_after_highlight_clears`：`set_highlight_layers` → `set_pixmap` → 断言 `_highlight_layers == []`、`_page_rect is None`。

---

## 第二批：性能优化

### 性能 1 — 消除 PNG 双重编解码（高收益、低风险、零协议改动）

**已验证：** IPC 对图像是 opaque length-prefixed bytes（`shared_memory_v2.py:508-512`），worker `_to_ndarray`（`ocr_service.py:966-978`）用 `PIL.Image.open` 自动识别格式，`_prepare_image_data`（`:419-420`）对 bytes 输入原样返回。让 `_render_page` 返回原始 PNG bytes 直接喂 `recognize_batch`，即跳过主进程的 PNG 解码 + 重编码（省 2 次压缩），worker 仍解码 1 次。

**改动（仅 `pdf_session_manager.py`）：**
- `_render_page`（`:703-711`）：返回 `bytes | None`；删掉 `Image.open(...).convert("RGB")` + `np.array(...)`，直接 `return png`。清理不再需要的 import（grep 确认 `np`/`Image` 在 `_run_ocr` 内其他处是否还用）。
- `images`（`:719`）、`valid_images`（`:734`）类型注解 → `bytes`；`recognize_batch` 无需改（docstring `:279` 已声明接受多格式）。
- 失败仍返回 `None`（不要 `b""`，否则 worker 解码空 PNG 报错）；过滤逻辑（`:728-731`）不变。
- RGB 转换移到 worker `_to_ndarray`（`:976-977` 已 convert），行为一致（后端本就 `alpha=False` RGB）。

**测试：** 单测 `_render_page` 返回合法 PNG bytes；扩展 OCR e2e 确认 bytes 路径识别成功。

### 性能 2 — SHM 128MB，让传输永远不卡计算（核心修复）

**这一项直接针对你提的问题。** 重新校准：要让 SHM 单条消息预算 `0.7 × (SHM − 9)` ≥ 一个完整页批（16 张）的 PNG 总量。

- 单张 300dpi A4 PNG 上限 ≈ 4MB；16 张 = 64MB；`64MB / 0.7 ≈ 91MB`。
- **SHM 设 128MB**，`budget = 0.7 × 128MB ≈ 90MB` → 一条消息轻松装下 16 张，传输批从 ~3 张跃升到完整页批，**彻底消除"被 SHM 卡脖子"**。
- 系统内存代价：+112MB（16→128），对现代机器可忽略。你已确认这个取舍。

**改动：**
- `constants.py:54` `DEFAULT_SHM_SIZE = 128 * 1024 * 1024`（128MB）。
- 更新 `pdf_session_manager.py:670` 注释与 `ocr_service_subprocess.py:307` 注释，说明三层批关系与"传输≥计算"原则。
- `_OCR_BATCH_SIZE` 保持 16（正好 = 2 个 GPU 计算批 8×2，喂满 GPU 且不让单批 predict 超时）。不盲目调大，因为页批还要兼顾渲染/写层的内存与延迟。

**不动 `gpu_memory_monitor.estimate_gpu_batch_size`（死代码）：** 那是按 GPU 显存算 predict 批（且有 `GPU_BATCH_CAP=10`），与 SHM 传输批是两个概念。SHM 切分逻辑（`ocr_service_subprocess.py:307-329`）已就位，只要 budget 够大就不会切。计算批已由 `pipeline_ocr.py:51` 固定 8，无需动态化。

**测试：**
- `tests/utils/test_shared_memory.py`：新增大消息（如 90MB）write/read 往返。
- `tests/services/test_ocr_service_subprocess.py`（若存在）或 manager 单测：验证 128MB budget 下，16 页批不再被切（sub_batch 计数=1）。
- 回归全部 OCR 测试。

### 性能 3 — 流水线化（中收益、较高风险，最后做，可暂缓）

当前每批 `[渲染16页][==OCR==][写层16页]` 串行，GPU 在渲染+写层时空闲。目标：让"批 N 写层"与"批 N+1 渲染+OCR"重叠。

**方案：** 把写文字层从 runner 线程同步循环抽出到独立 `_TextLayerWriter` 线程 + `queue.Queue`：
- runner：渲染 → OCR → 推 `(page_index, result)` 入队 → 立即进下一批渲染。
- writer 线程：串行 `add_text_layer`（fitz 写不可并发），完成后发 `page_done`/`progress`（"变绿"对应真正写层完成，更准确）。
- 取消：设 flag，runner 跳过后续批；writer drain 完已入队项后退出（塞 sentinel）。
- `all_done`：runner 退出 **且** writer 队列空（join writer）后才发；`get_model` 刷新在 writer 全部写完后。
- Bug A 的 `_sync_layer_grid_from_model` 兜底仍适用。

**风险：** 取消时序（runner 退出但 writer 还在写）、model 刷新必须等 writer join。改动较大，独立 commit + 充分 e2e。**性能 1+2 后若速度已达标，可与用户确认是否仍需要。**

---

## 实施顺序与验证

1. **Bug A + Bug B**（独立提交）→ `tests/views/test_pdf_preview_window.py` + `tests/integration/test_pdf_ocr_orchestration.py` + `tests/managers/test_pdf_session_manager.py`。
2. **性能 1**（PNG 直传）→ OCR e2e + 新单测。
3. **性能 2**（SHM 128MB）→ shared_memory 测试 + 回归全部 OCR 测试。
4. **性能 3**（流水线）→ 独立提交 + e2e；性能 1+2 后评估是否仍需要。

每批提交前全量 `pytest`（tests/views, tests/integration, tests/managers, tests/services, tests/utils, tests/workers）。

## 不改动
- IPC 协议（`shared_memory_v2.py` 序列化）——保持 opaque bytes。
- `_to_ndarray`（worker 解码）——PIL 自动识别，无需改。
- 后端 `render_preview` 仍返回 PNG（HTTP 传输需序列化；改 raw pixel 需新端点，收益<复杂度）。
- `gpu_memory_monitor` 死代码——PDF 路径不用它。
- `_OCR_BATCH_SIZE=16`——正好对齐 2 个 GPU 计算批。
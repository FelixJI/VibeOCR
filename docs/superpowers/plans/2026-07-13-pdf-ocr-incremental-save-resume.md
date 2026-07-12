# PDF OCR 逐批增量落盘 + 断点续传 + UI 进度细化 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 OCR 文字层逐批增量落盘（崩溃只丢最后一批）+ 末尾自动聚合压缩（体积不膨胀）+ sidecar 断点续传，并把 UI 格子从二态扩为四态、预览数据随进度增量写入。

**Architecture:** 在现有 `_run_ocr` 三阶段批处理（渲染→识别→写层）的阶段 3 后追加 incremental save + sidecar 更新；批循环结束后追加整文档聚合压缩；新增 `utils/ocr_sidecar.py` 模块管理断点续传状态（指纹校验 + 原子写）；`BatchAddTextLayerRequest` 加 `save` 字段让写层+落盘一次 HTTP 完成；manager 的 `_on_ocr_page_done_signal` 增量落 model 消除预览滞后；`LayerStatusDelegate` 扩为四态。

**Tech Stack:** Python 3.13, PySide6 (Qt), FastAPI + httpx (后端子进程 IPC), PyMuPDF (fitz), pydantic (schemas), pytest (qt_api=pyside6)

## Global Constraints

- 纯加文字层 OCR 不触发 `has_structural_change`（只有删页/插页/重排/移动会，见 `pdf_service.py:458/471/486/504/525`）→ OCR 续传场景天然走 `incremental=True` 增量分支。
- `incremental=True`（`pdf_service.py:248-256`）不重开 doc，内存对象可继续用于下一批 OCR；全量压缩 `_compress_in_place`（`:138-173`）必须 close/reopen，只能放在所有 OCR 完成后。
- `_run_ocr` 是纯执行层，信任传入的 `pages` 列表，内部不做 has_text_layer 过滤 → 续传过滤接在 `start_ocr`（`:712`）入口，不进 `_run_ocr`。
- sidecar 复用 `machine_cache.get_cache_dir(project_root)` → `<install_root>/.vibeocr/ocr_sessions/<fingerprint>.json`，原子写复用 `save_cache` 的 tmp+replace 模式。
- sidecar 和 incremental save 都是"尽力而为"——失败时降级记日志，不阻断 OCR 主流程。
- spec 文档：`docs/superpowers/specs/2026-07-13-pdf-ocr-incremental-save-resume-design.md`
- commit message 用中文描述 + 英文 type 前缀（与现有 commit 风格一致，见 `git log`）。

---

## File Structure

| 文件 | 职责 | 新建/修改 |
|---|---|---|
| `src/vibeocr/utils/ocr_sidecar.py` | sidecar 读写：指纹、原子写、版本、增量合并页 | **新建** |
| `tests/utils/test_ocr_sidecar.py` | sidecar 单元测试 | **新建** |
| `src/vibeocr/services/pdf_service.py` | 新增 `save_incremental` 静态方法 | 修改 |
| `tests/services/test_pdf_service_save_incremental.py` | `save_incremental` 单元测试 | **新建** |
| `src/vibeocr/ipc/schemas.py` | `BatchAddTextLayerRequest` 加 `save` 字段；`ProgressPhase` 加 `COMPRESS` | 修改 |
| `src/vibeocr/services/pdf_backend_process.py` | `add_text_layer_batch` 路由加 `save` 分支 | 修改 |
| `src/vibeocr/services/pdf_backend_client.py` | `add_text_layer_batch` 加 `save` 参数 + 解析 `extra.saved` | 修改 |
| `src/vibeocr/managers/pdf_session_manager.py` | `_run_ocr` 阶段3加 incremental save + sidecar；末尾加聚合压缩；`_on_ocr_page_done_signal` 增量落 model；`start_ocr` 续传过滤 | 修改 |
| `tests/managers/test_pdf_session_manager.py` | 追加：增量落盘/sidecar/续传测试 | 修改 |
| `src/vibeocr/views/tabs/pdf_tab.py` | `LayerStatusDelegate` 四态；`_on_ocr_page_result` 加预览刷新；格子状态转换；续传提示 | 修改 |

依赖顺序：Task 1（sidecar）→ Task 2（save_incremental）→ Task 3（schema）→ Task 4（后端路由）→ Task 5（client）→ Task 6（manager 编排）→ Task 7（UI 四态+预览）。Task 1-2 互相独立可并行；Task 3-5 串行；Task 6 依赖 1+2+5；Task 7 依赖 6。

---

### Task 1: Sidecar 读写模块

**Files:**
- Create: `src/vibeocr/utils/ocr_sidecar.py`
- Test: `tests/utils/test_ocr_sidecar.py`

**Interfaces:**
- Produces: `compute_fingerprint(file_path: str) -> str`、`sidecar_path(file_path: str) -> Path`、`load_sidecar(file_path: str) -> dict | None`、`save_sidecar(file_path: str, data: dict) -> bool`、`mark_pages_saved(file_path: str, page_indices: list[int], angles: dict[int, int]) -> bool`、`mark_completed(file_path: str) -> bool`、`restore_pending_pages(file_path: str) -> dict[int, int] | None`（返回 {page_index: ocr_preproc_angle} 或 None）

- [ ] **Step 1: 写失败测试 — 指纹计算与 sidecar 路径**

```python
# tests/utils/test_ocr_sidecar.py
import json
from pathlib import Path
from unittest.mock import patch

from vibeocr.utils.ocr_sidecar import (
    SIDECAR_VERSION,
    compute_fingerprint,
    sidecar_path,
    load_sidecar,
    save_sidecar,
    mark_pages_saved,
    mark_completed,
    restore_pending_pages,
)


def test_compute_fingerprint_uses_size_and_mtime(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_bytes(b"hello")
    fp = compute_fingerprint(str(f))
    size, mtime = fp.split(":")
    assert size == "5"
    assert int(mtime) > 0


def test_sidecar_path_under_vibeocr_cache(tmp_path):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"x")
    p = sidecar_path(str(f))
    # 位于 .vibeocr/ocr_sessions/ 下，文件名含指纹
    assert p.parent.name == "ocr_sessions"
    assert p.parent.parent.name == ".vibeocr"
    assert p.suffix == ".json"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/utils/test_ocr_sidecar.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'vibeocr.utils.ocr_sidecar'`

- [ ] **Step 3: 实现模块骨架 + 指纹/路径**

```python
# src/vibeocr/utils/ocr_sidecar.py
"""OCR 断点续传 sidecar：记录已增量落盘的页，崩溃后可跳过。

存储位置：<install_root>/.vibeocr/ocr_sessions/<fingerprint>.json
（复用 machine_cache 的 .vibeocr 目录与原子写模式）。

sidecar 是"尽力而为"：写入失败只记日志，不阻断 OCR 主流程。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from vibeocr.env_manager import get_project_root
from vibeocr.machine_cache import get_cache_dir

logger = logging.getLogger(__name__)

SIDECAR_VERSION = 1
_SIDECAR_SUBDIR = "ocr_sessions"


def compute_fingerprint(file_path: str) -> str:
    """文件指纹 = f"{size}:{mtime_ns}"。O(1)，不读全文件。"""
    st = os.stat(file_path)
    return f"{st.st_size}:{int(st.st_mtime_ns)}"


def _sessions_dir() -> Path:
    return get_cache_dir(get_project_root()) / _SIDECAR_SUBDIR


def sidecar_path(file_path: str) -> Path:
    return _sessions_dir() / f"{compute_fingerprint(file_path)}.json"


def load_sidecar(file_path: str) -> dict | None:
    """读 sidecar；指纹不匹配或损坏返回 None。"""
    try:
        p = sidecar_path(file_path)
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("version") != SIDECAR_VERSION:
            return None
        if data.get("fingerprint") != compute_fingerprint(file_path):
            return None
        return data
    except Exception as e:
        logger.debug("sidecar 读取失败（忽略）: %s", e)
        return None


def save_sidecar(file_path: str, data: dict) -> bool:
    """原子写（tmp + os.replace，复用 machine_cache 模式）。"""
    p = sidecar_path(file_path)
    tmp = p.with_suffix(".json.tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
        return True
    except Exception as e:
        logger.warning("sidecar 写入失败（忽略，不阻断 OCR）: %s", e)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/utils/test_ocr_sidecar.py -v`
Expected: 2 PASS

- [ ] **Step 5: 写失败测试 — mark_pages_saved / mark_completed / restore_pending_pages**

追加到 `tests/utils/test_ocr_sidecar.py`：

```python
def test_mark_pages_saved_merges_into_existing(tmp_path, monkeypatch):
    f = tmp_path / "d.pdf"
    f.write_bytes(b"abc")
    # 重定向 sidecar 目录到 tmp，避免污染真实 .vibeocr
    monkeypatch.setattr(
        "vibeocr.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    # 第一批
    assert mark_pages_saved(str(f), [0, 1], {0: 0, 1: 90}) is True
    data = load_sidecar(str(f))
    assert data["completed"] is False
    assert data["pages"] == {"0": {"has_text_layer": True, "ocr_preproc_angle": 0},
                              "1": {"has_text_layer": True, "ocr_preproc_angle": 90}}
    # 第二批合并
    assert mark_pages_saved(str(f), [2], {2: 0}) is True
    data = load_sidecar(str(f))
    assert set(data["pages"].keys()) == {"0", "1", "2"}


def test_mark_completed_sets_flag(tmp_path, monkeypatch):
    f = tmp_path / "d.pdf"
    f.write_bytes(b"abc")
    monkeypatch.setattr(
        "vibeocr.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    mark_pages_saved(str(f), [0], {0: 0})
    assert mark_completed(str(f)) is True
    assert load_sidecar(str(f))["completed"] is True


def test_restore_pending_pages_returns_dict_when_incomplete(tmp_path, monkeypatch):
    f = tmp_path / "d.pdf"
    f.write_bytes(b"abc")
    monkeypatch.setattr(
        "vibeocr.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    mark_pages_saved(str(f), [0, 2], {0: 0, 2: 90})
    result = restore_pending_pages(str(f))
    assert result == {0: 0, 2: 90}


def test_restore_pending_pages_none_when_completed(tmp_path, monkeypatch):
    f = tmp_path / "d.pdf"
    f.write_bytes(b"abc")
    monkeypatch.setattr(
        "vibeocr.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    mark_pages_saved(str(f), [0], {0: 0})
    mark_completed(str(f))
    assert restore_pending_pages(str(f)) is None


def test_restore_pending_pages_none_when_fingerprint_mismatch(tmp_path, monkeypatch):
    f = tmp_path / "d.pdf"
    f.write_bytes(b"abc")
    monkeypatch.setattr(
        "vibeocr.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
    )
    mark_pages_saved(str(f), [0], {0: 0})
    f.write_bytes(b"changed-content")  # 改动文件 → 指纹变
    assert restore_pending_pages(str(f)) is None
```

- [ ] **Step 6: 运行测试确认失败**

Run: `pytest tests/utils/test_ocr_sidecar.py -v`
Expected: 新增 4 个 FAIL（mark_pages_saved 等未定义）

- [ ] **Step 7: 实现剩余函数**

追加到 `src/vibeocr/utils/ocr_sidecar.py`：

```python
def _new_sidecar(file_path: str) -> dict:
    return {
        "version": SIDECAR_VERSION,
        "file_path": os.path.abspath(file_path),
        "fingerprint": compute_fingerprint(file_path),
        "completed": False,
        "pages": {},
    }


def mark_pages_saved(
    file_path: str, page_indices: list[int], angles: dict[int, int]
) -> bool:
    """增量合并：把 page_indices 标记为已落盘。angles = {page: preproc_angle}。"""
    data = load_sidecar(file_path) or _new_sidecar(file_path)
    for idx in page_indices:
        data["pages"][str(idx)] = {
            "has_text_layer": True,
            "ocr_preproc_angle": int(angles.get(idx, 0)),
        }
    data["completed"] = False
    data["fingerprint"] = compute_fingerprint(file_path)
    return save_sidecar(file_path, data)


def mark_completed(file_path: str) -> bool:
    data = load_sidecar(file_path) or _new_sidecar(file_path)
    data["completed"] = True
    return save_sidecar(file_path, data)


def restore_pending_pages(file_path: str) -> dict[int, int] | None:
    """返回 {page_index: ocr_preproc_angle} 用于续传跳过。

    None 表示：无 sidecar / 指纹不匹配 / 已 completed。
    """
    data = load_sidecar(file_path)
    if data is None or data.get("completed"):
        return None
    return {
        int(k): v.get("ocr_preproc_angle", 0)
        for k, v in data.get("pages", {}).items()
    }
```

- [ ] **Step 8: 运行测试确认通过**

Run: `pytest tests/utils/test_ocr_sidecar.py -v`
Expected: 6 PASS

- [ ] **Step 9: 提交**

```bash
git add src/vibeocr/utils/ocr_sidecar.py tests/utils/test_ocr_sidecar.py
git commit -m "feat(sidecar): 新增 OCR 断点续传 sidecar 读写模块

指纹校验 + 原子写 + 增量合并页 + 续传恢复。尽力而为，失败不阻断 OCR。"
```

---

### Task 2: `save_incremental` 静态方法

**Files:**
- Modify: `src/vibeocr/services/pdf_service.py`（在 `save_with_rewrite` 后新增方法，约 `:263` 处）
- Test: `tests/services/test_pdf_service_save_incremental.py`

**Interfaces:**
- Consumes: 现有 `pdf_service.py:248-256` 的 incremental 分支逻辑（备份 → `doc.save(incremental=True)` → 删备份；异常从备份回滚）
- Produces: `PdfService.save_incremental(doc: fitz.Document, save_path: str) -> bool`（True=已落盘，False=失败已回滚，doc 文字层保留可用）

- [ ] **Step 1: 写失败测试**

```python
# tests/services/test_pdf_service_save_incremental.py
import fitz
from vibeocr.services.pdf_service import PdfService


def test_save_incremental_persists_and_keeps_doc_usable(tmp_path):
    pdf = tmp_path / "a.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 72), "hello")
    doc.save(str(pdf))
    doc.close()
    doc = fitz.open(str(pdf))
    # 再加一层文字
    doc[0].insert_text((50, 100), "world")

    ok = PdfService.save_incremental(doc, str(pdf))
    assert ok is True
    # doc 仍可用（不重开，不 close）
    assert doc.page_count == 1
    # 重开验证内容落盘
    doc.close()
    doc2 = fitz.open(str(pdf))
    text = doc2[0].get_text()
    assert "hello" in text and "world" in text
    doc2.close()


def test_save_incremental_returns_false_and_keeps_doc_usable_on_failure(
    tmp_path, monkeypatch
):
    """失败时 doc 保持可用（不 close），文件从备份回滚。调用方据此不写 sidecar。"""
    pdf = tmp_path / "a.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(str(pdf))
    doc.close()
    doc = fitz.open(str(pdf))
    doc[0].insert_text((50, 100), "world")  # 内存改动

    # 模拟 save 抛异常（incremental 写文件失败）
    def boom(self, *a, **kw):
        raise RuntimeError("disk full")
    monkeypatch.setattr(fitz.Document, "save", boom)

    ok = PdfService.save_incremental(doc, str(pdf))
    assert ok is False
    # 关键：doc 仍可用（未 close），内存文字层保留，可继续后续操作
    assert doc.page_count == 1
    assert "world" in doc[0].get_text()  # 内存改动还在
    doc.close()
    # 文件从备份回滚（只剩最初的 new_page，无 world）
    monkeypatch.undo()
    doc2 = fitz.open(str(pdf))
    assert doc2.page_count == 1
    assert "world" not in doc2[0].get_text()
    doc2.close()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/services/test_pdf_service_save_incremental.py -v`
Expected: FAIL with `AttributeError: type object 'PdfService' has no attribute 'save_incremental'`

- [ ] **Step 3: 实现 `save_incremental`**

在 `src/vibeocr/services/pdf_service.py` 的 `save_with_rewrite` 方法之后（约 `:263`，`# ---- render` 注释前）插入：

```python
    @staticmethod
    def save_incremental(doc: fitz.Document, save_path: str) -> bool:
        """增量保存（纯加文字层场景）。doc 不 close/重开，内存对象始终可用。

        先备份原文件 → doc.save(incremental=True) → 删备份；异常从备份回滚文件
        但不 close doc（内存文字层保留，调用方可继续后续操作）。
        供 OCR 逐批落盘使用：每批写层后调用，崩溃只丢最后一批。

        Args:
            doc: fitz.Document 实例（已写好本批文字层）。无论成功失败都不 close。

        Returns:
            True 已落盘；False 失败已回滚文件（doc 内存文字层保留可用，调用方
            不应标记该批已落盘/不写 sidecar）。
        """
        backup_path = save_path + ".bak"
        try:
            shutil.copy2(save_path, backup_path)
        except Exception as e:
            logger.error("save_incremental: 备份失败，跳过本批落盘: %s", e)
            return False
        try:
            doc.save(save_path, incremental=True, encryption=0)
            Path(backup_path).unlink(missing_ok=True)
            return True
        except Exception as e:
            # incremental save 失败：文件可能半写，从备份恢复。
            # doc 内存对象未受影响（fitz save 失败不改内存 doc），保持可用，
            # 内存文字层保留。调用方据此返回 saved=False，不写 sidecar。
            logger.error("save_incremental: 增量保存失败，从备份回滚文件: %s", e)
            try:
                shutil.copy2(backup_path, save_path)
                Path(backup_path).unlink(missing_ok=True)
            except Exception:
                logger.error("save_incremental: 备份回滚失败", exc_info=True)
            return False
```

> 契约说明：成功/失败都不 close doc，调用方无需处理 doc 替换。失败时文件从 `.bak` 回滚，doc 内存文字层保留（fitz `save` 失败不改动内存对象）。这与 Task 4 路由层简化——回滚分支无需 `fitz.open` 替换 `s.doc`。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/services/test_pdf_service_save_incremental.py -v`
Expected: 2 PASS

> 注：`test_save_incremental_returns_false_on_failure` 中回滚后 doc 被 close，测试只验证文件未损坏（重新 open），不依赖原 doc 对象。

- [ ] **Step 5: 提交**

```bash
git add src/vibeocr/services/pdf_service.py tests/services/test_pdf_service_save_incremental.py
git commit -m "feat(pdf): 新增 save_incremental 增量落盘方法

复用 incremental save 路径，doc 不重开可继续 OCR。失败从备份回滚。
供 OCR 逐批落盘使用。"
```

---

### Task 3: Schema 改动（`save` 字段 + `COMPRESS` phase）

**Files:**
- Modify: `src/vibeocr/ipc/schemas.py:99-110`（ProgressPhase）、`:185-194`（BatchAddTextLayerRequest）

**Interfaces:**
- Produces: `BatchAddTextLayerRequest.save: bool = False`、`ProgressPhase.COMPRESS = "compress"`

- [ ] **Step 1: 改 `BatchAddTextLayerRequest` 加 `save` 字段**

编辑 `src/vibeocr/ipc/schemas.py`，在 `BatchAddTextLayerRequest`（`:185`）末尾加字段：

```python
class BatchAddTextLayerRequest(BaseModel):
    """批量写 OCR 文字层：一次接收一批页，后端聚合字符解析单一子集字体。

    避免逐页 add_text_layer 每页各解析一份子集字体（放大体积）。
    聚合逻辑复用 save_with_rewrite 已验证的"整文档一次子集"模式。

    save=True 时，写层成功后紧跟一次 incremental save 把本批落盘
    （崩溃只丢最后一批）。返回 extra.saved 标记是否成功落盘。
    """

    pages: list[BatchAddTextLayerPage]
    pdf_settings: dict[str, Any] | None = None
    overwrite: bool = False
    save: bool = False
```

- [ ] **Step 2: 改 `ProgressPhase` 加 `COMPRESS`**

编辑 `src/vibeocr/ipc/schemas.py:99-110`，在 `EXPORT` 后加：

```python
class ProgressPhase(StrEnum):
    """长操作阶段标识,供主进程切换文案/确定 vs 不确定进度条。"""

    LOAD = "load"
    RENDER = "render"
    OCR = "ocr"
    WRITE = "write"
    DETECT = "detect"
    CORRECT = "correct"
    DELETE = "delete"
    SAVE = "save"
    EXPORT = "export"
    COMPRESS = "compress"  # OCR 末尾整文档聚合压缩（不确定进度）
```

- [ ] **Step 3: 验证 schema 可导入且字段就位**

Run:
```bash
cd C:/Users/felji/PycharmProjects/VibeOCR && python -c "
from vibeocr.ipc.schemas import BatchAddTextLayerRequest, ProgressPhase
r = BatchAddTextLayerRequest(pages=[])
assert r.save is False
assert ProgressPhase.COMPRESS == 'compress'
print('OK')
"
```
Expected: `OK`

- [ ] **Step 4: 提交**

```bash
git add src/vibeocr/ipc/schemas.py
git commit -m "feat(ipc): BatchAddTextLayerRequest 加 save 字段 + ProgressPhase.COMPRESS

save=True 触发写层后 incremental 落盘；COMPRESS phase 用于 OCR 末尾聚合压缩进度。"
```

---

### Task 4: 后端路由加 `save` 分支

**Files:**
- Modify: `src/vibeocr/services/pdf_backend_process.py:596-624`（`add_text_layer_batch` 路由）

**Interfaces:**
- Consumes: `PdfService.save_incremental(doc, save_path) -> bool`（Task 2）、`req.save`（Task 3）、`s.pdf_document.file_path`
- Produces: `MutateResponse.extra = {"saved": bool}`（saved=False 时调用方不写 sidecar）

- [ ] **Step 1: 改路由，在写层成功后追加 incremental save**

编辑 `src/vibeocr/services/pdf_backend_process.py`，替换 `add_text_layer_batch` 路由（`:596-624`）：

```python
@app.post("/session/{sid}/add_text_layer_batch", response_model=MutateResponse)
def add_text_layer_batch(
    sid: str, req: BatchAddTextLayerRequest
) -> MutateResponse:
    """批量写 OCR 文字层，一批页共享单一聚合子集字体。

    与逐页 add_text_layer 的区别：把本批所有页字符聚合一次解析子集字体，
    全批共享，避免每页一份独立子集字体放大体积。聚合逻辑复用
    PdfService.add_text_layer_batch（参照 save_with_rewrite 的整文档子集模式）。

    save=True 时，写层成功后紧跟一次 incremental save 把本批落盘
    （崩溃只丢最后一批）。extra.saved 标记是否成功落盘（False=回滚，调用方不写 sidecar）。
    """
    s = _get_registry().get(sid)
    try:
        pages_data = [
            {"page": p.page, "ocr_result": p.ocr_result} for p in req.pages
        ]
        saved = True
        with s.fitz_lock:
            results = PdfService.add_text_layer_batch(
                s.doc, s.pdf_document, pages_data,
                pdf_settings=_settings_from_dict(req.pdf_settings),
                overwrite=req.overwrite,
                cancel_check=s.cancel_event.is_set,
            )
            written_pages = sorted(results.keys())
            # 写层成功且有页 → 可选增量落盘
            if req.save and written_pages:
                save_path = s.pdf_document.file_path
                if save_path:
                    # save_incremental 成功失败都不 close doc，无需替换 s.doc
                    saved = PdfService.save_incremental(s.doc, save_path)
        return MutateResponse(
            diff=_diff_pages(
                s.pdf_document, written_pages,
                invalidate_thumbnails=written_pages, modified=True
            ),
            extra={"saved": saved} if req.save else None,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"批量加文字层失败: {e}") from e
```

- [ ] **Step 2: 验证路由可导入（语法检查）**

Run:
```bash
cd C:/Users/felji/PycharmProjects/VibeOCR && python -c "
import ast
ast.parse(open('src/vibeocr/services/pdf_backend_process.py', encoding='utf-8').read())
print('syntax OK')
"
```
Expected: `syntax OK`

- [ ] **Step 3: 提交**

```bash
git add src/vibeocr/services/pdf_backend_process.py
git commit -m "feat(backend): add_text_layer_batch 路由支持 save 增量落盘

save=True 时写层后 incremental save，extra.saved 标记结果。
回滚时重新 open doc 替换 s.doc。"
```

---

### Task 5: 客户端 `add_text_layer_batch` 加 `save` 参数

**Files:**
- Modify: `src/vibeocr/services/pdf_backend_client.py:476-501`

**Interfaces:**
- Consumes: `req.save`（Task 3）、`MutateResponse.extra`（Task 4）
- Produces: `PdfBackendClient.add_text_layer_batch(..., save: bool = False) -> MutateResponse`（调用方从 `resp.extra.get("saved")` 判定是否落盘成功）

- [ ] **Step 1: 改方法签名传 `save`**

编辑 `src/vibeocr/services/pdf_backend_client.py:476-501`：

```python
    def add_text_layer_batch(
        self,
        sid: str,
        pages_data: list[dict],  # [{page, ocr_result}, ...]
        pdf_settings: dict | None = None,
        overwrite: bool = False,
        save: bool = False,
    ) -> MutateResponse:
        """批量写 OCR 文字层，一批页共享单一聚合子集字体。

        供 PdfSessionManager._run_ocr 阶段3 攒一批结果后一次调用，替代逐页
        add_text_layer。后端聚合本批所有页字符解析单一子集字体，避免每页一份。

        save=True 时后端写层后 incremental 落盘，resp.extra["saved"] 标记结果
        （False=回滚，调用方不写 sidecar）。
        """
        pages = [
            BatchAddTextLayerPage(page=p["page"], ocr_result=p["ocr_result"])
            for p in pages_data
        ]
        return self._parse(
            self._post(
                f"/session/{sid}/add_text_layer_batch",
                BatchAddTextLayerRequest(
                    pages=pages, pdf_settings=pdf_settings,
                    overwrite=overwrite, save=save,
                ).model_dump(),
                timeout=_HTTP_LONG_TIMEOUT,
            ),
            MutateResponse,
        )
```

- [ ] **Step 2: 验证语法**

Run:
```bash
cd C:/Users/felji/PycharmProjects/VibeOCR && python -c "
import ast
ast.parse(open('src/vibeocr/services/pdf_backend_client.py', encoding='utf-8').read())
print('syntax OK')
"
```
Expected: `syntax OK`

- [ ] **Step 3: 提交**

```bash
git add src/vibeocr/services/pdf_backend_client.py
git commit -m "feat(client): add_text_layer_batch 加 save 参数透传后端

resp.extra['saved'] 标记增量落盘结果。"
```

---

### Task 6: Manager 编排（增量落盘 + sidecar + 末尾压缩 + 续传 + 预览增量落 model）

**Files:**
- Modify: `src/vibeocr/managers/pdf_session_manager.py`（`_run_ocr:857`、阶段3 `:947-1019`、末尾 `:1021-1033`、`_on_ocr_page_done_signal:1035-1040`、`start_ocr:712`）
- Modify: `tests/managers/test_pdf_session_manager.py`（追加测试）

**Interfaces:**
- Consumes: `ocr_sidecar.mark_pages_saved/mark_completed/restore_pending_pages`（Task 1）、`client.add_text_layer_batch(save=True)` + `resp.extra["saved"]`（Task 5）、`PdfService.save_with_rewrite`（现有，末尾压缩）、`ProgressPhase.COMPRESS`（Task 3）
- Produces: `_run_ocr` 阶段3 后增量落盘 + 写 sidecar；末尾聚合压缩 + `mark_completed`；`start_ocr` 入口按 sidecar 过滤 page_indices；`_on_ocr_page_done_signal` 增量落 model

本任务分 4 个子改动，各自带测试 + 提交。

#### 6A: `_on_ocr_page_done_signal` 增量落 model（消除预览滞后）

- [ ] **Step 6A-1: 写失败测试**

追加到 `tests/managers/test_pdf_session_manager.py`：

```python
class TestOcrPageDoneIncrementalModel:
    """_on_ocr_page_done_signal 应增量把 result.text_blocks 落 model，
    消除预览滞后（此前只在整批结束 get_model 才全量刷新）。"""

    def test_page_done_writes_ocr_blocks_to_model(self, qapp, tmp_path):
        from unittest.mock import MagicMock
        from vibeocr.managers.pdf_session_manager import PdfSessionManager
        from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._sessions = {}
        mgr._active_path = str(tmp_path / "x.pdf")
        mgr._session_id_to_path = {"sid1": str(tmp_path / "x.pdf")}

        doc = PdfDocument(file_path=str(tmp_path / "x.pdf"))
        doc.pages = [PdfPageInfo(page_index=0), PdfPageInfo(page_index=1)]
        session = MagicMock()
        session.pdf_document = doc
        mgr._sessions[str(tmp_path / "x.pdf")] = session

        # 模拟 OCRResult（带 text_blocks + preproc_angle）
        result = MagicMock()
        block = MagicMock()
        block.text = "hello"
        result.text_blocks = [block]
        result.preproc_angle = 90

        mgr._on_ocr_page_done_signal("sid1", 1, result)

        info = doc.pages[1]
        assert info.has_text_layer is True
        assert info.ocr_text_blocks == [block]
        assert info.ocr_preproc_angle == 90
```

- [ ] **Step 6A-2: 运行确认失败**

Run: `pytest tests/managers/test_pdf_session_manager.py::TestOcrPageDoneIncrementalModel -v`
Expected: FAIL（ocr_text_blocks 仍为空）

- [ ] **Step 6A-3: 改 `_on_ocr_page_done_signal`**

编辑 `src/vibeocr/managers/pdf_session_manager.py:1035-1040`：

```python
    def _on_ocr_page_done_signal(
        self, session_id: str, page_index: int, result: object
    ) -> None:
        file_path = self._path_for_session_id(session_id)
        if file_path:
            # 增量落 model：把 result.text_blocks 立即写入该页 PdfPageInfo，
            # 消除预览滞后（此前只在整批结束 get_model 才全量刷新）。
            # result 为 None（失败/空页）时跳过。
            if result is not None:
                session = self._sessions.get(file_path)
                if session is not None:
                    info = session.pdf_document.get_page(page_index)
                    if info is not None:
                        info.ocr_text_blocks = list(getattr(result, "text_blocks", []) or [])
                        info.ocr_preproc_angle = int(getattr(result, "preproc_angle", 0) or 0)
                        if info.ocr_text_blocks:
                            info.has_text_layer = True
            self.ocr_page_done.emit(file_path, page_index, result)
```

- [ ] **Step 6A-4: 运行确认通过**

Run: `pytest tests/managers/test_pdf_session_manager.py::TestOcrPageDoneIncrementalModel -v`
Expected: PASS

- [ ] **Step 6A-5: 提交**

```bash
git add src/vibeocr/managers/pdf_session_manager.py tests/managers/test_pdf_session_manager.py
git commit -m "fix(manager): _on_ocr_page_done_signal 增量落 model 消除预览滞后

OCR 进行中打开预览，已识别页立刻拿到 OCR 原始块，不再回退 detect_text_layers 懒加载。"
```

#### 6B: `_run_ocr` 阶段3 加 incremental save + sidecar

- [ ] **Step 6B-1: 写失败测试（验证 save=True 传参 + sidecar 写入）**

追加到 `tests/managers/test_pdf_session_manager.py`：

```python
class TestRunOcrIncrementalSave:
    """_run_ocr 阶段3 写层后应 incremental save + 写 sidecar。"""

    def test_run_ocr_calls_add_text_layer_batch_with_save_and_writes_sidecar(
        self, qapp, tmp_path, monkeypatch
    ):
        from unittest.mock import MagicMock
        from vibeocr.managers.pdf_session_manager import PdfSessionManager, _OcrRunner
        from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo

        pdf_path = tmp_path / "doc.pdf"
        pdf_path.write_bytes(b"%PDF-1.4 test")

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._sessions = {}
        mgr._active_path = str(pdf_path)

        doc = PdfDocument(file_path=str(pdf_path))
        doc.pages = [PdfPageInfo(page_index=i) for i in range(3)]
        session = MagicMock()
        session.session_id = "sid1"
        session.pdf_document = doc
        session.ocr_stats = {"written": 0, "skipped": 0}
        session.add_ocr_stats = MagicMock()
        mgr._sessions[str(pdf_path)] = session
        mgr._overwrite_text_layer = False

        # mock client
        client = MagicMock()
        # render_preview 返回非空 bytes（避免被当渲染失败）
        client.render_preview.return_value = b"\x89PNG fake"
        # add_text_layer_batch 返回带 extra.saved=True
        resp = MagicMock()
        resp.extra = {"saved": True}
        client.add_text_layer_batch.return_value = resp
        client.get_model.return_value = MagicMock()
        mgr._client = client

        # mock OCR service：每页返回带 text_blocks 的 result
        mgr._ocr_service = MagicMock()
        block = MagicMock()
        block.text = "t"
        result = MagicMock()
        result.text_blocks = [block]
        result.preproc_angle = 0
        mgr._ocr_service.recognize_batch.return_value = [result] * 3

        # sidecar 重定向到 tmp
        monkeypatch.setattr(
            "vibeocr.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "sessions"
        )
        from vibeocr.utils import ocr_sidecar
        # mark_pages_saved 用真实实现
        import vibeocr.managers.pdf_session_manager as mgr_mod
        # 确保 manager 内已 import ocr_sidecar（见实现步骤）

        runner = MagicMock()
        runner._cancelled = False
        runner._task_id = 1
        runner.page_done = MagicMock()
        runner.progress = MagicMock()
        runner.all_done = MagicMock()

        mgr._run_ocr(runner, "sid1", [0, 1, 2], None, {}, False)

        # 关键断言：add_text_layer_batch 被调用且 save=True
        assert client.add_text_layer_batch.called
        _, kwargs = client.add_text_layer_batch.call_args
        assert kwargs.get("save") is True
        # sidecar 已写入页（restore_pending_pages 应返回已落盘页）
        # 注意：末尾聚合压缩后 mark_completed，故 restore 返回 None
        from vibeocr.utils.ocr_sidecar import load_sidecar
        data = load_sidecar(str(pdf_path))
        assert data is not None
        assert data["completed"] is True
```

> 注：此测试覆盖 6B（save 透传 + sidecar）和 6C（末尾 mark_completed）。

- [ ] **Step 6B-2: 运行确认失败**

Run: `pytest tests/managers/test_pdf_session_manager.py::TestRunOcrIncrementalSave -v`
Expected: FAIL（save 未传或为 False）

- [ ] **Step 6B-3: 改 `_run_ocr` 阶段3 — 加 save=True + sidecar**

在 `src/vibeocr/managers/pdf_session_manager.py` 顶部 import 区加（若未有）：

```python
from vibeocr.utils import ocr_sidecar
```

编辑 `_run_ocr` 阶段3（`:968-979`，`if write_items and not runner._cancelled:` 块）：

```python
            write_page_results: dict[int, bool] = {}  # page -> ok
            batch_persisted = False
            if write_items and not runner._cancelled:
                try:
                    resp = self._client.add_text_layer_batch(
                        session_id, write_items, settings_dict, overwrite,
                        save=True,
                    )
                    batch_persisted = bool((resp.extra or {}).get("saved", False))
                    for item in write_items:
                        write_page_results[item["page"]] = True
                except Exception as e:
                    logger.error("批量写文字层失败(批起始页 %d): %s", batch_pages[0], e)
                    for item in write_items:
                        write_page_results[item["page"]] = False

            # 本批 incremental save 成功 → 写 sidecar 标记已落盘页
            if batch_persisted and session.file_path:
                try:
                    angles = {
                        item["page"]: int(
                            getattr(item["_result"], "preproc_angle", 0) or 0
                        )
                        for item in write_items
                        if write_page_results.get(item["page"], False)
                    }
                    saved_pages = list(angles.keys())
                    if saved_pages:
                        ocr_sidecar.mark_pages_saved(
                            session.file_path, saved_pages, angles
                        )
                except Exception:
                    logger.debug("sidecar mark_pages_saved 失败（忽略）", exc_info=True)
```

- [ ] **Step 6B-4: 运行测试（此时可能因末尾压缩未加而部分失败，6C 补齐）**

Run: `pytest tests/managers/test_pdf_session_manager.py::TestRunOcrIncrementalSave -v`
Expected: 可能仍 FAIL（`completed` 为 False，因为末尾 mark_completed 未加）—— 由 6C 补齐

#### 6C: `_run_ocr` 末尾加聚合压缩 + mark_completed

- [ ] **Step 6C-1: 改 `_run_ocr` 末尾（`:1021-1033` 之间，get_model 之前）**

编辑 `src/vibeocr/managers/pdf_session_manager.py`，在 `# 刷新 model` 注释（`:1021`）之前插入末尾压缩块：

```python
        # 末尾整文档聚合压缩：把批级冗余子集字体合并为单一字体 + 全量压缩落盘。
        # 复用 save_with_rewrite（path=None 覆盖原文件，compress_on_save 默认 True
        # 走 _compress_in_place）。doc 会被 close/reopen，由后端 save 路由替换。
        # compress 失败时 sidecar 保持 completed=false（已 incremental 落盘的页仍有效）。
        if not runner._cancelled and success > 0 and session.file_path:
            try:
                runner.progress.emit(session_id, 0, 0)  # 不确定进度（COMPRESS 态）
                self._client.save(session_id, None, settings_dict)
                try:
                    ocr_sidecar.mark_completed(session.file_path)
                except Exception:
                    logger.debug("sidecar mark_completed 失败（忽略）", exc_info=True)
            except Exception as e:
                logger.error("OCR 末尾聚合压缩失败（中间结果已增量落盘）: %s", e)
```

- [ ] **Step 6C-2: 运行 6B 测试确认通过**

Run: `pytest tests/managers/test_pdf_session_manager.py::TestRunOcrIncrementalSave -v`
Expected: PASS

> 测试中 `client.save` 是 MagicMock 默认返回，不抛异常 → mark_completed 执行 → `completed=True`。

- [ ] **Step 6C-3: 提交 6B+6C**

```bash
git add src/vibeocr/managers/pdf_session_manager.py tests/managers/test_pdf_session_manager.py
git commit -m "feat(manager): _run_ocr 逐批增量落盘 + sidecar + 末尾聚合压缩

阶段3 写层后 save=True 增量落盘，成功则 mark_pages_saved；
末尾 save_with_rewrite 聚合单一子集字体 + 全量压缩，mark_completed。
崩溃只丢最后一批；最终体积与手动保存一致。"
```

#### 6D: `start_ocr` 入口按 sidecar 续传过滤

- [ ] **Step 6D-1: 写失败测试**

追加到 `tests/managers/test_pdf_session_manager.py`：

```python
class TestStartOcrResumeFilter:
    """start_ocr 应读取 sidecar，过滤掉已落盘页（断点续传）。"""

    def test_start_ocr_skips_pages_in_pending_sidecar(self, qapp, tmp_path, monkeypatch):
        from unittest.mock import MagicMock, patch
        from PySide6.QtCore import QThread
        from vibeocr.managers.pdf_session_manager import PdfSessionManager
        from vibeocr.models.pdf_document import PdfDocument, PdfPageInfo

        pdf_path = tmp_path / "r.pdf"
        pdf_path.write_bytes(b"abc")
        monkeypatch.setattr(
            "vibeocr.utils.ocr_sidecar._sessions_dir", lambda: tmp_path / "s"
        )
        # 预置 sidecar：页 0 已落盘，未完成
        from vibeocr.utils.ocr_sidecar import mark_pages_saved
        mark_pages_saved(str(pdf_path), [0], {0: 0})

        mgr = PdfSessionManager.__new__(PdfSessionManager)
        mgr._task_generation = 0
        mgr._sessions = {}
        mgr._active_path = str(pdf_path)
        mgr._ocr_running = False
        mgr._ocr_cancelled = False
        mgr._ocr_worker = None
        mgr._client = MagicMock()

        doc = PdfDocument(file_path=str(pdf_path))
        doc.pages = [PdfPageInfo(page_index=0), PdfPageInfo(page_index=1)]
        session = MagicMock()
        session.session_id = "sid1"
        session.reset_ocr_stats = MagicMock()
        session.pdf_document = doc
        mgr._sessions[str(pdf_path)] = session
        mgr._ocr_service = MagicMock()
        mgr._is_mineru_first_use = MagicMock(return_value=False)
        mgr._pdf_settings = MagicMock()
        mgr._overwrite_text_layer = False

        mgr._pdf_settings = MagicMock()
        mgr._overwrite_text_layer = False
        mgr._settings_to_dict = MagicMock(return_value={})

        with (
            patch.object(mgr, "_cancel_ocr"),
            patch.object(QThread, "start"),  # 阻止真线程
        ):
            mgr.start_ocr([0, 1])  # 用户请求 0,1

        # runner 已构造，读取其 _pages：页 0 已落盘被过滤
        assert mgr._ocr_worker is not None
        assert mgr._ocr_worker._pages == [1]
```

- [ ] **Step 6D-2: 运行确认失败**

Run: `pytest tests/managers/test_pdf_session_manager.py::TestStartOcrResumeFilter -v`
Expected: FAIL（pages 未过滤，仍为 [0,1]）

- [ ] **Step 6D-3: 改 `start_ocr` 加续传过滤**

`start_ocr`（`:712`）在 `:749` 处构造 `_OcrRunner`（`:755` 内联定义），runner 构造时接收 `pages` 参数（`:766` `self._pages = pages`）。sidecar 过滤插在 `:749`（`# 后台线程编排 OCR 流程` 注释）之前，修改 `page_indices` 局部变量。

编辑 `src/vibeocr/managers/pdf_session_manager.py`，在 `start_ocr` 的 `self._client.reset_cancel(session.session_id)` 块（`:741-744`）之后、`# 递增 task generation`（`:745`）之前，插入：

```python
        # 断点续传：读取 sidecar，过滤掉已增量落盘的页（崩溃恢复）。
        # overwrite=True 时不过滤（用户明确要求重写）。
        if not overwrite and session.file_path:
            try:
                pending = ocr_sidecar.restore_pending_pages(session.file_path)
                if pending:
                    already = set(pending.keys())
                    page_indices = [p for p in page_indices if p not in already]
                    if not page_indices:
                        logger.info("start_ocr: 所有请求页已落盘（sidecar），跳过 OCR")
                        self._ocr_running = False
                        return
                    logger.info(
                        "start_ocr: sidecar 续传，跳过已落盘页 %s",
                        sorted(already),
                    )
            except Exception:
                logger.debug("start_ocr: sidecar 读取失败，全量 OCR", exc_info=True)
```

> 测试 6D-1 的 `fake_start_runner` patch 改为 patch runner 构造后的 `start()`：因 `_OcrRunner` 是 `start_ocr` 内联类，测试中 `patch.object(QThread, "start")` 已阻止真线程（见 `:397` 模板），需额外捕获传给 runner 的 pages。最简方式：`patch.object(QThread, "start")` 后，在断言前从 `mgr._ocr_worker._pages` 读取（`_OcrRunner.__init__` 存了 `self._pages`）。更新 6D-1 测试断言：

```python
        # runner 已构造（QThread.start 被 patch 不真跑），读取其 _pages
        assert mgr._ocr_worker is not None
        assert mgr._ocr_worker._pages == [1]  # 页 0 已落盘被过滤
```

- [ ] **Step 6D-4: 运行确认通过**

Run: `pytest tests/managers/test_pdf_session_manager.py::TestStartOcrResumeFilter -v`
Expected: PASS

- [ ] **Step 6D-5: 运行全部 manager 测试确认无回归**

Run: `pytest tests/managers/test_pdf_session_manager.py -v`
Expected: 全部 PASS

- [ ] **Step 6D-6: 提交**

```bash
git add src/vibeocr/managers/pdf_session_manager.py tests/managers/test_pdf_session_manager.py
git commit -m "feat(manager): start_ocr 按 sidecar 续传过滤已落盘页

崩溃后重开 PDF，OCR 自动跳过已增量落盘的页。overwrite=True 时不过滤。"
```

---

### Task 7: UI 四态格子 + 预览自动刷新 + 续传提示

**Files:**
- Modify: `src/vibeocr/views/tabs/pdf_tab.py`（`LayerStatusDelegate:118-179`、role 常量 `:112-115`、`_on_ocr_page_result:1015-1021`、网格构建处）

**Interfaces:**
- Consumes: manager 的 `ocr_page_done`（已带 result，6A 后 result 已落 model）、`_refresh_preview_window_if_current:1209`
- Produces: 格子四态视觉（none/processing/done/failed）、OCR 进行中预览自动刷新

#### 7A: 格子四态（新增 `_LAYER_STATE_ROLE`）

- [ ] **Step 7A-1: 加 `_LAYER_STATE_ROLE` 常量**

编辑 `src/vibeocr/views/tabs/pdf_tab.py:112-115`：

```python
# 文字层网格 item 数据角色
_LAYER_ROLE = Qt.ItemDataRole.UserRole
_HAS_LAYER_ROLE = Qt.ItemDataRole.UserRole + 1
_DESKEWED_ROLE = Qt.ItemDataRole.UserRole + 2
# 视觉状态枚举（不改 model schema，仅格子投影）：none/processing/done/failed
_LAYER_STATE_ROLE = Qt.ItemDataRole.UserRole + 3
```

- [ ] **Step 7A-2: 改 `LayerStatusDelegate.paint` 按四态着色**

编辑 `src/vibeocr/views/tabs/pdf_tab.py:128-163`，替换 `paint` 方法的着色逻辑：

```python
    def paint(self, painter, option, index):
        painter.save()
        page_idx = index.data(_LAYER_ROLE)
        page_num = str(page_idx + 1) if page_idx is not None else ""
        has_layer = index.data(_HAS_LAYER_ROLE)
        state = index.data(_LAYER_STATE_ROLE)  # none/processing/done/failed

        if option.state & QStyle.StateFlag.State_Selected:
            bg = QColor(Colors.accent)
        elif state == "processing":
            bg = QColor(Colors.accent)  # 蓝：识别中
        elif state == "failed":
            bg = QColor(Colors.danger)   # 红：失败
        elif has_layer or state == "done":
            bg = QColor(Colors.success)  # 绿：已落盘
        else:
            bg = QColor(Colors.text_subtle)  # 灰：未处理

        # 悬停态用 accent 描边，默认用 border 描边
        is_hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        border_color = QColor(Colors.accent) if is_hover else QColor(Colors.border)
        border_width = 2 if is_hover else 1

        rect = QRectF(option.rect)
        margin = 2
        cell = QRectF(
            rect.x() + margin,
            rect.y() + margin,
            rect.width() - 2 * margin,
            rect.height() - 2 * margin,
        )
        painter.setBrush(bg)
        painter.setPen(QPen(border_color, border_width))
        painter.drawRoundedRect(cell, 6, 6)

        painter.setPen(QPen(QColor("#ffffff")))
        font = painter.font()
        font.setBold(True)
        font.setPointSize(10)
        painter.setFont(font)
        painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, page_num)

        # failed 态右上角感叹号标记
        if state == "failed":
            painter.setPen(QPen(QColor("#ffffff"), 2))
            mark = QRectF(cell.right() - 12, cell.top() + 2, 10, 10)
            painter.drawText(mark, Qt.AlignmentFlag.AlignCenter, "!")

        # 已纠偏标记：右上角橙色小圆点（底色保持不变，两维信息并存）
        deskewed = index.data(_DESKEWED_ROLE)
        if deskewed:
            dot_d = 10
            dot = QRectF(
                cell.right() - dot_d - 2,
                cell.top() + 2,
                dot_d,
                dot_d,
            )
            painter.setBrush(QColor(Colors.warning))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(dot)

        painter.restore()
```

> 注：`failed` 标记与 `deskewed` 圆点位置可能重叠，但 failed 是终态优先级高，deskewed 在 failed 时通常无意义（识别失败不会纠偏），可接受。

- [ ] **Step 7A-3: 确认 `Colors.danger` 存在**

已核实：项目用 `Colors.danger`（见 `views/about_tab.py:341`），无 `Colors.error`。上述代码已用 `Colors.danger`。跳过此步。

- [ ] **Step 7A-4: 提交**

```bash
git add src/vibeocr/views/tabs/pdf_tab.py
git commit -m "feat(ui): 文字层格子四态(processing/done/failed/none) + 失败感叹号

新增 _LAYER_STATE_ROLE 视觉投影，不改 model schema。"
```

#### 7B: 网格状态转换（processing → done/failed）+ 预览自动刷新

- [ ] **Step 7B-1: 改 `_on_ocr_page_result` 加状态转换 + 预览刷新**

编辑 `src/vibeocr/views/tabs/pdf_tab.py:1015-1021`：

```python
    def _on_ocr_page_result(self, file_path: str, page_index: int, result) -> None:
        session = self._session_mgr.active_session
        if session is None or session.file_path != file_path:
            return
        # OCR 注入的是隐形文字层，缩略图无视觉变化 → 不重新渲染。
        # 逐页更新文字层网格格子 + 汇总统计。
        self._update_layer_grid_page(page_index, state="done" if result is not None else "failed")
        # 预览窗若正显示该页，刷新以叠加刚识别的文字层高亮
        self._refresh_preview_window_if_current(page_index)
```

- [ ] **Step 7B-2: 改 `_update_layer_grid_page` 支持 state 参数**

编辑 `src/vibeocr/views/tabs/pdf_tab.py:1342-1362`：

```python
    def _update_layer_grid_page(self, page_index: int, state: str | None = None) -> None:
        """增量更新单页网格格子（不全量重建），用于 OCR/删除文字层即时反馈。

        保留用户当前选中状态（只改单格的颜色/tooltip，不清空网格）。
        state: none/processing/done/failed 视觉态；None 时按 has_text_layer 推导。
        """
        session = self._session_mgr.active_session
        if session is None:
            return
        page_info = session.pdf_document.get_page(page_index)
        if page_info is None:
            return
        grid = self._layer_status_grid
        for row in range(grid.count()):
            item = grid.item(row)
            if item.data(_LAYER_ROLE) == page_index:
                item.setData(_HAS_LAYER_ROLE, page_info.has_text_layer)
                item.setData(_DESKEWED_ROLE, page_info.deskewed)
                if state is not None:
                    item.setData(_LAYER_STATE_ROLE, state)
                item.setToolTip(self._layer_cell_tooltip(page_info))
                break
        # 汇总统计实时刷新
        self._update_layer_summary(session.pdf_document.pages)
```

- [ ] **Step 7B-3: OCR 启动时把待识别页置 processing**

`_begin_ocr_ui(self, indices)` 是所有 OCR 入口（`_add_text_layer_for_indices:1448`、`_on_add_text_layer_for_pages_without_layer:2021`、`_on_add_text_layer:2092`）的公共 UI 复位点，`indices` 是变量名。在此函数末尾追加 processing 态设置。

编辑 `src/vibeocr/views/tabs/pdf_tab.py:2006-2019` `_begin_ocr_ui`，在 `self._btn_add_file.setEnabled(False)` 之后追加：

```python
        # 把本次待识别页置 processing 态（蓝），让用户看到"哪些页在算"
        for idx in indices:
            self._update_layer_grid_page(idx, state="processing")
```

- [ ] **Step 7B-4: 验证语法**

Run:
```bash
cd C:/Users/felji/PycharmProjects/VibeOCR && python -c "
import ast
ast.parse(open('src/vibeocr/views/tabs/pdf_tab.py', encoding='utf-8').read())
print('syntax OK')
"
```
Expected: `syntax OK`

- [ ] **Step 7B-5: 提交**

```bash
git add src/vibeocr/views/tabs/pdf_tab.py
git commit -m "feat(ui): OCR 进行中格子 processing 态 + 预览自动刷新

待识别页置蓝(processing)，写层完成转绿(done)/红(failed)；
预览窗正显示该页时识别完自动重绘叠加文字层。"
```

#### 7C: 续传提示（打开 PDF 时检测未完成 sidecar）

- [ ] **Step 7C-1: 在 `_on_load_done` 检测 sidecar 并提示**

`_on_load_done(self, file_path)` 在 `pdf_tab.py:1007`，内已有 `session = self._session_mgr.active_session`（`:1008`）。在函数末尾（`:1013` 之后）追加：

```python
        # 续传检测：若有未完成 sidecar，提示用户可继续 OCR
        try:
            from vibeocr.utils.ocr_sidecar import restore_pending_pages
            if file_path:
                pending = restore_pending_pages(file_path)
                if pending:
                    total_pages = len(session.pdf_document.pages) if session else 0
                    self._status_label.setText(
                        f"检测到上次未完成的 OCR（已保存 {len(pending)}/{total_pages} 页），"
                        f"可继续识别剩余页"
                    )
        except Exception:
            pass  # 续传提示是锦上添花，失败静默
```

- [ ] **Step 7C-2: 验证语法 + 提交**

Run:
```bash
cd C:/Users/felji/PycharmProjects/VibeOCR && python -c "
import ast
ast.parse(open('src/vibeocr/views/tabs/pdf_tab.py', encoding='utf-8').read())
print('syntax OK')
"
```

```bash
git add src/vibeocr/views/tabs/pdf_tab.py
git commit -m "feat(ui): 打开 PDF 时检测未完成 sidecar 并提示续传"
```

---

### Task 8: 端到端回归验证

**Files:** 无新增，仅运行验证

- [ ] **Step 1: 运行全部相关测试**

Run:
```bash
cd C:/Users/felji/PycharmProjects/VibeOCR && pytest tests/utils/test_ocr_sidecar.py tests/services/test_pdf_service_save_incremental.py tests/managers/test_pdf_session_manager.py -v
```
Expected: 全部 PASS

- [ ] **Step 2: 体积验证（若有测试 PDF）**

若有测试用 PDF（检查 `tests/fixtures/` 或项目根），手动跑一次完整 OCR，对比最终文件与现有"手动保存"结果体积应一致（聚合压缩生效）。若无测试 PDF，此步跳过，依赖 Task 6C 的逻辑保证。

- [ ] **Step 3: 语法全量检查**

Run:
```bash
cd C:/Users/felji/PycharmProjects/VibeOCR && python -c "
import ast
for f in ['src/vibeocr/utils/ocr_sidecar.py', 'src/vibeocr/services/pdf_service.py', 'src/vibeocr/ipc/schemas.py', 'src/vibeocr/services/pdf_backend_process.py', 'src/vibeocr/services/pdf_backend_client.py', 'src/vibeocr/managers/pdf_session_manager.py', 'src/vibeocr/views/tabs/pdf_tab.py']:
    ast.parse(open(f, encoding='utf-8').read())
print('all syntax OK')
"
```
Expected: `all syntax OK`

- [ ] **Step 4: 最终提交（若有遗留改动）**

```bash
git status
# 若有未提交改动：
git add -A && git commit -m "test: 端到端回归验证通过"
```

---

## 自审备注（实现者必读）

1. **`session.file_path`**：`PdfSession` 有 `file_path: str` 属性（`models/pdf_session.py:22`），`_run_ocr` 内的 `session` 变量（`:877`）直接用 `session.file_path` 即可（与 `start_ocr` 用法一致）。
2. **Task 7A-3 的 `Colors.error`**：若不存在改用 `#dc2626` 或现有错误色。
3. **Task 6D-3 的 runner 启动方式**：`start_ocr` 内部启动 runner 的具体代码需 grep 确认，`page_indices` 过滤插在 runner 构造前。
4. **`save_incremental` 契约**：成功/失败都不 close doc，调用方（Task 4 路由层）无需处理 doc 替换。失败时文件从 `.bak` 回滚，doc 内存文字层保留可用。

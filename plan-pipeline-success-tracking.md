# 管道识别成功记录 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用"管道是否识别成功过"替代"扫描模型文件"来判断超时策略和用户提示，同时删除模型下载弹窗和相关基础设施。

**Architecture:** 在现有 `.vibeocr/cache.json` 中新增 `pipeline_success` 字段，由 `OCRServiceSubprocess` 在识别成功时写入、识别前读取。删除 `model_cache_manager.py`、`model_download_service.py`、`model_download_dialog.py` 和自定义管道 YAML。

**Tech Stack:** Python, PySide6, PaddleX

---

### Task 1: 创建 pipeline_success 工具函数

**Files:**
- Create: `src/vibeocr/pipeline_status.py`
- Test: `tests/test_pipeline_status.py`

这是核心数据层，后续所有任务依赖它。

- [ ] **Step 1: Write the failing test**

```python
"""tests/test_pipeline_status.py"""
import json
from pathlib import Path

from vibeocr.machine_cache import generate_machine_id
from vibeocr.pipeline_status import (
    mark_pipeline_success,
    is_pipeline_ever_succeeded,
    PIPELINE_NAMES,
)


def _make_cache(tmp_path: Path, pipeline_success: dict | None = None) -> Path:
    cache_file = tmp_path / ".vibeocr" / "cache.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 1,
        "machine_id": generate_machine_id(),
    }
    if pipeline_success is not None:
        data["pipeline_success"] = pipeline_success
    cache_file.write_text(json.dumps(data), encoding="utf-8")
    return cache_file


def test_not_succeeded_when_no_cache(tmp_path):
    assert is_pipeline_ever_succeeded("OCR", tmp_path) is False


def test_not_succeeded_when_field_missing(tmp_path):
    _make_cache(tmp_path)
    assert is_pipeline_ever_succeeded("OCR", tmp_path) is False


def test_not_succeeded_when_false(tmp_path):
    _make_cache(tmp_path, {"OCR": False})
    assert is_pipeline_ever_succeeded("OCR", tmp_path) is False


def test_succeeded_when_true(tmp_path):
    _make_cache(tmp_path, {"OCR": True})
    assert is_pipeline_ever_succeeded("OCR", tmp_path) is True


def test_other_pipeline_unaffected(tmp_path):
    _make_cache(tmp_path, {"OCR": True})
    assert is_pipeline_ever_succeeded("table_recognition", tmp_path) is False


def test_mark_success_creates_field(tmp_path):
    _make_cache(tmp_path)
    mark_pipeline_success("OCR", tmp_path)
    assert is_pipeline_ever_succeeded("OCR", tmp_path) is True


def test_mark_success_preserves_existing(tmp_path):
    _make_cache(tmp_path, {"OCR": True})
    mark_pipeline_success("table_recognition", tmp_path)
    assert is_pipeline_ever_succeeded("OCR", tmp_path) is True
    assert is_pipeline_ever_succeeded("table_recognition", tmp_path) is True


def test_machine_id_mismatch_returns_false(tmp_path):
    cache_file = tmp_path / ".vibeocr" / "cache.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps({
        "version": 1,
        "machine_id": "wrong_id",
        "pipeline_success": {"OCR": True},
    }), encoding="utf-8")
    assert is_pipeline_ever_succeeded("OCR", tmp_path) is False


def test_pipeline_names_constant():
    assert "OCR" in PIPELINE_NAMES
    assert "table_recognition" in PIPELINE_NAMES
    assert "formula_recognition" in PIPELINE_NAMES
    assert "MinerU" not in PIPELINE_NAMES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pipeline_status.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'vibeocr.pipeline_status'`

- [ ] **Step 3: Write minimal implementation**

```python
"""src/vibeocr/pipeline_status.py"""
import json
import logging
from pathlib import Path

from vibeocr.machine_cache import generate_machine_id

_logger = logging.getLogger(__name__)

PIPELINE_NAMES = {"OCR", "table_recognition", "formula_recognition"}


def _cache_path(project_root: Path) -> Path:
    return project_root / ".vibeocr" / "cache.json"


def _read_cache(project_root: Path) -> dict | None:
    path = _cache_path(project_root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("machine_id") != generate_machine_id():
            return None
        return data
    except (json.JSONDecodeError, OSError):
        return None


def _write_cache(project_root: Path, data: dict) -> None:
    path = _cache_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_pipeline_ever_succeeded(pipeline_name: str, project_root: Path) -> bool:
    cache = _read_cache(project_root)
    if cache is None:
        return False
    return bool(cache.get("pipeline_success", {}).get(pipeline_name, False))


def mark_pipeline_success(pipeline_name: str, project_root: Path) -> None:
    cache = _read_cache(project_root)
    if cache is None:
        cache = {"version": 1, "machine_id": generate_machine_id()}
    ps = cache.setdefault("pipeline_success", {})
    ps[pipeline_name] = True
    _write_cache(project_root, cache)
    _logger.debug("管道 %s 标记为已成功", pipeline_name)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pipeline_status.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/vibeocr/pipeline_status.py tests/test_pipeline_status.py
git commit -m "feat: 添加 pipeline_status 管道成功记录模块"
```

---

### Task 2: 改造 OCRServiceSubprocess 使用 pipeline_status

**Files:**
- Modify: `src/vibeocr/services/ocr_service_subprocess.py`
- Modify: `src/vibeocr/workers/ocr_worker.py` (删除 `is_pipeline_cached` import)

这一步将超时判断从 `is_pipeline_cached` 切换到 `is_pipeline_ever_succeeded`。

- [ ] **Step 1: 修改 ocr_service_subprocess.py**

替换顶部 import：

```python
# 删除这行:
from vibeocr.model_cache_manager import is_pipeline_cached

# 替换为:
from vibeocr.pipeline_status import is_pipeline_ever_succeeded, mark_pipeline_success
```

修改 `_calculate_recognize_timeout` 方法（约 line 320-359），将 `is_pipeline_cached(pipeline_name_str)` 替换为 `is_pipeline_ever_succeeded(pipeline_name_str, Path.cwd())`。需要在文件顶部加 `from pathlib import Path`（如果还没有的话），但更好的方式是传入 `project_root`。

修改 `recognize` 方法，在成功返回前调用 `mark_pipeline_success`。找到 `recognize` 方法末尾的 return 语句，在返回前加：

```python
        result = self._paddlex_manager.execute(
            lambda w: w.recognize(image_data, options_dict, timeout=timeout),
            timeout=timeout,
        )
        # 标记管道识别成功
        pipeline_name = options_dict.get("pipeline", "OCR")
        if pipeline_name in ("OCR", "table_recognition", "formula_recognition"):
            from vibeocr.env_manager import get_project_root
            mark_pipeline_success(pipeline_name, get_project_root())
        return result
```

同样对 MinerU 分支（`_is_document_parsing` 为 True 时）的返回前也加标记——但 MinerU 不纳入，所以不加。

修改 `_calculate_recognize_timeout` 方法中的调用：

```python
# 原代码（约 line 351）:
        if is_pipeline_cached(pipeline_name_str):

# 改为:
        if is_pipeline_ever_succeeded(pipeline_name_str, self._get_project_root()):
```

添加辅助方法：

```python
    @staticmethod
    def _get_project_root() -> Path:
        from vibeocr.env_manager import get_project_root
        return get_project_root()
```

在文件顶部 import 区域加 `from pathlib import Path`（如果缺失）。

- [ ] **Step 2: 修改 ocr_worker.py**

删除 `from vibeocr.model_cache_manager import is_pipeline_cached` 这行 import。

找到 `ocr_worker_process.py` 中使用 `is_pipeline_cached` 的地方（约 line 674），将：

```python
            if not is_pipeline_cached(name):
```

改为：

```python
            if not is_pipeline_ever_succeeded(name, self._get_project_root()):
```

添加 import：

```python
from vibeocr.pipeline_status import is_pipeline_ever_succeeded
```

并在文件中找到或添加 `_get_project_root` 辅助方法（和上面一样的模式）。

- [ ] **Step 3: 验证导入无误**

Run: `python -c "from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess; print('OK')"`
Expected: OK（不运行完整测试，因为需要 PaddleX 环境）

- [ ] **Step 4: Commit**

```bash
git add src/vibeocr/services/ocr_service_subprocess.py src/vibeocr/workers/ocr_worker.py src/vibeocr/workers/ocr_worker_process.py
git commit -m "refactor: 超时判断改用 pipeline_status 替代 model_cache_manager"
```

---

### Task 3: 改造 OCRService（非子进程模式）

**Files:**
- Modify: `src/vibeocr/services/ocr_service.py`

`ocr_service.py` 是非子进程模式下的服务，也需要同样的改造。

- [ ] **Step 1: 修改 import**

删除（约 line 96-99）：

```python
from vibeocr.model_cache_manager import (
    is_pipeline_cached,
    quick_check_all_models,
)
```

替换为：

```python
from vibeocr.pipeline_status import is_pipeline_ever_succeeded, mark_pipeline_success
```

- [ ] **Step 2: 修改 `_create_pipeline` 方法**

将 `is_pipeline_cached(pipeline_name)` 调用（约 line 612）替换为 `is_pipeline_ever_succeeded(pipeline_name, self._get_project_root())`。

添加 `_get_project_root` 静态方法：

```python
    @staticmethod
    def _get_project_root():
        from vibeocr.env_manager import get_project_root
        from pathlib import Path
        return get_project_root()
```

- [ ] **Step 3: 修改 `recognize` 方法**

在 `recognize` 方法的 return 前加成功标记。找到 `_recognize_ocr` / `_recognize_table` / `_recognize_formula` 调用后的 return 语句区域（约 line 730-758），在结果返回前加：

```python
            # 标记成功
            pipeline_val = actual_options.pipeline.value
            if pipeline_val in ("OCR", "table_recognition", "formula_recognition"):
                mark_pipeline_success(pipeline_val, self._get_project_root())
```

- [ ] **Step 4: 修改 `preload_model_cache` 方法**

将 `quick_check_all_models()` 调用改为返回空字典或删除。因为这个方法在子进程模式下不会被调用，而非子进程模式也不需要扫描模型了。简化为：

```python
    @classmethod
    def preload_model_cache(cls) -> dict[str, bool]:
        return {}
```

- [ ] **Step 5: 验证导入无误**

Run: `python -c "from vibeocr.services.ocr_service import OCRService; print('OK')"`
Expected: OK

- [ ] **Step 6: Commit**

```bash
git add src/vibeocr/services/ocr_service.py
git commit -m "refactor: OCRService 超时判断改用 pipeline_status"
```

---

### Task 4: 删除 model_cache_manager 及相关文件

**Files:**
- Delete: `src/vibeocr/model_cache_manager.py`
- Delete: `config/pipelines/OCR.yaml`
- Delete: `config/pipelines/table_recognition.yaml`
- Delete: `config/pipelines/table_recognition_v2.yaml`
- Delete: `config/pipelines/formula_recognition.yaml`
- Delete: `tests/test_model_cache.py`

- [ ] **Step 1: 确认无残留引用**

Run: `grep -r "model_cache_manager\|from vibeocr\.model_cache" src/ tests/ --include="*.py" -l`
Expected: 无输出（所有引用已在 Task 2/3 中清理）

- [ ] **Step 2: 删除文件**

```bash
rm src/vibeocr/model_cache_manager.py
rm config/pipelines/OCR.yaml
rm config/pipelines/table_recognition.yaml
rm config/pipelines/table_recognition_v2.yaml
rm config/pipelines/formula_recognition.yaml
rm tests/test_model_cache.py
rmdir config/pipelines 2>/dev/null || true
```

- [ ] **Step 3: 验证无导入错误**

Run: `python -c "from vibeocr.services.ocr_service import OCRService; from vibeocr.services.ocr_service_subprocess import OCRServiceSubprocess; print('OK')"`
Expected: OK

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore: 删除 model_cache_manager 和自定义管道 YAML 配置"
```

---

### Task 5: 删除模型下载弹窗和服务

**Files:**
- Delete: `src/vibeocr/widgets/model_download_dialog.py`
- Delete: `src/vibeocr/services/model_download_service.py`
- Delete: `tests/services/test_model_download_service.py`

- [ ] **Step 1: 确认无残留引用**

Run: `grep -r "model_download_dialog\|model_download_service\|ModelDownloadDialog\|ModelDownloadService" src/ tests/ --include="*.py" -l`
Expected: 只剩下要删除的文件本身

- [ ] **Step 2: 删除文件**

```bash
rm src/vibeocr/widgets/model_download_dialog.py
rm src/vibeocr/services/model_download_service.py
rm tests/services/test_model_download_service.py
```

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "chore: 删除模型下载弹窗和 ModelDownloadService"
```

---

### Task 6: 修改 main_window.py — 删除安装后弹窗

**Files:**
- Modify: `src/vibeocr/views/main_window.py`

- [ ] **Step 1: 简化 `_on_install_succeeded` 方法**

将（约 line 541-550）：

```python
    @Slot()
    def _on_install_succeeded(self) -> None:
        """安装成功后弹出模型下载对话框"""
        from vibeocr.widgets.model_download_dialog import ModelDownloadDialog

        self._ocr_ready = True
        self._statusbar.showMessage("OCR依赖安装成功，正在下载模型...")

        dialog = ModelDownloadDialog(self._project_root, self)
        dialog.exec()
```

改为：

```python
    @Slot()
    def _on_install_succeeded(self) -> None:
        """安装成功后标记就绪"""
        self._ocr_ready = True
        self._statusbar.showMessage("OCR依赖安装成功，首次识别将自动下载模型")
```

- [ ] **Step 2: 验证导入无误**

Run: `python -c "from vibeocr.views.main_window import MainWindow; print('OK')"` 或检查语法。

- [ ] **Step 3: Commit**

```bash
git add src/vibeocr/views/main_window.py
git commit -m "refactor: 安装成功后不再弹模型下载窗口"
```

---

### Task 7: 修改设置页 — 删除下载模型按钮

**Files:**
- Modify: `src/vibeocr/views/settings_page_controller.py`
- Modify: `src/vibeocr/ui/main_window.ui`

- [ ] **Step 1: 修改 settings_page_controller.py**

删除按钮绑定代码（约 line 83-85）：

```python
        btn_download_models = self._ui.findChild(QPushButton, "btnDownloadModels")
        if btn_download_models:
            btn_download_models.clicked.connect(self._on_download_models_clicked)
```

删除 `_on_refresh_cache_clicked` 中的模型缓存更新（约 line 316）：

```python
        from vibeocr.model_cache_manager import update_cache as update_model_cache
```

和（约 line 320）：

```python
        update_model_cache()
```

保留 `refresh_cache` 调用不变。

删除整个 `_on_download_models_clicked` 方法（约 line 356-361）：

```python
    def _on_download_models_clicked(self) -> None:
        """下载模型按钮点击"""
        from vibeocr.widgets.model_download_dialog import ModelDownloadDialog

        dialog = ModelDownloadDialog(self._project_root, None)
        dialog.exec()
```

- [ ] **Step 2: 删除 UI 文件中的下载模型 GroupBox**

在 `src/vibeocr/ui/main_window.ui` 中，删除整个 `groupModelDownload` widget（约 line 624-661）：

```xml
           <item>
            <widget class="QGroupBox" name="groupModelDownload">
             ...
            </widget>
           </item>
```

删除从 `<item>` 开始到对应的 `</item>` 的整个块（line 624 到 661）。

- [ ] **Step 3: Commit**

```bash
git add src/vibeocr/views/settings_page_controller.py src/vibeocr/ui/main_window.ui
git commit -m "refactor: 删除设置页的下载模型按钮"
```

---

### Task 8: UI 层 — 识别前提示首次使用

**Files:**
- Modify: `src/vibeocr/views/tabs/single_recognition_tab.py`
- Modify: `src/vibeocr/views/batch_recognition_tab.py`

- [ ] **Step 1: 在 SingleRecognitionTab 中添加首次提示**

在 `run_ocr` 方法中，`self._result_widget.clear()` 之后、开始识别之前，加入首次使用检测：

```python
    def run_ocr(self, pixmap: QPixmap, options=None) -> None:
        """执行 OCR 识别"""
        from vibeocr.services import USE_SUBPROCESS

        self._preprocess_options.unlock_pipeline()

        if pixmap.devicePixelRatio() != 1.0:
            pixmap = QPixmap(pixmap)
            pixmap.setDevicePixelRatio(1.0)

        self._result_widget.clear()

        # 首次使用提示
        if options is None:
            options = self._build_options_from_ui()
        pipeline_val = options.pipeline.value
        if pipeline_val in ("OCR", "table_recognition", "formula_recognition"):
            from vibeocr.pipeline_status import is_pipeline_ever_succeeded
            from vibeocr.env_manager import get_project_root
            if not is_pipeline_ever_succeeded(pipeline_val, get_project_root()):
                self._result_widget.show_hint(
                    "正在识别，首次使用可能需要下载模型，请耐心等待…"
                )

        QApplication.processEvents()
        # ... 后续代码不变
```

注意：需要确认 `ResultViewWidget` 是否有 `show_hint` 方法。如果没有，可以用 `setPlainText` 或直接在 `clear` 之后设置一段提示文字。检查 `result_view_widget.py` 确认可用方法，然后选择合适的方式设置提示文字。

- [ ] **Step 2: 在 BatchRecognitionTab 中添加首次提示**

在 `_on_start` 方法中，`self._result_widget.clear()` 之后加类似逻辑：

```python
        self._result_widget.clear()

        # 首次使用提示
        pipeline_val = preprocess_options.pipeline.value
        if pipeline_val in ("OCR", "table_recognition", "formula_recognition"):
            from vibeocr.pipeline_status import is_pipeline_ever_succeeded
            from vibeocr.env_manager import get_project_root
            if not is_pipeline_ever_succeeded(pipeline_val, get_project_root()):
                self._result_widget.show_hint(
                    "正在识别，首次使用可能需要下载模型，请耐心等待…"
                )
```

- [ ] **Step 3: Commit**

```bash
git add src/vibeocr/views/tabs/single_recognition_tab.py src/vibeocr/views/batch_recognition_tab.py
git commit -m "feat: 识别前显示首次使用提示"
```

---

### Task 9: 识别失败时提示保持网络

**Files:**
- Modify: `src/vibeocr/views/tabs/single_recognition_tab.py`
- Modify: `src/vibeocr/views/batch_recognition_tab.py`

- [ ] **Step 1: 在 SingleRecognitionTab 的错误处理中加提示**

找到 `_on_ocr_error` 方法或在异常处理中（约 line 312-313）：

```python
            logger.error(f"[异步OCR] 识别失败: {e}", exc_info=True)
            self._on_ocr_error(str(e))
```

修改错误消息，追加提示。在 `single_recognition_tab.py` 中找到所有 `self._on_ocr_error` 调用，检查是否需要在错误消息中追加"请保持网络畅通后重试"提示。

根据现有 `_on_ocr_error` 的实现方式（可能是显示错误文本到结果区域），如果管道从未成功过，在错误消息后追加：

```python
            pipeline_val = options.pipeline.value
            suffix = ""
            if pipeline_val in ("OCR", "table_recognition", "formula_recognition"):
                from vibeocr.pipeline_status import is_pipeline_ever_succeeded
                from vibeocr.env_manager import get_project_root
                if not is_pipeline_ever_succeeded(pipeline_val, get_project_root()):
                    suffix = "\n\n提示：首次使用需要下载模型，请保持网络畅通后重试。"
            self._on_ocr_error(str(e) + suffix)
```

- [ ] **Step 2: 对 BatchRecognitionTab 做类似处理**

找到 batch tab 中的错误处理，加同样的首次失败提示。

- [ ] **Step 3: Commit**

```bash
git add src/vibeocr/views/tabs/single_recognition_tab.py src/vibeocr/views/batch_recognition_tab.py
git commit -m "feat: 首次识别失败时提示保持网络畅通"
```

---

### Task 10: 清理残留引用和测试

**Files:**
- 可能修改: 多个文件（清理残留 import）

- [ ] **Step 1: 全局搜索残留引用**

```bash
grep -rn "model_cache_manager\|model_download\|ModelDownloadDialog\|ModelDownloadService\|is_pipeline_cached\|quick_check_all_models" src/ tests/ --include="*.py"
```

Expected: 无输出。如果有残留，逐一清理。

- [ ] **Step 2: 运行现有测试**

Run: `python -m pytest tests/ -v --timeout=30 -x`
Expected: 所有测试通过（排除需要 PaddleX 环境的集成测试）

- [ ] **Step 3: 最终 Commit**

```bash
git add -A
git commit -m "chore: 清理残留引用，确保所有测试通过"
```

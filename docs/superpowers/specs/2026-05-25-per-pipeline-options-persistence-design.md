# 按管道独立 OCR 选项设置与持久化

## 目标

主界面和截图面板的 OCR 选项按管道独立存储和恢复，用户对每个管道的预处理/高级选项配置互不干扰，且截图面板按钮直接使用持久化的选项而非默认值。

## 背景

当前 `InlineRecognitionPanel`（截图面板）点击按钮时 `get_options()` 只返回 `OCROptions(pipeline=选中的管道)`，所有选项都用默认值。主界面的 `PreprocessOptionsWidget` 通过 `OCRPreferences` 做持久化，但存的是一整份 `OCROptions`，切换管道会覆盖上一个管道的设置。

## 数据层：OCRPreferences 改造

### 文件结构

`config/ocr_preferences.json` 版本升级到 2：

```json
{
  "version": 2,
  "main": {
    "OCR": { "pipeline": "OCR", "use_doc_orientation_classify": true, ... },
    "PP-StructureV3": { ... },
    "TABLE_RECOGNITION": { ... }
  },
  "screenshot": {
    "OCR": { ... },
    "PP-StructureV3": { ... }
  },
  "batch_options": { ... }
}
```

- `"main"` — 主界面按管道独立的选项
- `"screenshot"` — 截图面板按管道独立的选项
- `"batch_options"` — 批量识别选项，保持不变
- 仅保存用户实际修改过的管道选项（首次使用时字典为空，按需填充）

### 新增 API

```python
def get_pipeline_options(self, source: str, pipeline: OCRPipeline) -> OCROptions:
    """读取指定区域指定管道的选项，不存在则返回默认 OCROptions"""

def set_pipeline_options(self, source: str, pipeline: OCRPipeline, options: OCROptions) -> None:
    """保存到指定区域并持久化，触发 pipeline_options_changed 信号"""
```

新信号：`pipeline_options_changed = Signal(str, object)` — 参数为 `(source, OCROptions)`

### 版本迁移

检测到 version < 2 时，将顶层选项移入 `"main"` 下对应管道键，然后回写。

## 主界面：PreprocessOptionsWidget 按管道独立存取

### 保存时机

1. **管道切换时**（`_on_pipeline_changed`）：先保存旧管道选项，再加载新管道选项
2. **开始识别时**（`_start_recognition`）：保存当前管道选项

### 加载逻辑

- 初始化时从 `OCRPreferences.get_pipeline_options("main", 默认管道)` 加载
- 管道切换时从 `OCRPreferences.get_pipeline_options("main", 新管道)` 加载，通过 `set_options()` 刷新 UI

### 双向同步

- 去掉 `options_changed` → `OCRPreferences.set_options` 的实时同步
- 保留 `pipeline_options_changed` 信号用于跨 Tab 同步

### 涉及文件

- `base_tab.py`：`_init_options_from_preferences` 改为按管道独立存取
- `single_recognition_tab.py`：`_start_recognition` 中调用保存
- `preprocess_options_widget.py`：`_on_pipeline_changed` 中增加保存旧管道逻辑

## 截图面板：InlineRecognitionPanel 读取持久化选项

### 改动

1. 初始化时从 `OCRPreferences.get_pipeline_options("screenshot", OCR)` 加载默认选中管道
2. 按钮点击时从 `get_pipeline_options("screenshot", 新管道)` 读取该管道的选项
3. `get_options()` 返回持久化的 `OCROptions` 而非默认值
4. 按钮的 tooltip 动态显示该管道当前布尔选项状态

### Tooltip 格式

仅显示该管道 `supported_options` 中的布尔选项：

```
OCR: "方向分类: 开 | 扭曲矫正: 开 | 文本行方向: 关"
PP-StructureV3: "方向分类: 开 | 扭曲矫正: 开 | 表格识别: 开 | 公式识别: 开 | ..."
```

### 涉及文件

- `inline_recognition_panel.py`

## 设置页：截图面板选项区域

### 布局

在设置页 stacked widget 中新增区域，包含：
- 标题："截图面板识别选项"
- 一个 `PreprocessOptionsWidget` 实例

### 数据绑定

- 初始化时从 `OCRPreferences.get_pipeline_options("screenshot", 默认管道)` 加载
- 管道切换时：保存旧管道选项 → 加载新管道选项
- 选项变更时：立即保存（设置页适合实时保存）

### 涉及文件

- `settings_page_controller.py`：持有截图面板的 `PreprocessOptionsWidget` 引用，连接保存逻辑
- `ui_main_window.py`：设置页新增截图面板选项 UI 区域

## 数据流

```
设置页编辑截图选项
  → PreprocessOptionsWidget (截图实例)
  → OCRPreferences.set_pipeline_options("screenshot", pipeline, options)
  → ocr_preferences.json

主界面编辑选项
  → PreprocessOptionsWidget (主界面实例)
  → 管道切换/开始识别时
  → OCRPreferences.set_pipeline_options("main", pipeline, options)
  → ocr_preferences.json

截图面板点击按钮
  → InlineRecognitionPanel._on_pipeline_clicked(pipeline)
  → OCRPreferences.get_pipeline_options("screenshot", pipeline)
  → confirmed 信号携带完整 options → SingleRecognitionTab.run_ocr()
```

## 涉及文件总览

| 文件 | 改动 |
|------|------|
| `ocr_preferences.py` | 数据结构改为 per_pipeline 双区域，新增 API，迁移逻辑 |
| `preprocess_options_widget.py` | 管道切换时保存旧管道选项 |
| `inline_recognition_panel.py` | 从 OCRPreferences 读取选项，tooltip 显示选项状态 |
| `settings_page_controller.py` | 接管截图面板 PreprocessOptionsWidget 实例 |
| `ui_main_window.py` | 设置页新增截图面板选项区域 |
| `base_tab.py` | `_init_options_from_preferences` 改为按管道独立存取 |
| `single_recognition_tab.py` | 开始识别时保存当前管道选项 |

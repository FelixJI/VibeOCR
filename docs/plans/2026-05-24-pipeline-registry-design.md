# 管道注册表模式设计

## 目标

将表格识别和公式识别拆分为独立的顶级管道，与"通用 OCR"、"PP-StructureV3"并列，用户可直接使用而无需经过版面分析。同时采用管道注册表模式重构现有架构，使新增管道只需注册一个 spec。

## 方案：管道注册表模式（Pipeline Registry）

### 核心组件

新增 `PipelineSpec` 和 `PipelineRegistry`：

```python
@dataclass
class PipelineSpec:
    name: str                                    # "TABLE_RECOGNITION"
    display_name: str                            # "表格识别"
    description: str
    options_class: type[BasePipelineOptions]
    create_pipeline: Callable[..., Any]          # 工厂函数
    recognize: Callable[..., OCRResult]          # 识别方法
```

### Options 层级

```
BasePipelineOptions
├── OCROptions
├── PPStructureV3Options
├── TableRecognitionOptions
├── FormulaRecognitionOptions
├── MinerUOptions
└── PaddleOCRVLOptions
```

每个 Options 类只定义自己管道需要的字段。

### 新增管道规格

#### 表格识别

- 独立使用 `TableRecognitionPipelineV2`
- 选项：预处理（方向分类、扭曲矫正）、表格类型（有线/无线）、模型名称/路径、方向分类、单元格 OCR 配置、HTML 输出模式、端到端模式
- 模型：`SLANeXt_wireless` / `SLANeXt_wired`

#### 公式识别

- 使用 PaddleOCR 公式识别独立模型（如无独立 pipeline 类，则组合版面检测+公式提取）
- 选项：预处理、公式模型名称/路径/批量大小

### 文件结构

```
src/vibeocr/core/pipelines/
├── __init__.py              # 导出注册表和所有 spec
├── registry.py              # PipelineRegistry + PipelineSpec
├── base_options.py          # BasePipelineOptions
├── pipeline_ocr.py          # OCROptions + spec
├── pipeline_pp_structure.py # PPStructureV3Options + spec
├── pipeline_table.py        # TableRecognitionOptions + spec
├── pipeline_formula.py      # FormulaRecognitionOptions + spec
├── pipeline_mineru.py       # MinerUOptions + spec
└── pipeline_paddlocr_vl.py  # PaddleOCRVLOptions + spec
```

### OCRService 改造

- `recognize()` 基于注册表分发，`OCRService` 只负责管道缓存管理和通用后处理（bbox 归一化等）
- 管道特定逻辑（创建实例、解析输出）下沉到各 spec 的 `create_pipeline` 和 `recognize`

### UI 适配

1. 管道下拉菜单数据源从枚举改为 `registry.list_all()`
2. 根据选中管道的 `spec.options_class` 动态生成配置 UI
3. 新增 `TableRecognitionOptionsWidget` 和 `FormulaRecognitionOptionsWidget`
4. 设置存储按管道独立序列化，兼容旧格式自动迁移
5. 预处理/预览机制复用

### 向后兼容

- 现有 `pipelines.py` 和 `ocr_options.py` 标记 deprecated，重新导出新模块内容
- 设置读取时检测旧格式并自动迁移

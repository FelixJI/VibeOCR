# 管道识别成功记录 — 设计文档

## 背景

当前项目通过 `model_cache_manager.py` 扫描 `~/.paddlex/official_models/` 目录判断模型是否已下载，
并维护自定义 YAML 配置文件（`config/pipelines/*.yaml`）列出各管道所需模型名。
这套机制存在两个问题：

1. 自定义 YAML 可能与 PaddleX 官方内置配置不一致，导致缓存判断误报
2. `ModelDownloadDialog` 在安装后弹出一个无真实进度的下载窗口，用户体验不佳

## 目标

用"管道是否识别成功过"替代"扫描模型文件"：
- 成功过 → 模型一定已下载 → 短超时
- 未成功过 → 可能需要下载 → 长超时 + 提示用户保持网络

同时删除模型下载弹窗和相关基础设施，简化代码。

## 删除项

| 文件/组件 | 原因 |
|-----------|------|
| `widgets/model_download_dialog.py` | 弹窗不再需要 |
| `services/model_download_service.py` | 下载由 PaddleX 自动处理 |
| `model_cache_manager.py` | 替换为简单的成功记录 |
| `config/pipelines/*.yaml`（4 个文件） | 不再需要自定义模型配置 |
| 设置页"下载模型"按钮（`btnDownloadModels`） | UI 入口移除 |
| `main_window.py` 中 `_on_install_succeeded` 的模型下载弹窗逻辑 | 安装后不再弹下载窗 |

保留：`InstallDialog`（依赖安装弹窗）不动。

## 数据结构

在现有 `.vibeocr/cache.json` 中新增 `pipeline_success` 字段：

```json
{
  "version": 1,
  "machine_id": "abc123...",
  "pipeline_success": {
    "OCR": true,
    "table_recognition": false,
    "formula_recognition": false
  }
}
```

- `MinerU` 管道不纳入（它通过外部 API 调用，不依赖本地模型下载）
- `machine_id` 变化时整个 cache 作废（已有逻辑），所有管道回到"首次使用"

## 核心行为

### 读取（超时 + 提示决策）

`OCRServiceSubprocess.recognize()` 调用前：

1. 读取 `cache.json` 中 `pipeline_success[pipeline_name]`
2. 如果从未成功（`false` 或不存在）：
   - 通过 `status_callback` 通知 UI："首次使用，正在下载模型并识别，请保持网络畅通…"
   - 超时 600s
3. 如果已成功过（`true`）：
   - 无额外提示
   - 超时 60s

### 写入（标记成功）

`OCRServiceSubprocess.recognize()` 成功返回后：

1. 读取 `cache.json`
2. 设置 `pipeline_success[pipeline_name] = true`
3. 写回 `cache.json`

### 失败处理

- 识别失败时不修改 `pipeline_success`
- 下次仍按"首次"处理（长超时 + 提示）
- 失败错误信息中提示用户保持网络畅通后重试

### 安装成功后

`_on_install_succeeded` 简化为：
- 标记 `_ocr_ready = True`
- 状态栏提示"依赖安装成功，首次识别将自动下载模型"

## UI 层改动

### Tab 层（SingleRecognitionTab、BatchRecognitionTab）

识别开始前：
- 如果管道从未成功，在结果区域先显示提示文字："正在识别，首次使用可能需要下载模型，请耐心等待…"
- 识别完成后，提示被识别结果替换

### 设置页

- 删除"下载模型"按钮及其 `_on_download_models_clicked` 处理
- 删除 `_on_refresh_cache_clicked` 中 `update_model_cache()` 调用
- 其余缓存管理功能（依赖缓存刷新/清除）保留

## 受影响的调用点

| 文件 | 当前调用 | 改为 |
|------|---------|------|
| `ocr_service_subprocess.py` | `is_pipeline_cached()` | 查 `cache.json` 的 `pipeline_success` |
| `ocr_service.py` | `is_pipeline_cached()` + `quick_check_all_models()` | 同上 |
| `ocr_worker_process.py` | `is_pipeline_cached()` | 同上 |
| `model_download_service.py` | `update_cache()` | 删除整个文件 |
| `settings_page_controller.py` | `update_cache()` + `ModelDownloadDialog` | 删除相关代码 |
| `main_window.py` | `ModelDownloadDialog` | 删除弹窗，简化 `_on_install_succeeded` |

## 不变的部分

- PaddleX 的自动下载机制（`create_pipeline` 内部处理）
- `InstallDialog` 依赖安装流程
- `machine_cache.py` 的机器码和缓存文件管理
- `network_detector.py` 的模型源检测

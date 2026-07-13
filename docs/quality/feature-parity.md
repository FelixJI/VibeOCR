# WinUI 功能对等矩阵

此矩阵是 PySide 正式切换前的活文档。状态仅在自动化证据或指定人工矩阵通过后更新。

| 功能 | PySide 语义真源 | WinUI 状态 | 自动化证据 | 待人工签核 |
|---|---|---|---|---|
| 单图输入（文件/剪贴板/全桌面/拖放） | `SingleRecognitionTab` | PASS | `RecognitionViewModelTests` | 多显示器混合 DPI |
| 取消、generation 丢弃、Worker 崩溃重试 | OCR/WorkerHost | PASS | `RecognitionViewModelTests` | 长任务取消体感 |
| 预览编辑（六类标注、移动、缩放、旋转、裁剪、撤销/重做） | PySide editor | PASS | `tests/web/editor.test.ts` | 触控笔与高 DPI |
| 结果渲染（plain/Markdown/表格/公式/代码/Unicode/XSS） | `ResultViewWidget` | PASS | `tests/web/result-renderer.test.ts` | 高对比度与屏幕阅读器 |
| 复制（富文本/Markdown/纯文本） | `ClipboardController` | PASS | `ResultActionsTests` | Office 粘贴矩阵 |
| 导出（HTML/Markdown/text） | Python `ExportService` | PASS | `single-recognition.spec.ps1` | 系统 picker 覆盖提示 |
| 批量识别 | `BatchRecognitionTab` | PASS | `BatchViewModelTests`、`batch.spec.ps1` | 大队列体感与并发预算上限 |
| 二维码/条码 | QR services | PASS | `QrCodeViewModelTests`、`qrcode.spec.ps1` | 多码与高 DPI 渲染 |
| PDF 会话与耐久文字层 | PDF orchestrator/sidecar | PASS | `PdfViewModelTests`、`pdf.spec.ps1`、`test_pdf_ocr_orchestrator.py` | 多批 PDF 崩溃续传、旋转全部、自动摆正 |
| 设置与诊断 | Config/diagnostics | PASS | `SettingsViewModelTests`、`settings.switch_backend`/`settings.install_dependency` 协议 | 真实 GPU 切换与镜像网络 |

单图门禁命令：`powershell -File tests/e2e/winui/single-recognition.spec.ps1`。它同时验证 Python 导出真源、C# 命令状态与剪贴板重试、Web 语义渲染和 Unicode/XSS 夹具。

批量门禁命令：`powershell -File tests/e2e/winui/batch.spec.ps1`。它验证 Python 批量队列的顺序与取消（含 PENDING 标记为 CANCELLED）、C# `BatchViewModel` 的去重/并发预算/单项与全部取消/失败继续/导出/重启不恢复，以及批量导出的唯一输出路径。

二维码门禁命令：`powershell -File tests/e2e/winui/qrcode.spec.ps1`。它验证 Python 解码（严格 http/https URL 判定）与生成（QR/条码）真源、WorkerHost `is_url` 字段跨语言一致传播，以及 C# `QrCodeViewModel` 的图片/剪贴板输入、多码/无结果、URL 安全过滤、QR/条码生成与保存（覆盖确认、选择器取消）。`qrcode.decode` 响应的 `is_url` 由 Python 解码服务计算，C# 仅读取，不在客户端重判 URL 安全性。

PDF 门禁命令：`powershell -File tests/e2e/winui/pdf.spec.ps1`。它验证 Python OCR 编排器（逐批 save+sidecar 续传、页边界取消、末尾压缩、写层错误聚合、winui-dev sidecar 隔离）、WorkerHost `pdf.*` 协议与处理器，以及 C# `PdfViewModel` 的打开/旋转（选中/全部）/OCR/删除文字层/保存。按钮语义以当前 `main` 为准（`顺时针90°`/`逆时针90°`/`旋转全部`+90°/`自动摆正`，单绿色页状态）；编排器额外暴露三色文字层来源投影供未来对齐。

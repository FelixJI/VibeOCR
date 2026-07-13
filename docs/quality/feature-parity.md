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
| 批量识别 | `BatchRecognitionTab` | PENDING | Phase 4.1 | — |
| 二维码/条码 | QR services | PENDING | Phase 4.2 | — |
| PDF 会话与耐久文字层 | PDF orchestrator/sidecar | PENDING | Phase 4.3 | — |
| 设置与诊断 | Config/diagnostics | PENDING | Phase 4.4–4.5 | — |

单图门禁命令：`powershell -File tests/e2e/winui/single-recognition.spec.ps1`。它同时验证 Python 导出真源、C# 命令状态与剪贴板重试、Web 语义渲染和 Unicode/XSS 夹具。

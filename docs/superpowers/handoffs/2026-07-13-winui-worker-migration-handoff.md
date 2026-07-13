# WinUI 3 Worker 迁移交接（2026-07-13）

## 合并边界

- 目标分支：`main`
- 已验证成果截止提交：`84802b3 test(winui): close single-recognition parity loop`
- 主分支合并提交：`8fc538d merge: WinUI worker migration phases 2-3`
- 已进入主分支：Phase 2 全部任务、Phase 3 单图识别闭环与质量记录。
- 未进入主分支：Phase 4.1 批量识别草稿 `dc4e9ad wip(winui): begin batch recognition parity`。该提交保留在 `codex/winui-worker-migration` 和工作树 `C:\tmp\VibeOCR-winui-phase2`，尚未完成测试，不得视为可发布成果。

此次合并没有切换正式入口。`production` 继续运行现有 PySide6 UI；WinUI 使用隔离的 `winui-dev` profile，正式 config/model/output/sidecar 不应被旁路开发链路改写。

## 已完成能力

1. .NET solution、Contracts、WorkerHost、Bootstrapper 与 WinUI shell 基础设施。
2. 版本化 NDJSON 协议、golden contract、schema/correlation/deadline/cancel/error 映射。
3. Worker 生命周期、限额、孤儿进程清理、共享内存传输和诊断包。
4. 单图 OCR 的选图、剪贴板输入、区域截图、进度/取消/错误、预览编辑、WebView2 安全桥、结果渲染、复制与导出闭环。
5. `docs/quality/feature-parity.md` 已记录 Phase 3 自动化证据；Phase 4–5 仍为待完成状态。

## 合并前验证证据

- Python Worker/contracts：203 passed。
- .NET：App 35、Contracts 14、Platform 30，共 79 passed。
- Web：14 passed。
- Release build：0 warnings，0 errors。
- 单图 E2E：Python export、C# result actions、Web semantic rendering 共 3 项 PASS。
- 真实 GUI smoke：`BridgeReady=true`、`CopyAction=true`、`ExportAction=true`、退出码 0。

合并到 `main` 后已再次复核：

- solution Release build：0 warnings，0 errors。
- .NET App/Contracts/Platform：35 + 14 + 30，共 79 passed。
- Python Worker/contracts：203 passed。
- 当前 PDF UI 链路：92 passed。
- Web：14 passed。
- 单图跨语言 E2E：Python export、C# result actions、Web renderer 三行 PASS。

仍需在目标环境人工完成：Win10 1809、真实托盘、混合 DPI、多显示器、Office 剪贴板、键盘可达性、高对比度和辅助功能检查。这些不是 Phase 3 自动化通过所能替代的发布签核。

## PDF 链路交接真源

Task 4.3 必须以当前 `main` 的 Python/UI 行为为准，不照搬旧计划假设：

- OCR 每批写层并增量落盘，sidecar 只记录 `extra.saved=true` 的页面；保存失败回滚，失败批不可进入已完成页。
- `add_text_layer_batch` 的页面写层与持久化结果已解耦；落盘错误通过 `ocr_write_error` 收集、去重，并在本轮 OCR 结束后提示。
- 删除文字层逐页流式执行，显示不确定进度动画、当前/总数，并保留取消。
- 页面处理状态 `none/processing/done/failed` 与文字层来源 `none/native/ocr` 正交；灰色表示无层、浅绿表示原生 PDF 文字层、深绿表示 OCR 文字层。
- 旋转按钮为“顺时针90°”“逆时针90°”“全部顺时针90°”“全部逆时针90°”；没有选中页时明确提示，全页旋转不二次确认。
- “自动摆正”使用 OCR 方向检测；“横放摆正”“纵放摆正”只按考虑页面 rotation 后的宽高比处理选中页，不调用 OCR。
- 文字层入口为“添加文字层”“为无文字层页添加文字层”“删除文字层”“预览文字层”。

详细约束已同步回 `docs/superpowers/plans/2026-07-11-winui3-worker-migration.md` 的 Task 4.3。

## 继续开发

推荐顺序：

1. 从 `codex/winui-worker-migration` 的 `dc4e9ad` 继续 Task 4.1，先补 Batch 单元测试和 E2E，再决定合入。
2. 完成 Task 4.2 二维码。
3. 按更新后的 Task 4.3 迁移 PDF；不要在 C# 重写 sidecar、保存和恢复状态机。
4. 完成 Task 4.4–4.6，再进入 Phase 5 的数据迁移、打包、性能门禁和正式切换。

恢复草稿时先检查：

```powershell
git -C C:\tmp\VibeOCR-winui-phase2 show --stat dc4e9ad
git -C C:\tmp\VibeOCR-winui-phase2 status --short
```

该工作树中的 `task_plan.md`、`findings.md`、`progress.md` 是本地过程记录，未进入主分支；本文件是主分支上的正式交接记录。

## 常用验证命令

```powershell
$env:VIBEOCR_REPOSITORY_ROOT = (Get-Location).Path
dotnet restore src/dotnet/VibeOCR.slnx -p:Platform=x64
dotnet build src/dotnet/VibeOCR.slnx -c Release -p:Platform=x64 --no-restore
dotnet test src/dotnet/VibeOCR.slnx -c Release -p:Platform=x64 --no-build

.\.venv\Scripts\python.exe -m pytest tests/worker_host tests/contracts -q
C:\Users\felji\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe --test tests/web/*.test.ts
powershell -File tests/e2e/winui/single-recognition.spec.ps1
```

完整迁移尚未完成，因此不得将当前合并解释为正式 WinUI cutover 或移除旧 UI 的授权。

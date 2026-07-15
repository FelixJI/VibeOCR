# 调研记录：WinUI 开发启动与界面一致性

## 当前已知

- 用户反馈 WinUI 开发运行时似乎不会自动拉起后端。
- WinUI 各页面与 PySide6 版本差异较大，其中“单次截图”页面基本不可用。
- 本轮优先目标不是追求视觉花哨，而是恢复清晰的信息架构、稳定的主要工作流和可诊断的启动行为。
- WinUI 代码已具备 `WorkerProcessSupervisor`、`WorkerHostClient`、`DeferredWorkerHostClient`，并非从零开始。
- 正式架构要求：拿到前端互斥锁后启动专属 WorkerHost，ready 后再创建主窗口，退出时有界关闭后端。
- 当前单次识别入口集中在 `RecognitionPage.xaml`/`.xaml.cs`、`RecognitionViewModel.cs` 和 `InputService.cs`。
- `RecognitionPage.xaml` 的截图入口目前只是内容为“截图”的普通按钮，初步印证页面交互层级不足。
- `App.OnLaunched` 当前先创建并激活主窗口，随后才以 fire-and-forget 方式调用 `ConnectWorkerAfterFirstWindowAsync`；这与正式计划中的“Worker ready 后创建主窗口”不同，也会让用户在首屏阶段直接操作一个尚未连接后端的界面。
- 后端连接前还有 `PrerequisiteReport` 全量通过门槛；任一前置项缺失就直接返回，不会尝试启动 WorkerHost，错误仅进入诊断状态。
- PySide6 单次识别不是孤立按钮页：它有左右分栏（图片预览/结果）、截图与文件入口、管道与参数、结果复制/导出，以及截图后自动回到结果工作流。
- PySide6 还提供全局截图快捷入口和按 OCR/表格/公式区分的快捷截图操作；WinUI 当前能力与呈现均明显更薄。

## 待核实

- WinUI 的实际开发启动命令与启动项目。
- 后端是否完全未启动，还是启动失败但错误被吞掉。
- PySide6 单次截图页的布局、状态机、快捷操作和结果展示方式。
- WinUI 是否已有可复用的页面组件、样式和 ViewModel 命令。
- `App.xaml.cs` 在发布布局与源码开发布局下是否采用了不同的 WorkerHost 路径解析。
- WinUI 前置依赖判定是否把“可由源码工作区提供的 Python/WorkerHost”错误判为缺失。

## 已确认根因

- `AppLaunchOptions.Parse` 在没有命令行参数时固定默认 `production`。
- `winui-dev` 想使用仓库 `.venv` 与 `src/`，必须同时收到 `--profile winui-dev` 和环境变量 `VIBEOCR_REPOSITORY_ROOT`。
- 当前项目扫描未发现 Visual Studio/`dotnet run` 的启动配置自动提供上述两项，因此直接运行 WinUI 项目会按发布布局查找 `bin/.../python/python.exe` 与 `bin/.../worker`，开发工作区通常不存在这些内容。
- `ResolveWorkerRoot` 对开发环境只认显式环境变量，没有从程序集输出目录向上发现仓库的兜底逻辑。
- `RecognitionPage` 当前混入两个禁用的“开发中” Expander、Web bridge 内部状态、两排等权重复制/导出按钮；这些诊断噪音直接挤占主工作流。
- 页面没有明确标题、空态、主按钮、输入说明、结果标题或渐进式操作菜单；用户反馈“杂乱无章、基本无法使用”与代码结构一致。
- 现有测试明确把“默认 production、开发模式必须显式指定”当作预期，这个测试固化了导致源码直接运行失败的行为，需要随修复同步调整。
- `PortableLayoutTests` 已覆盖显式仓库环境变量，但没有覆盖“从 WinUI 输出目录自动发现仓库”的开发体验。
- `RecognitionViewModel` 已有可用的 `IsBusy`、`Status`、`ResultText`、取消、三类输入和结果动作，首轮页面重构可以主要集中在 XAML/少量 code-behind，不必重写 OCR 状态机。
- PySide 单次识别的关键骨架是预览与结果双栏、重新识别、选项折叠和结果动作；WinUI 暂不具备完整参数 DTO 时，应先实现清晰的核心路径，不放置不可操作的占位控件。
- WinUI 的 `CaptureScreenAsync` 当前直接抓取整个虚拟桌面，没有框选遮罩、窗口隐藏等待或区域确认；这不是单纯样式问题，是“单次截图基本无法使用”的主要功能根因。
- Web 预览资产其实已经同时具备图片区、标注工具和结果区，并支持 `preview.setImage`，但 C# 从未把输入图片发送进去；因此页面外层又套双栏，形成“Web 内双栏 + XAML 外双栏”的重复嵌套。
- 合理的短期收敛方式是：XAML 只保留页面标题、主输入动作、状态和一个完整工作台 WebView；将输入图片同步给 Web 预览，复制/导出折叠为次级动作。区域框选作为独立原生能力实现，不能用视觉调整假装完成。
- Web bridge 默认消息上限只有 64 KiB，不适合把截图 base64 直接塞进 JSON；输入预览应通过受控的本地 Web 资源端点或独立原生 Image 控件传递。
- 当前 `PreviewHost` 把唯一虚拟主机固定映射到只读 WebAssets，且资源过滤仅允许已映射的同源 URL；可以新增受控的内存图片资源路径，既避免落盘也不突破导航/网络安全边界。
- Web 资产已有独立 Node 测试（bridge/editor/result renderer），页面结构与 CSS 调整不需要新增依赖。
- 首轮 diff 显示核心改造集中在启动解析、识别输入/页面、Web 预览与新增区域选择器，没有触碰用户已有的 lock 文件改动。
- Web bridge/editor/result renderer 共 14 个 Node 测试全部通过；修改后的 XAML 文件均为有效 XML。
- 其余 WinUI 页面仍广泛暴露“功能开发中”占位文本（批量、设置、PDF、二维码、关于），这解释了用户对“每个界面差异很大”的整体感受，本轮至少应清理会干扰主要操作的占位区并统一页面标题/间距。
- 主窗口默认只有 900×600（最小 720×480），却承载批量页三栏和复杂 PDF 操作区；尺寸约束本身会导致控件拥挤。
- 应用启动后默认落到一个只有“VibeOCR”字样的空白主页，而真正主工作流“单次识别”是第二个导航项；这与 PySide6 首屏直接加载单次识别相反。
- 其余页面多数业务 ViewModel 能力尚未接通，完整像素/功能对齐不是单纯 XAML 工作；本轮安全收敛点是首屏导航、默认尺寸、统一页边距，并移除明确不可用的按钮/占位组。
- Microsoft 官方 Windows App SDK 文档确认本实现使用的 `AppWindow.MoveAndResize(RectInt32)`、`OverlappedPresenter.SetBorderAndTitleBar` 和 `IsAlwaysOnTop` API 均受支持；区域遮罩的窗口定位/无边框置顶方案与当前 SDK API 一致。
- 全量 WinUI XAML 文件均通过 XML 解析，`git diff --check` 通过；本地没有 tree-sitter C# 解析器，仍需依赖有 .NET 10 SDK 的环境完成最终编译验证。
- 工作区状态再次确认：两个 `packages.lock.json` 是任务开始前已有改动，本轮未编辑它们。
- 使用仓库 `.venv` 和 `PYTHONPATH=src` 执行 WorkerHost `--self-test` 成功，返回协议 v1、Worker 0.4.28，并确认 OCR/PDF/二维码/设置能力可加载；开发后端本体是可启动的。

## 后续高成本差异（未伪装为已完成）

- 批量页缺少文件预览、单项结果预览与实际预处理/导出设置。
- PDF 页缺少页面画布、缩略图渲染、文字层摘要以及若干文件/页面操作。
- 二维码页缺少生成预览，以及尺寸、纠错、颜色、Logo、文字说明配置。
- 设置页缺少预加载配置、缓存管理、边缘工具栏/托盘配置和依赖树操作。
- 这些差异需要补 ViewModel/协议能力和交互测试，不能只靠 XAML 排版达到 PySide6 等价。

## 工作区注意事项

- 开始时已有修改：`src/dotnet/VibeOCR.App/packages.lock.json`、`tests/dotnet/VibeOCR.App.Tests/packages.lock.json`。

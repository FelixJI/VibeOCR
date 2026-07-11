# VibeOCR WinUI 3 + WebView2 + Python Worker 迁移设计

日期：2026-07-11
状态：设计已获用户逐节确认，等待书面审阅
关联审计：`docs/packaging_startup_review_2026-07-11.md`、`docs/concurrency_async_review_2026-07-10.md`

## 1. 目标

在不重写 Python OCR/PDF/MinerU 后端的前提下，将 VibeOCR 的桌面 UI 从 PySide6/QtWebEngine 迁移为 C# WinUI 3 + WebView2，并实现以下结果：

- 继续支持 Windows 10 1809 及以上和 Windows 11；
- 使用 framework-dependent、unpackaged 部署；
- 复用系统 Evergreen WebView2 和共享 Windows App SDK Runtime；
- 保留现有嵌入式 Python、模型缓存、用户配置、快捷键和历史输出；
- 新 UI 达到完整功能对等后一次性切换正式入口；
- 切换后不提供旧 PySide6 UI 回退入口；
- 相对当前约 160 MB GitHub 分发包，体积减少至少 30%，或冷启动 p95 改善至少 30%；
- 所有已确认 P0/P1 并发、生命周期和构建问题必须在迁移前解决。

## 2. 非目标

- 不把 PaddleOCR、MinerU、PyMuPDF 或模型推理改写为 C#/Rust/C++。
- 不在本次迁移中支持 Android、iOS、macOS 或 Linux。
- 不采用 .NET MAUI；Windows-only 场景直接使用 WinUI 3。
- 不采用 self-contained 或 single-file WinUI 3 发布模式。
- 不在两套 UI 之间提供用户可见的长期切换开关。
- 不让 WinUI 3 直接依赖现有 Python worker 的内部共享内存协议。

## 3. 已确认决策

| 主题 | 决策 |
|---|---|
| 部署 | framework-dependent、unpackaged |
| 迁移方式 | 旁路迁移；PySide6 正式版持续维护到切换 |
| 切换门槛 | 完整功能对等 |
| 数据兼容 | 复用现有 Python、模型、配置、快捷键和输出 |
| 回退 | 正式切换后不允许启动旧 UI |
| Python 边界 | 独立 `vibeocr.worker_host` gateway |
| 控制协议 | Windows Named Pipe 上的版本化 JSON-RPC |
| 大数据 | Windows Shared Memory 描述符 |
| HTML 结果 | WebView2 + host message/object bridge |
| 开发数据 | WinUI 旁路版默认使用独立测试 profile |

## 4. 迁移前置门禁 Phase 0

WinUI 3 工程不得在 Phase 0 完成前进入功能迁移。允许提前建立纯文档和性能试验项目，但不得开始复制业务 UI。

### 4.1 发布和构建

- 保持 GitHub Actions 干净 checkout 为唯一正式发布源。
- 锁定 PyInstaller、PySide6、Shiboken、NumPy、Pillow 等 GUI bundle 构建版本。
- 构建产物生成 machine-readable manifest，包含路径、大小和 SHA256。
- CI 拒绝 `output/**`、缓存、日志、用户文档和非白名单大文件。
- 本地构建从 `git archive HEAD` 或只含白名单的 staging 目录读取源码。
- 记录当前约 160 MB GitHub 包的文件数、压缩/解压体积和构建依赖版本。

### 4.2 启动测量

修复现有 `scripts/profile_startup.py`，统一采集：

- T0：原生进程入口；
- T1：第一视觉反馈；
- T2：主窗口 exposed；
- T3：UI 可交互；
- T4：WorkerHost/worker 可通信；
- T5：选定模型预热完成；
- T6：首次 OCR 完成。

基线覆盖 Windows 10/11、Defender 开启、本地 SSD/USB、冷/热缓存、已有/全新运行时；每格至少 10 次，记录 p50/p95。

### 4.3 路径边界

用 `AppPaths` 取代语义过载的 project root：

```text
AppPaths
├─ app_root       可替换应用文件
├─ resource_root  当前版本只读资源
├─ data_root      配置、输出、日志
├─ runtime_root   嵌入式 Python 与 site-packages
├─ model_root     模型缓存
└─ update_root    更新下载和临时目录
```

首次升级必须从旧路径解析已有数据，并以幂等方式写入路径布局版本。迁移失败时不删除原文件。

### 4.4 Worker 和并发修复

- WorkerManager 领取 IDLE worker 与标记 BUSY 必须在同一锁内原子完成。
- 健康检查必须执行真实 stop/start，不得把仍存活但卡死的 worker 直接标回 IDLE。
- 正常关闭顺序固定为：拒绝新任务、协作取消、协议 shutdown、有界等待、超时 kill、释放 Job Object、共享内存和 reader。
- 批量 OCR 取消使用独立控制路径，不再通过已被 commit 占用的同一 worker 请求通道。
- GUI/未来 WinUI 调用取消不得同步等待最长业务超时。
- PDF OCR、mutate、export 使用 `task_id`/generation；旧任务迟到事件不得修改新任务状态。
- PDF session close 必须与 render/mutate/export 使用同一生命周期锁或 drain 屏障。
- SharedMemoryProtocol 的 interrupt 必须真正中断 read/write 重试循环。

### 4.5 Phase 0 退出条件

- 当前 PySide6 回归测试全部通过；
- 上述并发和关闭问题各有红绿回归测试；
- GitHub 产物内容、体积和构建锁门禁通过；
- T0～T6 基线完成；
- Python 领域操作可以在不 import PySide6 的测试进程中执行。

## 5. 目标架构

```text
VibeOCR.WinUI.exe
├─ WinUI 3 页面与窗口
├─ WebView2 结构化结果
├─ 托盘、截图、热键与 DPI 平台服务
├─ WorkerHostClient
├─ Settings/Runtime/Update 管理
└─ Windows App SDK bootstrapper
          │
          │ Named Pipe JSON-RPC
          │ Shared Memory payload
          ▼
python.exe -m vibeocr.worker_host
├─ RPC 路由、能力协商和任务登记
├─ OCR/Batch/MinerU 编排
├─ PDF backend 编排
├─ 依赖安装与推理后端切换
├─ 取消、进度、健康检查
└─ 现有 Python workers、模型和 runtime
```

### 5.1 仓库边界

```text
src/vibeocr/worker_host/       Python gateway 与 RPC adapter
src/dotnet/VibeOCR.App/        WinUI 3 页面、ViewModel 和应用入口
src/dotnet/VibeOCR.Contracts/  DTO、RPC envelope、错误码
src/dotnet/VibeOCR.Platform/   截图、托盘、热键、单实例、更新
tests/worker_host/              Python 契约、生命周期和集成测试
tests/dotnet/                   C# 单元、契约、集成和 UI 测试
contracts/                      语言中立 JSON schema 与 golden samples
```

`VibeOCR.Contracts` 不引用 WinUI。`VibeOCR.Platform` 不包含 OCR 领域逻辑。Python gateway 不 import PySide6。

## 6. RPC 契约

### 6.1 连接和握手

WinUI 启动 `python.exe -m vibeocr.worker_host`，传入：

- pipe 名；
- 128-bit 随机 session token；
- app/runtime/data/model 根路径；
- UI 版本和期望协议主版本。

WorkerHost 创建仅当前 Windows 用户可访问的 Named Pipe。首个请求必须是 `system.handshake`，双方交换：

- `protocol_version`；
- `app_version`、`worker_version`；
- 可用 capability；
- Python、CPU/GPU、依赖和模型状态；
- 最大消息和共享内存尺寸。

主版本不兼容时拒绝工作；次版本差异通过 capability 协商。

### 6.2 Envelope

请求：

```json
{
  "protocol_version": "1.0",
  "request_id": "uuid",
  "task_id": "uuid",
  "method": "ocr.recognize",
  "payload": {},
  "deadline_ms": 30000
}
```

响应：

```json
{
  "protocol_version": "1.0",
  "request_id": "uuid",
  "task_id": "uuid",
  "ok": true,
  "result": {},
  "error": null
}
```

事件：

```json
{
  "protocol_version": "1.0",
  "event_id": "uuid",
  "task_id": "uuid",
  "event": "task.progress",
  "payload": {"stage": "recognize", "current": 1, "total": 3}
}
```

### 6.3 大数据

控制消息不得内嵌 Base64 图片或大 PDF。共享内存描述符包含：

```json
{
  "name": "Local\\VibeOCR-{session}-{uuid}",
  "size": 1048576,
  "content_type": "image/png",
  "sha256": "hex",
  "owner": "client",
  "expires_at_ms": 30000
}
```

创建方负责释放，读取方必须发送 `memory.consumed`。断线或超时由双方 session cleanup 清除。

### 6.4 错误码

- `INVALID_REQUEST`
- `DEPENDENCY_MISSING`
- `WORKER_UNAVAILABLE`
- `TASK_CANCELLED`
- `TASK_TIMEOUT`
- `PROTOCOL_MISMATCH`
- `RESOURCE_EXHAUSTED`
- `INTERNAL_ERROR`

错误包含稳定 code、用户可显示 message、诊断 detail 和 `retryable`。UI 不解析 Python traceback 来决定行为。

## 7. 生命周期和取消

- UI 主线程不允许同步阻塞 RPC。
- 每个长任务必须有 `task_id`、deadline 和 cancellation token。
- `task.cancel` 使用控制通道并在 2 秒内返回 ACK。
- 取消 ACK 表示已接受取消，不等同任务已经终止；最终以 `task.cancelled` 或终态响应为准。
- 查询型 OCR 在 worker 崩溃后最多自动重试一次。
- PDF 修改、保存、更新、依赖安装和后端切换不得自动重试。
- WorkerHost 异常退出时，WinUI 最多自动重启一次；连续失败进入运行环境修复页。
- 应用退出先 drain WorkerHost，再终止遗留子进程；不使用无界等待。

## 8. UI 功能映射

### 8.1 WinUI 3 原生实现

- 主窗口、导航、状态栏和设置页；
- 单实例与前台激活；
- 系统托盘和快捷菜单；
- 全局快捷键；
- 多屏截图、混合 DPI 坐标和放大镜；
- 文件选择、剪贴板、通知和错误对话框；
- 更新、依赖修复和后端选择 UI。

### 8.2 WebView2 实现

- 结构化 OCR HTML；
- KaTeX 公式；
- 块选择和双向高亮；
- HTML/Markdown/纯文本复制状态；
- 结果页内交互。

QWebChannel 替换为 `CoreWebView2.PostWebMessageAsJson`/`WebMessageReceived`。消息必须有 schema，网页内容不得直接调用任意 host 方法。

### 8.3 Python WorkerHost 实现

- 单次和批量 OCR；
- MinerU；
- PDF 打开、渲染、OCR、修改、导出和保存；
- 二维码识别中依赖 Python 库的部分；
- Python/runtime/模型依赖检查与安装；
- CPU/GPU backend 切换和 worker 预热。

## 9. 配置和数据迁移

- 旁路开发默认 profile：`data/profiles/winui-dev`，不得写正式配置。
- 正式切换前运行一次性 migrator，读取旧 JSON 并写入带 `schema_version` 的共享配置。
- migrator 必须幂等；已迁移版本重复执行不得改变结果。
- 快捷键、预设、布局中可跨框架的语义字段保留；Qt geometry byte blob 不直接迁移，转换为窗口位置、大小、最大化状态和屏幕标识。
- 现有 Python runtime、site-packages、模型和输出只登记新路径，不重复下载或复制。
- 迁移前创建配置备份用于修复，不提供旧 UI 回退入口。

## 10. 更新与正式切换

切换版本的更新流程：

1. 下载并校验 WinUI app payload、WorkerHost wheel、manifest 和 runtime prerequisites。
2. 检查/安装 Windows App SDK Runtime 与 Evergreen WebView2。
3. 安装 WinUI app 到新版本目录，不覆盖旧运行中 UI。
4. 停止旧 UI 和所有 workers。
5. 执行配置 migrator。
6. 启动 WinUI，并等待 `startup.healthy` 标记。
7. 健康标记成功后删除 Qt/PyInstaller app 文件。
8. 失败时保留数据和诊断包，进入修复流程；不得启动旧 UI。

后续更新只分发 WinUI app、WorkerHost wheel 和变更组件；Python runtime、模型与依赖按 manifest 复用。

## 11. 安全与防御

- Named Pipe ACL 只允许当前用户 SID。
- session token 只通过子进程参数/继承句柄传递，不写日志。
- 消息使用 32-bit 长度前缀，并限制最大控制消息尺寸。
- 方法白名单路由，不接受任意 Python module/function 名。
- 所有路径输入做 canonicalization，并限制在允许的数据、runtime、model 或临时根下。
- WebView2 只加载本地受信任资源；外部导航交给系统浏览器。
- WebView2 host bridge 只暴露枚举方法，不执行任意脚本传入的命令。
- 更新 manifest、app payload 和 WorkerHost wheel 都做签名/哈希验证。

## 12. 测试策略

### 12.1 契约测试

- `contracts/schemas/*.json` 定义 envelope 和领域 DTO。
- `contracts/golden/*.json` 同时由 Python 和 C# 反序列化/序列化。
- schema 变更必须包含向前/向后兼容测试或提升协议主版本。

### 12.2 Python 测试

- RPC 路由、认证、deadline、取消和错误映射；
- WorkerHost 异常退出和 child cleanup；
- OCR/PDF/MinerU adapter；
- Shared Memory 所有权、校验、超时和泄漏；
- Phase 0 并发回归。

### 12.3 C# 测试

- Named Pipe framing、并发 request map 和 event dispatch；
- 超时、取消、断线、重连和协议不匹配；
- ViewModel 状态机和错误映射；
- 配置 migrator；
- Windows App SDK/WebView2 prerequisite 检测。

### 12.4 端到端测试

- 单次、批量、二维码和 MinerU；
- PDF 打开、编辑、OCR、导出和保存；
- 截图、多屏混合 DPI、托盘和全局快捷键；
- WebView2 公式、块高亮和复制；
- CPU/GPU 环境安装、切换、预热和取消；
- 更新、首次迁移、健康标记和修复流程。

## 13. 里程碑

### Phase 0：现有问题修复

交付：稳定 PySide6 基线、构建门禁、T0～T6 指标、并发回归和 UI-free Python 领域边界。

### Phase 1：WorkerHost 契约

交付：Named Pipe server、Shared Memory adapter、领域方法、golden contract tests 和 Python host 集成测试。

### Phase 2：WinUI 3 壳

交付：framework-dependent unpackaged app、bootstrapper、单实例、导航、设置基础、WorkerHostClient 和诊断页。

### Phase 3：核心 OCR

交付：截图、文件输入、单次 OCR、WebView2 结果、复制和导出。

### Phase 4：完整功能

交付：批量、二维码、PDF、依赖管理、后端切换、托盘、快捷键和更新。

### Phase 5：对等与切换

交付：100% 功能矩阵、性能/体积报告、配置 migrator、切换更新包和正式健康门禁。

## 14. 最终验收

- 功能对等矩阵 100%；
- 没有未解决 P0/P1；
- Python/C# golden contract 全通过；
- 所有取消、超时、断线和关闭测试通过；
- 当前 Python、模型、配置、快捷键和输出可直接复用；
- Windows 10/11 framework-dependent 安装和修复流程通过；
- 相对当前 GitHub 基线，包体积减少至少 30%，或冷启动 p95 改善至少 30%；
- WinUI 健康启动后旧 Qt/PyInstaller app 文件被删除；
- 产品中不存在旧 UI 回退入口。

## 15. 主要风险与控制

| 风险 | 控制 |
|---|---|
| 双语言协议漂移 | JSON schema、golden samples、protocol version、CI 双端测试 |
| WinUI 桌面能力不齐 | Phase 2 先验证托盘、热键、截图、多屏 DPI 和单实例 |
| framework runtime 缺失 | bootstrapper 检测、安装和修复页 |
| WebView2 行为差异 | 先迁移现有 HTML，建立公式/高亮/复制 golden tests |
| 大数据 IPC 复制 | Named Pipe 控制 + Shared Memory payload |
| 新旧配置互相污染 | 旁路 profile、一次性幂等 migrator、切换前备份 |
| 迁移时间失控 | 每个 Phase 有独立可测试交付，Phase 2/3 后重新评估收益 |
| 无回退导致切换失败 | 切换前完整健康测试；失败进入修复流程并保留数据，不启动旧 UI |

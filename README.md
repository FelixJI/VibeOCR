<p align="center">
  <img src="resources/icon_256.png" width="140" alt="VibeOCR" />
</p>

<h1 align="center">VibeOCR</h1>

<p align="center">
  面向 Windows 的一站式 OCR 与文档处理桌面应用 —— 截图识别、批量识别、PDF 文字层、文档解析、二维码，一站式搞定。
</p>

<p align="center">
  <a href="https://github.com/FelixJI/VibeOCR/releases"><img alt="版本" src="https://img.shields.io/badge/version-0.4.28-blue" /></a>
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-green" /></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.13-blue" />
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows%2064--bit-lightgrey" />
</p>

---

## 简介

VibeOCR 是一款基于 [PySide6](https://www.qt.io/) + [PaddlePaddle](https://www.paddlepaddle.org.cn/) 的本地化 OCR 桌面应用。
所有识别都在本地完成，**无需联网、不上传任何文件**，适合处理隐私敏感的文档与截图。

支持文字识别、表格识别、公式识别、文档解析（MinerU / PaddleOCR-VL）等多条推理流水线，
并对 GPU（CUDA）与 CPU 双后端做了适配。

## 🚦 开发状态与两条路线

VibeOCR 目前存在**两条并行的桌面产品路线**，共用同一份 Python 后端（OCR / PDF / 二维码 / 设置），
但前端实现、成熟度和可用性差异巨大。**请先了解现状再选择使用哪一条。**

### 路线一：PySide6 Classic —— ✅ 当前主力，推荐使用

- 基于 [PySide6](https://www.qt.io/) 的成熟 Qt 桌面 UI，是当前发布与日常使用的**默认产品**。
- 功能完整：截图识别、单文件 / 批量识别、PDF 处理与文字层写入、二维码生成与解码、多引擎流水线、设置、托盘、程序内更新等均已落地并通过回归。
- Releases 页的 `VibeOCR-Classic-vX.Y.Z-win64.zip` 即此路线。
- **绝大多数用户应当下载并使用 Classic 版本。**

### 路线二：WinUI Next —— ⚠️ 早期开发阶段，目前基本不可用

- 基于 [WinUI 3](https://learn.microsoft.com/windows/apps/winui/winui3/) 的下一代 UI，目标是长期取代 Classic。
- **当前状态：早期开发中，不可用于日常使用。** 大多数功能页面仍是占位状态或尚未接通后端：
  - 单次识别页：开发启动时存在**后端不自动拉起**的问题（已修，但尚未在任何已发布版本中体现），区域截图框选刚具备雏形。
  - 批量识别 / PDF / 二维码 / 设置等页面：ViewModel 与协议方法已搭建并有单元测试，但实际 UI 交互、文件预览、页面操作、缩略图渲染、导出等**尚未完成对等**。
  - 真实 WinUI 运行级验证仍需 .NET 10.0.302 SDK 环境；多数改动目前只通过静态 / 单元测试覆盖。
- Releases 页的 `VibeOCR-Next-vX.Y.Z-win64.zip` **默认不随 tag 发版**——发版流水线默认只产 Classic；需要时由维护者手动 `workflow_dispatch` 选 `winui`/`all` 才会构建，**仅为开发预览，不建议普通用户使用**。
- 适用人群：WinUI / .NET 开发者、愿意参与早期建设或做技术评估的贡献者。

> 两套前端通过 Windows 命名 Mutex **互斥运行**，不能同时启动；架构细节见下方「双前端独占架构」一节。

## 功能特性

### 📷 识别能力

- **截图识别** —— 全局快捷键唤起，框选屏幕区域即可识别；内置可标注的内联编辑器（马赛克 / 模糊 / 矩形 / 椭圆 / 填充色等）
- **单文件识别** —— 支持打开本地图片（PNG/JPG 等）与 PDF 文件
- **批量识别** —— 拖入多文件批量处理，进度可见
- **多引擎流水线** —— 文字识别（PP-OCRv5 / PP-V3）、表格识别、公式识别、文档解析（MinerU）、多模态文档解析（PaddleOCR-VL）

### 📄 PDF 文档处理

- 页面级操作：旋转 / 删除 / 插入 / 拖拽重排
- **文字层写入**：对无文字层的扫描页执行 OCR 并内嵌可搜索、可复制的中文文字层（子集化 CJK 字体内嵌，跨阅读器兼容）
- 文字层删除、逐页状态可视化、独立预览窗口（支持缩放与拖拽平移）
- 按 GPU 显存 / 内存自适应的动态批处理大小

### 🔲 二维码

- 生成：QR 码（含 logo 嵌入、文字标签、颜色反转、SVG 导出）与多种条形码（Code128 / Code39 / EAN-13 等）
- 解码：粘贴 / 拖入 / 选择图片识别 QR 码与条形码

### 🛠 工程化

- **推理后端自适应** —— 首次启动自动检测 GPU/CPU，可在设置页切换后端
- **独占 WorkerHost 隔离** —— 每个前端只拥有一个通过随机 Named Pipe + token 连接的 WorkerHost；OCR、PDF、二维码和业务设置均在该进程内执行，二进制大载荷使用共享内存
- **流水线缓存** —— 按显存分层的重型流水线缓存（FIFO 淘汰 + TTL 回收），减少重复加载
- **程序内更新** —— 自动检查新版本，国内客户端走 gh 代理加速下载；含 `--self-update` 兜底替换通道
- **系统托盘** —— 最小化到托盘，边缘悬浮工具栏快捷唤起

### 🏗️ 双前端独占架构

VibeOCR 采用**双前端并存**架构，两套 UI 各自独占一个 WorkerHost 后端进程，互斥运行：

- **PySide6 Classic** —— 成熟的 Qt 桌面 UI（当前主力）
- **WinUI Next** —— 基于 WinUI 3 的下一代 UI

核心设计（详见 `specs/2026-07-14-dual-frontend-exclusive-workerhost-adr.md`）：

| 约束 | 实现 |
|------|------|
| 两套产品互斥运行 | Windows 命名 Mutex `Local\VibeOCR.Frontend.Exclusive.v1`（不扫描进程名） |
| 单份后端实现 | 两套前端共享同一 WorkerHost 代码库，各自启动独占实例 |
| UI 不直接调后端 | 前端只依赖 `BackendClient` + 协议 DTO；架构守卫强制（`tests/architecture/`） |
| 协议一致性 | schema / C# / Python 三方方法表一致性测试 |

**自动化架构守卫**（`tests/architecture/`）：
- UI→backend import 零例外门禁（迁移 allowlist 已删除）
- WorkerHost UI-free import gate
- 后端→UI 禁止反向依赖
- 协议方法表三方一致性

**实施状态**：二维码、单图/批量 OCR、五种导出、PDF open/render/mutate/OCR/save、业务设置、预热/缓存和有界 shutdown 均通过 typed client/RPC；UI→backend import 从基线 90 清零。contracts、Python client、backend、PySide app 已形成独立 workspace 边界，发布流水线单次构建 backend wheel，并把同一 SHA-256 精确绑定到 Classic/Next 两个制品。

## 下载安装

### 方式一：下载便携版（推荐，无需配置环境）

前往 [Releases](https://github.com/FelixJI/VibeOCR/releases) 下载最新版 **`VibeOCR-Classic-vX.Y.Z-win64.zip`**（即 PySide6 Classic 路线，功能完整、当前主力），解压后运行 `VibeOCR.exe` 即可。

> ⚠️ 部分历史或手动构建的 Release 里可能附带 `VibeOCR-Next-vX.Y.Z-win64.zip`（WinUI Next 路线），**目前处于早期开发阶段、基本不可用**，仅供开发预览，普通用户请勿下载。默认 tag 发版只产 Classic；详见上方「开发状态与两条路线」一节。

> 国内用户访问 GitHub 较慢时，可在程序内检查更新（自动走 gh 代理加速），或使用 gh 代理前缀手动下载。

首次启动时，应用会自动检测 GPU/CPU 并引导安装推理依赖；WebEngine 渲染组件已内置主包。

### 方式二：从源码运行（开发者）

详见下方 [开发指南](#开发指南)。

## 技术栈

| 领域 | 技术 |
|------|------|
| GUI 框架 | PySide6（Qt for Python） |
| 文字 / 表格 / 公式 OCR | PaddleOCR、PaddlePaddle-GPU（CUDA 12.6） |
| 文档解析 | MinerU、PaddleOCR-VL |
| 深度学习运行时 | PyTorch（cu126，同时为 Paddle 提供 CUDA DLL） |
| PDF 处理 | PyMuPDF、fontTools（CJK 子集化） |
| PDF 后端进程 | FastAPI + uvicorn（子进程内 HTTP 服务） |
| 进程间通信 | pydantic（共享 schema）、httpx（PDF 后端）、Named Pipe + 共享内存（WorkerHost RPC） |
| 条码 | qrcode、python-barcode、pyzbar、OpenCV |
| 导出 | python-docx（Word）、openpyxl（Excel） |
| 异步集成 | qasync（Qt 事件循环 + asyncio） |
| 依赖管理 | [uv](https://github.com/astral-sh/uv) |
| 代码质量 | ruff + pyright + pytest |

## 开发指南

### 环境要求

- Windows 10/11（64 位）
- Python **3.13**（仅支持 3.13，见 `pyproject.toml`）
- [uv](https://docs.astral.sh/uv/)（推荐）或 pip
- .NET SDK **10.0.302**（构建 WinUI 前端，见 `global.json`）

> ⚠️ **dotnet 路径陷阱**：若系统同时装了 x86 与 x64 .NET，PATH 里的 `dotnet` 可能解析到
> `C:\Program Files (x86)\dotnet\dotnet.exe`（仅有运行时、**无 SDK**），导致
> `A compatible .NET SDK was not found` / `Requested SDK version: 10.0.302`。
> 验证：`dotnet --list-sdks` 应能看到 `10.0.302`；若为空，说明解析到了 x86 副本。
> 修复：把 `C:\Program Files\dotnet\` 移到 PATH 中 x86 目录**之前**，或在 shell 里：
>
> ```powershell
> # PowerShell：优先使用 x64 dotnet（仅当前会话）
> $env:PATH = "$env:ProgramFiles\dotnet;$env:PATH"
> dotnet --list-sdks   # 应列出 10.0.302
> ```
>
> ```bash
> # Git Bash：优先使用 x64 dotnet（仅当前会话）
> export PATH="/c/Program Files/dotnet:$PATH"
> ```
>
> 仓库内 `scripts/*.ps1` 已硬编码 `$env:ProgramFiles\dotnet\dotnet.exe`，不受此影响。

### 安装依赖

```bash
# 克隆仓库
git clone https://github.com/FelixJI/VibeOCR.git
cd VibeOCR

# 使用 uv（推荐，国内镜像已在 pyproject.toml 配置）
uv sync
```

> 说明：PaddlePaddle-GPU 与 PyTorch 均为 CUDA 12.6 构建（cu126）以保持 CUDA DLL 同源。
> `pyproject.toml` 已配置国内镜像（阿里云 / 南大 / PaddlePaddle 官方），无需额外设置。

### 运行应用

```bash
uv run python src/vibeocr/main.py
```

### 代码质量检查

项目在 `qa/` 目录提供了一套统一的代码质量脚本（详见 [`qa/README.md`](qa/README.md)）：

```bash
# 交互式选择检查项
uv run python qa/run.py

# 一键运行全部检查（格式化 / lint / 类型检查）
uv run python qa/run.py --all --quick

# 运行测试并生成覆盖率报告
uv run python qa/coverage.py --html
```

提交前建议执行 `pre-commit install` 配置 Git 钩子。

### 运行测试

测试按模块组织在 `tests/` 下（`core` / `managers` / `models` / `services` / `views` / `widgets` / `workers` / `integration`），共 134 个：

```bash
# 跑全部测试
uv run pytest

# 只跑某一层（推荐：改动后定向验证）
uv run pytest tests/services
uv run pytest tests/views/tabs

# 单个测试文件 / 函数
uv run pytest tests/services/test_pdf_service.py
uv run pytest tests/services/test_pdf_service.py::test_add_text_layer
```

### 构建 / 测试 WinUI（.NET）前端

WinUI 前端位于 `src/dotnet/`，解决方案文件为 `src/dotnet/VibeOCR.slnx`（7 个项目：
`Contracts` / `Platform` / `App`(WinUI) / `Bootstrapper` + 3 个测试项目）。确保先用上面的
dotnet 路径陷阱说明修正 `dotnet` 解析，然后：

```bash
# 还原 + 构建整个解决方案（Debug）
dotnet build src/dotnet/VibeOCR.slnx

# 跑 .NET 测试（xUnit）
dotnet test src/dotnet/VibeOCR.slnx --no-build

# 打包 Release 便携版（硬编码 x64 dotnet，无需手动改 PATH）
pwsh scripts/build_winui_release.ps1 -Version 0.4.28
```

> 也可单独构建：`dotnet build src/dotnet/VibeOCR.App/VibeOCR.App.csproj`

### NuGet 依赖锁文件

仓库将各 .NET 应用 / 测试入口的 `packages.lock.json` 纳入 Git，用于锁定完整的传递依赖图。
`global.json` 固定 .NET SDK 版本，`Directory.Packages.props` 集中声明直接依赖版本；普通 Restore
默认启用 locked mode，只能读取已提交的锁文件。项目声明与锁文件不一致时，Restore 会失败，
不会因 Visual Studio、开发机架构或隐式 Restore 而静默改写锁文件。

```powershell
# 日常还原：严格使用已提交的锁文件
dotnet restore src/dotnet/VibeOCR.slnx

# 仅在升级包版本、修改 PackageReference / TFM / RID 时更新锁文件
powershell -ExecutionPolicy Bypass -File scripts/update_dotnet_locks.ps1
# PowerShell 7 也可使用：pwsh -File scripts/update_dotnet_locks.ps1

# 通过 QA 依赖升级入口执行同一脚本（同时升级 Python 依赖）
python qa/upgrade_deps.py --dotnet-locks
```

更新依赖时，应将 `Directory.Packages.props` / `.csproj` 与生成的 `packages.lock.json` 放在同一提交中，
并审查锁文件 diff。当前 WinUI App 及其测试只支持 `win-x64`；若出现 `win-x86` 或 `win-arm64`
依赖图，不应直接提交，应先检查 Restore 命令或 IDE 平台设置。若将来正式支持新架构，应先更新
项目中的 `RuntimeIdentifiers`，再通过上述脚本统一重建并提交锁文件。CI 同样使用 locked mode，
锁文件过期会直接阻止构建。

## 项目结构

```
vibeocr/
├── src/vibeocr/
│   ├── main.py                       # 应用入口（环境变量预处理 + 单实例 + 启动 + --self-update 兜底）
│   ├── env_manager.py                # 环境与依赖管理（python-build-standalone 部署、CUDA DLL 路径）
│   ├── pipeline_status.py            # 流水线首次成功状态记录（防重复引导）
│   ├── machine_cache.py              # 机器特征缓存（避免重复探测 GPU/模型源）
│   ├── network_detector.py           # 国内/海外网络环境探测
│   ├── python_path_manager.py        # 打包态内嵌 Python 路径解析
│   ├── core/                         # 领域内核（与 Qt/网络无关）
│   │   ├── constants.py              # 全局常量（共享内存大小、超时等）
│   │   ├── base_worker.py            # Worker 基类
│   │   ├── singleton_meta.py         # 线程安全单例元类
│   │   └── pipelines/               # 推理流水线注册表 + 各管道 spec/选项
│   │       ├── __init__.py           # OCRPipeline 枚举 + 元数据（单一事实源）
│   │       ├── registry.py           # PipelineRegistry / PipelineSpec
│   │       ├── pipeline_ocr.py       # 通用 OCR
│   │       ├── pipeline_pp_structure.py  # PP-StructureV3
│   │       ├── pipeline_table.py     # 表格识别
│   │       ├── pipeline_formula.py   # 公式识别
│   │       ├── pipeline_mineru.py    # MinerU 文档解析
│   │       └── pipeline_paddlocr_vl.py   # PaddleOCR-VL
│   ├── models/                       # 数据模型（dataclass，跨进程共享）
│   │   ├── ocr_result.py             # OCRResult / TextBlock
│   │   ├── pdf_document.py           # PdfDocument / PdfPageInfo / TextLayerInfo（规范模型）
│   │   ├── pdf_session.py            # PDF 会话状态
│   │   ├── batch_request.py          # 批量请求 + 预处理选项
│   │   ├── ocr_options.py            # OCR 选项
│   │   ├── pdf_ocr_options.py        # PDF OCR 选项
│   │   ├── export_settings.py        # 导出设置
│   │   └── text_block_options.py     # 文本块选项
│   ├── ipc/                          # PDF 后端进程间通信契约
│   │   ├── schemas.py                # pydantic 共享 schema（请求/响应/Mirror/Diff）
│   │   └── model_bridge.py           # PdfDocumentMirror ↔ PdfDocument 桥接（主进程侧）
│   ├── services/                     # 业务服务层
│   │   ├── ocr_service_subprocess.py # OCR 子进程服务（主进程侧单例，对外接口）
│   │   ├── ocr_worker_process.py     # OCR worker 子进程入口（持有 PaddleX）
│   │   ├── worker_manager.py         # PaddleX WorkerManager 封装（共享内存 IPC）
│   │   ├── ocr_service.py / _base.py / _portable.py  # 多形态 OCRService
│   │   ├── pdf_backend_process.py    # PDF 后端 FastAPI 子进程（持有 fitz）
│   │   ├── pdf_backend_client.py     # 主进程 httpx 客户端 + 进程托管
│   │   ├── pdf_service.py            # PDF 操作与文字层写入（在后端进程内调用）
│   │   ├── mineru_service.py / _batch.py  # MinerU 文档解析
│   │   ├── pipeline_cache_manager.py # 流水线缓存（FIFO + TTL）
│   │   ├── export_service.py         # 导出 Word/Excel/Markdown
│   │   ├── qrcode_service.py         # 二维码/条码生成
│   │   ├── qrcode_decode_service.py  # 二维码/条码解码
│   │   ├── text_block_processor.py   # 文本块后处理
│   │   ├── update_service.py         # 程序内更新
│   │   ├── env_config.py             # 镜像源、python-build-standalone 配置
│   │   └── log_service.py            # 日志初始化
│   ├── workers/                      # Qt 后台线程（主进程内）
│   │   ├── ocr_worker.py             # OCR QRunnable
│   │   ├── batch_queue_manager.py    # 批量任务队列
│   │   ├── pdf_ipc_worker.py         # PDF IPC 请求线程
│   │   └── pdf_render_thumb_ipc_worker.py  # PDF 缩略图渲染线程
│   ├── managers/                     # 应用级管理器（单例）
│   │   ├── config_manager.py         # 统一配置
│   │   ├── settings_manager.py       # 设置持久化
│   │   ├── dependency_manager.py     # 依赖安装/更新
│   │   ├── subprocess_manager.py     # OCR 子进程生命周期
│   │   ├── pdf_session_manager.py    # PDF 会话编排
│   │   └── layout_manager.py         # 窗口布局持久化
│   ├── views/                        # 视图层（Controller + 复合页）
│   │   ├── main_window.py            # 主窗口
│   │   ├── clipboard_controller.py   # 剪贴板协调
│   │   ├── settings_page_controller.py
│   │   ├── batch_recognition_tab.py  # 批量识别页
│   │   ├── pdf_preview_window.py     # PDF 独立预览窗
│   │   └── tabs/                     # 各功能标签页（单文件 / PDF / 二维码 / 关于）
│   ├── widgets/                      # 可复用 Qt 组件
│   │   ├── screen_capture_overlay.py # 全屏截图遮罩
│   │   ├── inline_edit_canvas.py     # 截图内联编辑器
│   │   ├── editor/                   # 编辑器子模块（画布/工具栏/标注项/命令栈）
│   │   ├── recognition_panel.py / result_view_widget.py
│   │   ├── install_dialog.py / backend_choice_dialog.py
│   │   ├── toast_widget.py / toolbar.py / magnifier_overlay.py
│   │   └── *_options_widget.py       # 各类选项面板
│   ├── ui/                           # Qt 自动生成代码
│   │   ├── ui_main_window.py         # 由 .ui 编译（见 scripts/compile_ui.py）
│   │   └── theme.py                  # 主题 token（当前禁用，保留）
│   └── utils/                        # 工具集
│       ├── cpu_info.py / system_memory.py / gpu_memory_monitor.py
│       ├── cjk_font_resolver.py      # 系统中文字体定位 + 子集化
│       ├── shared_memory_v2.py       # 跨进程张量共享内存
│       ├── job_object.py             # Windows Job Object 孤儿子进程清理
│       ├── subprocess_log.py         # 子进程 stdout → 主进程日志转发
│       ├── single_instance.py        # 单实例守卫
│       ├── qt_async.py               # qasync 事件循环工厂
│       ├── markdown_converter.py     # Markdown → HTML（KaTeX 渲染）
│       ├── thumbnail_lru_cache.py    # PDF 缩略图 LRU 缓存
│       ├── warmup_utils.py / ocr_preferences.py / app_settings.py
│       └── indent_processor.py / pdf_coords.py / mime_types.py / autostart.py
├── resources/                  # 应用图标、KaTeX 资源（打包由 --add-data 内嵌）
├── scripts/                    # bump_version（打包发版）、compile_ui、update_replacer、updater_main、profile_startup
├── qa/                         # 代码质量脚本（lint/format/type_check/coverage/upgrade_deps）
├── tests/                      # 单元 + 集成测试（134 个，按模块组织）
├── .github/workflows/release.yml  # GitHub Actions 发版（PyInstaller + 镜像 CNB）
└── pyproject.toml
```

## 源码阅读辅助

本章帮助新加入的开发者快速建立对整体架构的心智模型。代码库规模约 **132 个源文件 / 4.5 万行**，按职责分层清晰，但有几处「隔离边界」是阅读时的关键。

### 架构总览

VibeOCR 是一个 **多进程 + Qt 主线程** 的桌面应用。主进程只跑 GUI 和轻量协调，所有重依赖（PaddlePaddle、PyMuPDF）都下沉到独立子进程，通过 IPC 通信。

> **双前端边界**：PySide Classic 与 WinUI Next 都只通过各自的客户端会话连接独占 WorkerHost；UI 层不再直接 import 后端 service/manager/worker。详见上方「双前端独占架构」。

```
PySide Classic / WinUI Next（互斥）
  → BackendClient + contracts DTO
  → 随机 Named Pipe + session token + shared payload
  → 独占 WorkerHost
      ├─ OCR / batch / export
      ├─ PDF session / render / mutate / OCR / save
      ├─ QR encode / decode
      └─ settings / dependency / warmup / cache
```

### 分层职责

| 层 | 目录 | 职责 | 依赖方向 |
|----|------|------|----------|
| **入口** | `main.py`, `env_manager.py` | 环境变量预处理、单实例、依赖检测、启动 | → 所有层 |
| **纯契约** | `contracts/`, `ipc/` | 协议 DTO、流水线元数据、模型桥接 | 无 Qt |
| **Python 客户端** | `client/`, `worker_host/backend_client.py` | 会话、typed RPC、取消、共享载荷 | → contracts |
| **后端** | `worker_host/`, `application/`, `services/` | OCR/PDF/二维码/设置业务实现 | → contracts/models |
| **PySide 壳** | `pyside/`, `views/`, `widgets/`, `ui/` | Qt 平台能力、展示与输入采集 | → client/contracts |
| **工具** | `utils/` | CPU/GPU 信息、字体、共享内存等纯工具 | 无外部依赖 |

> **规则**：前端只能依赖 client/contracts，不能 import 后端实现；Python/C# 方法表、JSON Schema 与 golden fixture 必须同步通过契约门禁。

### 关键数据流

#### 1. 截图 OCR（最常用路径）

```
全局快捷键 → widgets/screen_capture_overlay.py（全屏遮罩 + 框选）
          → widgets/inline_edit_canvas.py（可选标注：马赛克/模糊/矩形）
          → client/session.py（进程级 BackendSession）
          │   ↓ Named Pipe RPC + 共享内存
          → worker_host（PaddleX 推理、进度、取消、导出）
          → widgets/recognition_panel.py（渲染结果，支持复制/导出）
```

#### 2. PDF 处理（进程化架构）

```
views/tabs/pdf_tab.py（UI 交互）
  → pyside/pdf_session_manager.py（Qt 会话/ViewModel）
  → pyside/pdf_ipc_worker.py（Qt 线程）
  → client/pdf.py（共享 WorkerHost 兼容客户端）
  │   ↓ Named Pipe RPC
  → WorkerHost 内 PDF adapter（不再启动 localhost 子进程）
  │     → services/pdf_service.py（fitz 操作 + OCR 文字层写入）
  │     ← 返回 PdfDocumentMirror / ModelDiff / PNG 字节
  → ipc/model_bridge.py（Mirror → PdfDocument 只读视图）
  → widgets（缩略图/预览刷新）
```

> PDF 后端返回的是 `PdfDocumentMirror`（纯数据，可序列化），主进程通过 `ipc/model_bridge.py` 重建为 UI 现有代码熟悉的 `PdfDocument` 只读视图。所有修改操作（旋转/删除/插入/OCR 写层）走 IPC，后端返回增量 `ModelDiff`，主进程 `apply_diff` 刷新视图。

#### 3. 批量识别

```
views/batch_recognition_tab.py（拖入多文件）
  → client/batch.py（批量 adapter）
  → 共享 BackendSession（并发、取消、进度）
  → WorkerHost ocr.export（Word/Excel/Markdown/HTML/Text）
```

### 推荐阅读顺序

按以下顺序读，能在最短时间内建立完整心智模型：

1. **`main.py`** —— 启动流程：环境变量预处理 → 单实例守卫 → `ConfigManager` → `MainWindow` → qasync 循环。注意 `--self-update` 兜底通道（抢在 Qt import 之前拦截）。
2. **`contracts/` + `contracts/v1/`** —— 纯 Python DTO、JSON Schema、golden fixture 与公开方法表。
3. **`client/session.py` + `worker_host/backend_client.py`** —— PySide 的进程级独占会话与 typed RPC。
4. **`worker_host/main.py` + `composition.py`** —— Named Pipe 生命周期、公开方法注册与 UI-free 生产组合。
5. **`pyside/pdf_session_manager.py` + `client/pdf.py`** —— PDF Qt ViewModel 与 WorkerHost 边界。
6. **`views/main_window.py` → `views/tabs/`** —— 前端如何只经 client/contracts 提交任务并展示结果。

### 关键设计决策（读码前必知）

- **为什么只有一个 WorkerHost？** OCR/PDF 仍与 UI 崩溃域隔离，但统一的父进程 watchdog、随机 pipe/token 和有界 shutdown 能保证一一对应，不再叠加 localhost 子进程。
- **为什么 PDF 在 WorkerHost 内执行？** PyMuPDF session 只能由后端持有；前端只接收 PNG bytes、`PdfDocumentMirror` 与 `ModelDiff`，不会跨进程携带 fitz/Qt 对象。
- **为什么有 `Mirror` 和 `Diff`？** 主进程不能持有 fitz 上下文，但 UI 代码大量直接读 `PdfPageInfo` 字段。`ipc/model_bridge.py` 把后端返回的纯数据 `Mirror` 重建为 UI 熟悉的 `PdfDocument` 只读视图；修改操作返回增量 `ModelDiff` 而非整文档，减少传输。
- **流水线缓存（`pipeline_cache_manager.py`）**：重型流水线（PP-StructureV3 / MinerU / PaddleOCR-VL）按显存分层加载，FIFO 淘汰 + TTL 闲置回收。改流水线相关代码前先读它，理解生命周期边界。
- **CUDA DLL 同源（`env_manager.py` + `pyproject.toml` 注释）**：Paddle cu126 找 `cublas64_12.dll`，由 torch cu126 的 `torch/lib` 提供。所以项目显式依赖 torch 不仅为推理，也为给 paddle 提供 CUDA 运行时 DLL。`OCRService._setup_cuda_dll_path` 负责注册路径。
- **孤儿进程清理（`utils/job_object.py`）**：子进程由 Windows Job Object 托管，主进程崩溃时子进程自动终止，不残留。

### 常见读码问题

| 问题 | 入口文件 |
|------|----------|
| 新加一种识别流水线？ | `core/pipelines/`（新建 `pipeline_xxx.py` + 注册到 `__init__.py`） |
| 改 OCR 选项 UI？ | `widgets/backend_options_widget.py` + `models/ocr_options.py` |
| 改 PDF 页面操作？ | `views/tabs/pdf_tab.py`（UI）→ `pyside/pdf_session_manager.py` |
| 新增 PDF 后端 API？ | `contracts/v1/methods.schema.json` → WorkerHost handler/composition → Python/C# typed client |
| 改导出格式？ | `services/export_service.py` |
| 改快捷键/托盘？ | `views/main_window.py` + `main.py` |
| 调整流水线缓存策略？ | `services/pipeline_cache_manager.py` |

### 开发调试技巧

- **日志**：`services/log_service.py` 统一初始化；子进程 stdout 由 `utils/subprocess_log.py` 转发到主进程日志。三套子进程（OCR worker / PDF 后端 / updater）的日志通道已统一。
- **UI 文件**：`ui/ui_main_window.py` 由 `scripts/compile_ui.py` 从 `.ui` 编译生成，**不要手改**，改 `.ui` 后重新编译。
- **代码质量**：提交前跑 `uv run python qa/run.py --all --quick`（lint + 格式 + 类型检查）。
- **测试**：`tests/` 按模块组织（core/managers/models/services/views/widgets/workers/integration），134 个测试。`uv run pytest tests/<子目录>` 跑某一层。
- **网络环境**：`network_detector.py` 探测国内/海外，影响镜像源选择；调试时可关注 `services/env_config.py`。

## 发布流程

打包与发版由 [`scripts/bump_version.py`](scripts/bump_version.py) 编排（基于 PyInstaller），
推送到 `v*` 格式的 tag 后，GitHub Actions（[`.github/workflows/release.yml`](.github/workflows/release.yml)）会：

1. 构建并验证一次 UI-free backend wheel
2. 用同一 wheel SHA-256 组合 `VibeOCR-Classic`（PySide，**正式发布**）与 `VibeOCR-Next`（WinUI，**开发预览、基本不可用**）两个 ZIP
3. 分别运行制品 verifier 并生成 SHA-256 文件
4. 上传两个产品到 GitHub Release，并镜像代码到 CNB

```bash
# 本地打包当前版本
uv run python scripts/bump_version.py --build

# 升级版本并打包（交互式）
uv run python scripts/bump_version.py minor
```

## 下载渠道

| 渠道 | 地址 |
|------|------|
| GitHub（主） | <https://github.com/FelixJI/VibeOCR> |
| Gitee（代码仓库） | <https://gitee.com/felixjii/vibeocr> |
| CNB（代码镜像） | <https://cnb.cool/feljii/VibeOCR> |

## 许可证

[MIT License](LICENSE) © 2025–2026 Felix Ji

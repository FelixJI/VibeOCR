<p align="center">
  <img src="resources/icon_256.png" width="140" alt="VibeOCR" />
</p>

<h1 align="center">VibeOCR</h1>

<p align="center">
  面向 Windows 的一站式 OCR 与文档处理桌面应用 —— 截图识别、批量识别、PDF 文字层、文档解析、二维码，一站式搞定。
</p>

<p align="center">
  <a href="https://github.com/FelixJI/VibeOCR/releases"><img alt="版本" src="https://img.shields.io/badge/version-0.3.1-blue" /></a>
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
- **子进程隔离** —— OCR 在独立子进程执行，隔离 GPU 上下文，主界面不卡顿
- **流水线缓存** —— 按显存分层的重型流水线缓存（FIFO 淘汰 + TTL 回收），减少重复加载
- **程序内更新** —— 自动检查新版本，国内客户端优先走 Gitee 镜像加速下载
- **系统托盘** —— 最小化到托盘，边缘悬浮工具栏快捷唤起

## 下载安装

### 方式一：下载便携版（推荐，无需配置环境）

前往 [Releases](https://github.com/FelixJI/VibeOCR/releases) 下载最新版 `VibeOCR-vX.Y.Z-win64.zip`，解压后运行 `VibeOCR.exe` 即可。

> 国内用户访问 GitHub 较慢时，可使用 Gitee 镜像：<https://gitee.com/felixjii/vibeocr/releases>

首次启动时，应用会自动检测 GPU/CPU 并引导安装推理依赖；WebEngine 渲染组件按需下载。

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
| 条码 | qrcode、python-barcode、pyzbar、OpenCV |
| 异步集成 | qasync |
| 依赖管理 | [uv](https://github.com/astral-sh/uv) |
| 代码质量 | ruff + pyright + pytest |

## 开发指南

### 环境要求

- Windows 10/11（64 位）
- Python **3.13**（仅支持 3.13，见 `pyproject.toml`）
- [uv](https://docs.astral.sh/uv/)（推荐）或 pip

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

## 项目结构

```
vibeocr/
├── src/vibeocr/
│   ├── main.py                 # 应用入口（环境变量预处理 + 启动）
│   ├── env_manager.py          # 环境与依赖管理（生产依赖检测、CUDA DLL 路径）
│   ├── core/
│   │   └── pipelines/          # 推理流水线注册表与各管道选项定义
│   ├── services/
│   │   ├── ocr_service_subprocess.py  # 子进程 OCR 服务（生产路径）
│   │   ├── ocr_worker_process.py      # OCR worker 子进程
│   │   ├── mineru_service.py          # MinerU 文档解析
│   │   ├── pdf_service.py             # PDF 操作与文字层写入
│   │   ├── pipeline_cache_manager.py  # 流水线缓存（FIFO + TTL）
│   │   └── update_service.py          # 程序内更新
│   ├── views/
│   │   ├── main_window.py             # 主窗口
│   │   ├── tabs/                      # 各功能标签页（单文件 / PDF / 二维码 / 关于）
│   │   └── settings_page_controller.py
│   ├── widgets/                       # 截图遮罩、编辑画布、结果视图等
│   ├── workers/                       # OCR / PDF 后台线程
│   ├── managers/                      # 配置、依赖、布局、子进程管理
│   └── utils/                         # CPU/GPU 信息、CJK 字体、Markdown 转换等
├── resources/                  # 应用图标、KaTeX 资源
├── scripts/                    # 版本管理（bump_version）、更新助手、CI 同步
├── qa/                         # 代码质量脚本
├── tests/                      # 测试
└── pyproject.toml
```

## 发布流程

打包与发版由 [`scripts/bump_version.py`](scripts/bump_version.py) 编排（基于 PyInstaller），
推送到 `v*` 格式的 tag 后，GitHub Actions（[`.github/workflows/release.yml`](.github/workflows/release.yml)）会：

1. 用 PyInstaller 构建便携版 zip（主包 + WebEngine 资源包 + SHA256 校验）
2. 上传到 GitHub Release
3. 镜像代码与产物到 Gitee / CNB（国内加速），并清理历史版本

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
| Gitee（国内镜像） | <https://gitee.com/felixjii/vibeocr> |
| CNB（代码镜像） | <https://cnb.cool/feljii/VibeOCR> |

## 许可证

[MIT License](LICENSE) © 2025–2026 Felix Ji

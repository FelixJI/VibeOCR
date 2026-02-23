# VibeOCR

一个简单的截图 OCR 识别工具，使用 RapidOCR 进行文字识别。

## 功能特性

- 截图识别：通过快捷键或点击触发截图，框选区域后自动识别文字
- 图片文件识别：支持打开本地图片文件进行 OCR 识别
- 多屏幕支持：支持多显示器环境下的截图
- 复制结果：一键复制识别结果到剪贴板

## 安装

需要 Python 3.10 或更高版本。

```bash
# 使用 pip 安装
pip install -e .

# 或使用 uv 安装
uv sync
```

## 使用方法

```bash
vibeocr
```

### 快捷键

- `Ctrl+O`：打开图片文件
- `Ctrl+S`：开始截图
- `Ctrl+Q`：退出程序
- `ESC`：取消截图

## 技术栈

- **GUI 框架**：PySide6
- **OCR 引擎**：RapidOCR
- **图像处理**：Pillow

## 项目结构

```
src/vibeocr/
├── main.py              # 应用程序入口
├── services/
│   └── ocr_service.py   # OCR 服务（单例模式）
├── views/
│   └── main_window.py   # 主窗口逻辑
├── widgets/
│   ├── preview_widget.py   # 图片预览组件
│   └── screenshot_widget.py # 截图遮罩组件
└── ui/
    └── main_window.ui   # Qt Designer UI 文件
```

## 运行测试

```bash
# 安装测试依赖
uv pip install -e ".[test]"

# 运行所有测试
pytest

# 运行并生成覆盖率报告
pytest --cov=src/vibeocr --cov-report=html
```

## 许可证

MIT License

# Changelog

## [0.1.1] - 2026-06-25

### Fixed
- fix(updater): 修复 item 可能未绑定导致的 Pyright 告警
- fix(pdf-text-layer): 修复带 /Rotate 页面文字层坐标旋转错位
- fix(日志): 修正推理硬件误报 + 预热输出设备信息 + 第三方库降噪

### Changed
- refactor(env): 修正 torch 镜像源、移除无调用方的安装入口、修复更新后依赖降级
- chore(版权): 版权年份更新为 2025–2026，关于页年份改为运行时取系统日期

## [0.1.0] - 2025-01-01

### Added
- 项目初始化
- 截图 OCR 识别功能
- PaddleOCR 集成
- MinerU 文档解析集成
- 批量识别功能

# Changelog

## [0.1.6] - 2026-06-25

### Added
- feat(bump): 交互式菜单新增'仅打包当前版本'选项

### Fixed
- fix(install): _install_paddle_stack 兼容打包环境 paddlepaddle 键名，修复 KeyError
- fix(test): uv.lock 路径支持环境变量隔离，修复 10 个预存测试失败

## [0.1.5] - 2026-06-25

### Added
- feat(settings): 应用设置页连接重装 Python/依赖按钮 + 状态刷新
- feat(ui): 应用设置页新增'环境维护'分组（重装按钮）
- feat(dialog): BackendChoiceDialog 透传 reinstall_python
- feat(install): InstallWorker 加 reinstall_python + 进度日志镜像
- feat(env): 新增 reinstall_embedded_python 强制删除后重装

### Changed
- chore: lint 清理（安装日志 + 重装入口）
- refactor(env): 依赖安装/后端切换 report 闭包改用 logging
- refactor(env): install_embedded_python 改用 logging 落盘日志
- refactor(env): download_file_with_progress 改用 logging 落盘日志
- docs(plan): 安装日志接入 logging + 设置页重装入口实施计划
- docs: 安装日志接入 logging + 设置页重装入口设计

## [0.1.4] - 2026-06-25

### Fixed
- fix(打包): 修正方案A误删 Qt6Qml*/Qt6Quick 导致 QtWebChannel 加载失败

### Changed
- chore(换行符): 新增 .gitattributes 统一 LF，消除 autocrlf 警告

## [0.1.3] - 2026-06-25

### Fixed
- fix(发版): 发版时自动同步 uv.lock，修正版本号滞后漂移
- fix(打包): 修复进程递归卡死/体积臃肿/安装入口缺失/路径解析错误

## \[0.1.2] - 2026-06-25

### Fixed

* fix(pyright): 扩展检查范围至 scripts/tests/qa/examples 并清零 156 个 error

### Changed

* refactor(质量): ruff/pyright 全量清零 + 版本测试永久免疫 bump

## \[0.1.1] - 2026-06-25

### Fixed

* fix(updater): 修复 item 可能未绑定导致的 Pyright 告警
* fix(pdf-text-layer): 修复带 /Rotate 页面文字层坐标旋转错位
* fix(日志): 修正推理硬件误报 + 预热输出设备信息 + 第三方库降噪

### Changed

* refactor(env): 修正 torch 镜像源、移除无调用方的安装入口、修复更新后依赖降级

## \[0.1.0] - 2025-01-01

### Added

* 项目初始化
* 截图 OCR 识别功能
* PaddleOCR 集成
* MinerU 文档解析集成
* 批量识别功能


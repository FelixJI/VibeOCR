# Changelog

## [0.1.6] - 2026-06-26

### Added
- 项目初始化
- 截图 OCR 识别功能
- PaddleOCR 集成
- MinerU 文档解析集成
- 批量识别功能
- 应用设置页新增「环境维护」分组（重装 Python/依赖按钮）
- BackendChoiceDialog 透传 reinstall_python
- InstallWorker 支持 reinstall_python + 进度日志镜像
- reinstall_embedded_python 强制删除后重装
- 交互式菜单新增「仅打包当前版本」选项

### Changed
- 安装/下载日志接入 logging（替代 print）
- torch 镜像源修正、移除无调用方安装入口
- ruff/pyright 全量清零 + 版本测试永久免疫 bump
- 统一 LF 换行符（.gitattributes）
- 发版时自动同步 uv.lock

### Fixed
- updater 未绑定变量告警
- PDF 文字层旋转页面坐标错位
- 推理硬件误报 + 预热输出设备信息 + 第三方库降噪
- _install_paddle_stack 兼容打包环境键名（KeyError）
- uv.lock 路径支持环境变量隔离
- 方案 A 打包误删 Qt6Qml/Quick 导致 QtWebChannel 加载失败
- 发版 uv.lock 版本号滞后漂移
- 打包进程递归卡死 / 体积臃肿 / 安装入口缺失 / 路径解析错误

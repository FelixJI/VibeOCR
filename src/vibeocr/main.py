"""VibeOCR 应用程序入口点"""

import os
import sys
from pathlib import Path

# ============================================================
# 重要：必须在导入任何其他模块之前设置以下环境变量
# 这些设置解决 Windows + PaddlePaddle + NumPy 环境下的常见崩溃问题
# ============================================================

# 解决 OpenMP 库冲突 (libiomp5md.dll 重复加载导致 0xC0000005 崩溃)
# 当多个库（PaddlePaddle、NumPy、Intel MKL）各自捆绑不同版本的 OpenMP 时会发生冲突
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

# 禁用 OneDNN 以提高兼容性（某些 CPU 指令集不兼容会导致崩溃）
os.environ.setdefault("FLAGS_enable_onednn_backend", "0")
os.environ.setdefault("FLAGS_use_mkldnn", "0")

# 设置环境变量以抑制不必要的警告
# 禁用 PaddleX 的模型源连接检查
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

# 导入环境管理模块
from vibeocr import env_manager

# 确保src目录在路径中
src_path = Path(__file__).parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))


def check_production_dependencies() -> bool:
    """检查生产环境依赖

    Returns:
        是否所有生产依赖都已安装
    """
    ready, missing = env_manager.is_production_environment_ready()
    if not ready:
        print(f"[VibeOCR] 缺少生产依赖: {', '.join(missing)}")
        print("[VibeOCR] 请使用以下命令安装:")
        print("  pip install pyside6 pillow")
        print("  或")
        print("  uv sync")
    return ready


def _create_tray_icon(app, window, app_settings):
    """创建系统托盘图标

    Args:
        app: QApplication 实例
        window: MainWindow 实例
        app_settings: AppSettings 实例

    Returns:
        QSystemTrayIcon 实例，如果不支持返回 None
    """
    from PySide6.QtGui import QAction, QIcon
    from PySide6.QtWidgets import QMenu, QSystemTrayIcon

    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("[VibeOCR] 系统不支持托盘图标")
        return None

    # 使用应用默认图标，如果没有则创建简单的彩色图标
    icon = app.windowIcon()
    if icon.isNull():
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QColor, QPixmap

        pixmap = QPixmap(QSize(64, 64))
        pixmap.fill(QColor("#0078d4"))
        icon = QIcon(pixmap)

    tray = QSystemTrayIcon(icon, app)
    tray.setToolTip("VibeOCR")

    # 上下文菜单
    menu = QMenu()

    action_show = QAction("显示主窗口", menu)
    action_show.triggered.connect(lambda: _show_main_window(window))
    menu.addAction(action_show)

    action_settings = QAction("设置", menu)
    action_settings.triggered.connect(
        lambda: _show_tray_settings(app_settings, window)
    )
    menu.addAction(action_settings)

    menu.addSeparator()

    action_quit = QAction("退出", menu)
    action_quit.triggered.connect(lambda: _quit_app(app, window))
    menu.addAction(action_quit)

    tray.setContextMenu(menu)

    # 点击托盘图标切换主窗口显示
    tray.activated.connect(
        lambda reason: _on_tray_activated(reason, window)
    )

    tray.show()
    return tray


def _show_main_window(window):
    """显示并激活主窗口"""
    window.showNormal()
    window.activateWindow()
    window.raise_()


def _show_tray_settings(app_settings, parent):
    """从托盘菜单打开设置对话框"""
    from vibeocr.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(app_settings, parent)
    if dialog.exec():
        # 设置已保存，通知主窗口刷新
        if hasattr(parent, "apply_app_settings"):
            parent.apply_app_settings()


def _quit_app(app, window):
    """完全退出应用"""
    # 标记为真正退出（而非最小化到托盘）
    window._force_quit = True
    window.close()
    app.quit()


def _on_tray_activated(reason, window):
    """托盘图标激活事件"""
    from PySide6.QtWidgets import QSystemTrayIcon

    if reason == QSystemTrayIcon.ActivationReason.Trigger:
        if window.isVisible() and not window.isMinimized():
            window.hide()
        else:
            _show_main_window(window)


def launch_application() -> int:
    """启动应用程序"""
    from PySide6.QtWidgets import QApplication

    from vibeocr.utils.app_settings import AppSettings
    from vibeocr.utils.qt_async import create_qasync_event_loop
    from vibeocr.views.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("VibeOCR")
    app.setApplicationVersion("0.1.0")

    # 加载应用设置
    project_root = env_manager.get_project_root()
    app_settings = AppSettings(project_root / "config")

    # 创建 qasync 事件循环（整合 Qt 和 asyncio）
    loop = create_qasync_event_loop(app)

    window = MainWindow()
    window.set_app_settings(app_settings)
    window.show()

    # 创建系统托盘图标
    tray = _create_tray_icon(app, window, app_settings)
    if tray:
        window.set_tray_icon(tray)
        # 托盘模式下关闭窗口不退出程序
        app.setQuitOnLastWindowClosed(False)

    # 使用 qasync 事件循环运行应用
    try:
        with loop:
            loop.run_forever()
    except Exception as e:
        print(f"[VibeOCR] 应用异常退出: {e}")
        return 1

    return 0


def main() -> int:
    """应用程序入口点

    启动流程：
    1. 检测生产环境依赖（PySide6, Pillow）
    2. 失败 → 控制台错误提示，退出
    3. 通过 → 启动GUI
    4. GUI启动后 → 异步检测嵌入式OCR依赖
    """

    # 1. 检查生产环境依赖
    if not check_production_dependencies():
        input("\n按回车键退出...")
        return 1

    # 2. 启动应用
    print("[VibeOCR] 启动应用...")
    return launch_application()


if __name__ == "__main__":
    sys.exit(main())

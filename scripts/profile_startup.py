"""启动时间分析脚本

用法: python scripts/profile_startup.py
"""

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

# 添加项目路径
project_root = Path(__file__).parent.parent
src_path = project_root / "src"
sys.path.insert(0, str(src_path))

# 设置环境变量
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

# 全局计时器
_timings: list[tuple[str, float, float]] = []
_start_time = time.perf_counter()


def mark(stage: str) -> None:
    """记录一个时间点"""
    elapsed = time.perf_counter() - _start_time
    _timings.append((stage, elapsed, 0))
    print(f"[启动分析] {stage}: {elapsed:.3f}s")


@contextmanager
def measure(name: str):
    """测量一个代码块的执行时间"""
    start = time.perf_counter()
    print(f"[启动分析] 开始: {name}")
    yield
    elapsed = time.perf_counter() - start
    print(f"[启动分析] 完成: {name} - {elapsed:.3f}s")
    _timings.append((name, time.perf_counter() - _start_time, elapsed))


def print_report():
    """打印分析报告"""
    print("\n" + "=" * 60)
    print("启动时间分析报告")
    print("=" * 60)

    total = time.perf_counter() - _start_time
    print(f"\n总启动时间: {total:.3f}s\n")

    print(f"{'阶段':<40} {'累计时间':>10} {'阶段耗时':>10}")
    print("-" * 60)

    prev_time = 0
    for stage, cumulative, duration in _timings:
        if duration > 0:
            # 这是一个测量块
            print(f"{stage:<40} {cumulative:>9.3f}s {duration:>9.3f}s")
        else:
            # 这是一个标记点
            stage_duration = cumulative - prev_time
            print(f"{stage:<40} {cumulative:>9.3f}s {stage_duration:>9.3f}s")
        prev_time = cumulative

    print("-" * 60)
    print(f"{'总计':<40} {total:>9.3f}s")
    print("=" * 60)


def profile_imports():
    """分析各模块的导入时间"""
    print("\n" + "=" * 60)
    print("模块导入时间分析")
    print("=" * 60)

    modules_to_test = [
        ("os, sys, pathlib", lambda: None),  # 基础模块已导入
        ("PySide6.QtWidgets", lambda: __import__("PySide6.QtWidgets")),
        ("PySide6.QtCore", lambda: __import__("PySide6.QtCore")),
        ("PySide6.QtGui", lambda: __import__("PySide6.QtGui")),
        ("PySide6.QtUiTools", lambda: __import__("PySide6.QtUiTools")),
        ("PIL.Image", lambda: __import__("PIL.Image")),
        (
            "vibeocr.env_manager",
            lambda: __import__("vibeocr.env_manager", fromlist=["env_manager"]),
        ),
        (
            "vibeocr.machine_cache",
            lambda: __import__("vibeocr.machine_cache", fromlist=["machine_cache"]),
        ),
        (
            "vibeocr.model_cache_manager",
            lambda: __import__(
                "vibeocr.model_cache_manager", fromlist=["model_cache_manager"]
            ),
        ),
    ]

    # 需要重新测试的模块（需要清除缓存）
    results = []

    for name, import_func in modules_to_test:
        # 跳过已导入的模块
        if name in ["os, sys, pathlib"]:
            results.append((name, 0, "已导入"))
            continue

        try:
            start = time.perf_counter()
            import_func()
            elapsed = time.perf_counter() - start
            status = "OK"
            results.append((name, elapsed, status))
        except ImportError as e:
            results.append((name, 0, f"导入失败: {e}"))

    print(f"\n{'模块':<35} {'导入时间':>12} {'状态':>10}")
    print("-" * 60)
    for name, elapsed, status in results:
        print(f"{name:<35} {elapsed:>11.3f}s {status:>10}")
    print("=" * 60)


def profile_startup():
    """分析完整启动流程"""
    global _start_time
    _start_time = time.perf_counter()
    _timings.clear()

    # 1. 导入环境管理模块
    with measure("导入 env_manager"):
        from vibeocr import env_manager

    # 2. 检查生产依赖
    with measure("检查生产依赖"):
        ready, missing = env_manager.is_production_environment_ready()

    if not ready:
        print(f"[启动分析] 缺少生产依赖: {missing}")
        return

    # 3. 导入 Qt 模块
    with measure("导入 PySide6.QtWidgets"):
        from PySide6.QtWidgets import QApplication

    with measure("导入 PySide6.QtCore"):
        pass

    # 4. 创建 QApplication
    with measure("创建 QApplication"):
        app = QApplication(sys.argv)
        app.setApplicationName("VibeOCR")
        app.setApplicationVersion("0.1.0")

    # 5. 导入主窗口模块
    with measure("导入 MainWindow 模块"):
        from vibeocr.views.main_window import MainWindow

    # 6. 创建主窗口
    with measure("创建 MainWindow"):
        window = MainWindow()

    # 7. 显示窗口
    with measure("显示窗口"):
        window.show()

    mark("窗口已显示")

    # 打印报告
    print_report()

    # 返回 app 以便继续运行（可选）
    return app, window


def profile_startup_full():
    """完整启动分析（包括模块导入）"""
    global _start_time
    _start_time = time.perf_counter()
    _timings.clear()

    mark("脚本开始")

    # 模拟完整启动
    with measure("导入 env_manager"):
        from vibeocr import env_manager

    mark("env_manager 导入完成")

    with measure("检查生产依赖"):
        ready, missing = env_manager.is_production_environment_ready()

    if not ready:
        print(f"缺少生产依赖: {missing}")
        return None, None

    with measure("导入 PySide6 模块"):
        from PySide6.QtWidgets import QApplication

    mark("Qt 模块导入完成")

    with measure("创建 QApplication"):
        app = QApplication(sys.argv)

    mark("QApplication 创建完成")

    with measure("导入 MainWindow"):
        from vibeocr.views.main_window import MainWindow

    mark("MainWindow 模块导入完成")

    with measure("创建 MainWindow 实例"):
        window = MainWindow()

    mark("MainWindow 实例创建完成")

    with measure("显示窗口"):
        window.show()

    mark("窗口显示完成")

    # 等待依赖检查完成
    print("\n[启动分析] 等待依赖检查完成...")
    time.sleep(2)  # 等待异步依赖检查

    mark("依赖检查完成（预估）")

    print_report()

    return app, window


def main():
    """主函数"""
    print("=" * 60)
    print("VibeOCR 启动时间分析")
    print("=" * 60)

    # 1. 先分析模块导入时间
    print("\n[阶段 1] 分析模块导入时间...")
    profile_imports()

    # 2. 分析完整启动流程
    print("\n[阶段 2] 分析完整启动流程...")
    app, window = profile_startup_full()

    if app:
        print("\n[启动分析] 分析完成，3秒后退出...")
        QTimer.singleShot(3000, app.quit)
        app.exec()


if __name__ == "__main__":
    main()

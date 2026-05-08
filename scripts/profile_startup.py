"""VibeOCR 启动耗时分析脚本

运行方式: python -m scripts.profile_startup
输出: 控制台表格 + logs/startup_profile.log

采集三层耗时:
1. Import 层 — 重量级模块导入时间
2. 初始化层 — MainWindow 各阶段
3. 渲染层 — window.show() 到实际可见
"""

import os
import sys
import time
from pathlib import Path

# 环境变量（与 main.py 一致）
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("FLAGS_enable_onednn_backend", "0")
os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

# 确保 src 目录在路径中
src_path = Path(__file__).resolve().parent.parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# 日志输出目录
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


class ImportProfiler:
    """采集模块导入耗时"""

    def __init__(self):
        self._import_times: dict[str, float] = {}
        self._original_import = None

    def start(self):
        self._original_import = __builtins__.__import__
        __builtins__.__import__ = self._timing_import

    def stop(self):
        if self._original_import:
            __builtins__.__import__ = self._original_import

    def _timing_import(self, name, *args, **kwargs):
        if not name.startswith("_"):
            start = time.perf_counter()
        result = self._original_import(name, *args, **kwargs)
        if not name.startswith("_"):
            elapsed = time.perf_counter() - start
            top_level = name.split(".")[0]
            self._import_times[top_level] = (
                self._import_times.get(top_level, 0) + elapsed
            )
        return result

    def get_results(self) -> dict[str, float]:
        return dict(
            sorted(self._import_times.items(), key=lambda x: x[1], reverse=True)
        )


class StartupProfiler:
    """启动流程分段计时"""

    def __init__(self):
        self._stamps: list[tuple[str, float]] = []

    def mark(self, label: str):
        self._stamps.append((label, time.perf_counter()))

    def report(self) -> str:
        lines = []
        lines.append("=" * 70)
        lines.append("VibeOCR 启动耗时分析报告")
        lines.append("=" * 70)

        if len(self._stamps) < 2:
            lines.append("数据不足，至少需要 2 个时间戳")
            return "\n".join(lines)

        total = self._stamps[-1][1] - self._stamps[0][1]
        lines.append(f"\n总耗时: {total:.3f}s\n")
        lines.append(f"{'阶段':<45} {'耗时':>8} {'占比':>8}")
        lines.append("-" * 70)

        for i in range(len(self._stamps) - 1):
            label_start, t_start = self._stamps[i]
            _, t_end = self._stamps[i + 1]
            elapsed = t_end - t_start
            pct = (elapsed / total * 100) if total > 0 else 0
            lines.append(f"{label_start:<45} {elapsed:>7.3f}s {pct:>6.1f}%")

        lines.append("-" * 70)
        lines.append(f"{'总计':<45} {total:>7.3f}s {'100.0%':>8}")
        return "\n".join(lines)


def profile_imports():
    """采集 import 耗时"""
    profiler = ImportProfiler()
    profiler.start()

    t0 = time.perf_counter()
    from vibeocr import env_manager
    import_time = time.perf_counter() - t0

    profiler.stop()
    return profiler.get_results(), import_time


def profile_gui_startup():
    """采集 GUI 启动各阶段耗时"""
    sp = StartupProfiler()

    sp.mark("import PySide6 + env_manager")
    from PySide6.QtWidgets import QApplication
    from vibeocr import env_manager
    sp.mark("创建 QApplication")

    app = QApplication(sys.argv)
    sp.mark("初始化 ConfigManager")

    from vibeocr.managers.config_manager import ConfigManager
    project_root = env_manager.get_project_root()
    cm = ConfigManager.instance(project_root)
    sp.mark("加载 AppSettings")

    from vibeocr.utils.app_settings import AppSettings
    app_settings = AppSettings(cm)
    sp.mark("创建 qasync 事件循环")

    from vibeocr.utils.qt_async import create_qasync_event_loop
    loop = create_qasync_event_loop(app)
    sp.mark("创建 MainWindow")

    from vibeocr.views.main_window import MainWindow
    window = MainWindow()
    sp.mark("set_app_settings")

    window.set_app_settings(app_settings)
    sp.mark("window.show()")

    window.show()
    sp.mark("事件循环首次 idle")

    # 处理一次事件循环让 UI 真正渲染
    app.processEvents()
    sp.mark("完成")

    # 清理
    window.close()
    app.quit()

    return sp


def run_profile():
    """运行完整分析"""
    print("正在分析启动耗时...\n")

    # Import 分析
    print("阶段 1: Import 耗时分析")
    print("-" * 50)
    import_times, total_import = profile_imports()
    for mod, t in list(import_times.items())[:20]:
        if t > 0.01:
            print(f"  {mod:<35} {t:.3f}s")
    print(f"  {'env_manager 总计':<35} {total_import:.3f}s\n")

    # GUI 启动分析
    print("阶段 2: GUI 启动流程分析")
    print("-" * 50)
    sp = profile_gui_startup()
    report = sp.report()
    print(report)

    # 写入文件
    log_file = LOG_DIR / "startup_profile.log"
    with open(log_file, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n报告已保存到: {log_file}")


if __name__ == "__main__":
    run_profile()

"""VibeOCR 启动耗时分析脚本

运行方式: python -m scripts.profile_startup
输出: 控制台表格 + logs/startup_profile.log

采集三层耗时:
1. Import 层 — 重量级模块导入时间
2. 初始化层 — MainWindow 各阶段
3. 渲染层 — window.show() 到实际可见
"""

import builtins
import os
import sys
import time
from pathlib import Path
from typing import Any

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
    """采集模块导入耗时。

    通过 monkey-patch ``builtins.__import__`` 拦截所有 import 调用，
    记录每个顶层包的累计导入耗时。**必须在 start()/stop() 之间执行
    真实 import**——profiler 只测量发生在活跃期间的导入。
    """

    def __init__(self) -> None:
        self._import_times: dict[str, float] = {}
        # builtins.__import__ 的原始引用，start() 时保存。
        self._original_import: Any = None
        self._active = False

    def start(self) -> None:
        """开始拦截 import 调用。"""
        self._original_import = builtins.__import__
        builtins.__import__ = self._timing_import  # type: ignore[assignment]
        self._active = True

    def stop(self) -> None:
        """恢复原始 __import__。"""
        if self._original_import is not None:
            builtins.__import__ = self._original_import
        self._active = False

    def _timing_import(self, name: str, *args: Any, **kwargs: Any) -> Any:
        tracked = not name.startswith("_")
        start = time.perf_counter() if tracked else 0.0
        result = self._original_import(name, *args, **kwargs)
        if tracked:
            elapsed = time.perf_counter() - start
            top_level = name.split(".")[0]
            self._import_times[top_level] = (
                self._import_times.get(top_level, 0.0) + elapsed
            )
        return result

    def get_results(self) -> dict[str, float]:
        """返回 {顶层包名: 累计耗时} 按耗时降序。"""
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


def profile_imports() -> tuple[dict[str, float], float]:
    """采集 VibeOCR 启动链路中重量级模块的真实 import 耗时。

    修复前 bug：start()/stop() 之间没有任何 import 调用，total_import 恒为 ~0。
    现在在 profiler 活跃期间导入 env_manager（主入口顶层导入的模块）及其
    传递依赖，使 ImportProfiler 能拦截并记录真实耗时。

    Returns:
        (import_times, total_import) — import_times 按耗时降序，
        total_import 是 profiler 活跃期间的总墙钟时间。
    """
    # 清除可能已缓存的模块，强制重新 import（否则 sys.modules 命中不触发 __import__）
    _evict_vibeocr_modules()

    profiler = ImportProfiler()
    profiler.start()

    t0 = time.perf_counter()
    # 执行真实 import：env_manager 是 main.py 的顶层导入，
    # 会触发 numpy/PIL/httpx/pydantic 等传递依赖的加载。
    try:
        import vibeocr.env_manager  # noqa: F401  # pyright: ignore[reportUnusedImport]
    except Exception:
        # 即使 import 失败也恢复 __import__（避免全局污染）
        pass
    total_import = time.perf_counter() - t0

    profiler.stop()
    return profiler.get_results(), total_import


def _evict_vibeocr_modules() -> None:
    """从 sys.modules 移除 vibeocr.* 及其常见重依赖，使下次 import 重新加载。

    只移除 vibeocr 自身模块；第三方依赖（numpy/PIL 等）若已在测试进程加载
    则保留（避免重复加载 C 扩展导致 crash）。在独立脚本进程中无此问题。
    """
    to_remove = [
        name for name in list(sys.modules) if name.startswith("vibeocr")
    ]
    for name in to_remove:
        del sys.modules[name]


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

    loop = create_qasync_event_loop(app)  # noqa: F841 (持有引用，防止事件循环被回收)
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


def run_multi_profile(runs: int, output: str) -> None:
    """运行多次独立进程采样，输出 T0–T6 p50/p95 汇总。

    每次采样是一个独立 Python 子进程（设置 VIBEOCR_STARTUP_TRACE 输出 JSONL），
    以避免单进程内重复 import 被缓存。汇总所有 run 的里程碑时间戳后输出 JSON。
    """
    import json as _json
    import subprocess

    from vibeocr.startup_metrics import summarize_runs

    trace_file = LOG_DIR / "multi-startup-trace.jsonl"
    trace_file.parent.mkdir(parents=True, exist_ok=True)
    # 清空旧 trace
    trace_file.write_text("", encoding="utf-8")

    print(f"采集 {runs} 次独立进程启动样本...")

    all_runs: list[dict[str, float]] = []
    for i in range(runs):
        # 每次子进程设置 VIBEOCR_STARTUP_TRACE 写入同一个 trace 文件
        env = os.environ.copy()
        env["VIBEOCR_STARTUP_TRACE"] = str(trace_file)
        env["VIBEOCR_STARTUP_PROFILE_MODE"] = "1"  # 让子进程快速退出
        # 运行一个最小启动脚本（只到 T3 首窗，不等 T6 预加载）
        result = subprocess.run(
            [sys.executable, "-c", _MINIMAL_STARTUP_SCRIPT],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        if result.returncode != 0:
            print(f"  run {i+1}: FAILED (exit {result.returncode})")
            continue
        print(f"  run {i+1}: OK")

    # 读取 trace 文件中的所有 run
    if trace_file.exists():
        for line in trace_file.read_text(encoding="utf-8").strip().split("\n"):
            if line.strip():
                try:
                    all_runs.append(_json.loads(line))
                except _json.JSONDecodeError:
                    continue

    if not all_runs:
        print("\n警告：未采集到有效样本")
        return

    summary = summarize_runs(all_runs)
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        _json.dumps(
            {"runs": len(all_runs), "summary": summary, "raw": all_runs},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n{len(all_runs)} 次样本汇总已保存到: {output_path}")
    print("\n里程碑 p50/p95：")
    for ev in ["T0", "T1", "T2", "T3", "T4", "T5", "T6"]:
        if ev in summary:
            s = summary[ev]
            print(f"  {ev}: p50={s['p50']:.3f}s p95={s['p95']:.3f}s (n={int(s['count'])})")


# 最小启动脚本：导入并初始化到 T3 首窗，然后退出（不启动 OCR 预加载）。
_MINIMAL_STARTUP_SCRIPT = """
import os, sys
sys.path.insert(0, os.path.join(os.getcwd(), "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 导入 main 触发 T0/T1 记录（main 模块顶层记录 PROCESS_START 和 RUNTIME_READY）
import vibeocr.main  # noqa: F401
from vibeocr.startup_metrics import StartupEvent, record_startup, flush_startup
record_startup(StartupEvent.SHELL_CREATED)  # T2（offscreen 模式无真实窗口）
record_startup(StartupEvent.FIRST_WINDOW)   # T3
flush_startup()
"""


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="VibeOCR 启动耗时分析")
    parser.add_argument(
        "--runs",
        type=int,
        default=0,
        help="独立进程采样次数（>0 时输出 p50/p95 汇总）",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="reports/local/python-startup.json",
        help="多次采样汇总输出路径",
    )
    args = parser.parse_args()

    if args.runs > 0:
        run_multi_profile(args.runs, args.output)
    else:
        run_profile()

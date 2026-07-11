# Phase 0 基线：Python Shell（2026-07-11）

## 概要

Phase 0 修复了当前 Python/PySide6 正式版中的构建、启动、路径、退出和并发问题，
建立了可复现的质量门禁。本基线记录门禁通过时的测试数、产物体积和启动指标。

## 门禁状态

| 步骤 | 结果 | 耗时 |
|---|---|---|
| `uv sync --frozen --group dev` | PASS | <0.1s |
| `pytest -q` | PASS（0 failed） | ~22s |
| `ruff check src tests scripts` | PASS | <0.2s |
| `pyright` | PASS（0 errors） | ~9s |

## 测试基线

- pytest：全部通过（含新增 application/build_manifest/startup_metrics/app_paths 测试）
- ruff：0 errors（Phase 0 开始时 314 errors，全部清零）
- pyright：0 errors（Phase 0 开始时 147 errors，全部清零）

## 产物清单

Phase 0 新增的可复现构建保障：

- `requirements/build-shell.in` / `build-shell.lock`：CI 打包壳的哈希锁定依赖。
- `src/vibeocr/build_manifest.py`：产物清单生成 + 校验（reject output/ 等）。
- `artifact-manifest.json` 内嵌于发布 ZIP，`python -m vibeocr.build_manifest verify` 可校验。

## 启动指标 T0–T6

Phase 0 建立了可信的启动里程碑体系：

| 里程碑 | 含义 | 插桩位置 |
|---|---|---|
| T0 | 进程入口 | main.py 顶层 |
| T1 | 运行时就绪（env_manager） | main.py |
| T2 | Qt 壳创建 | launch_application |
| T3 | 首窗可见 | window.show() |
| T4 | Worker ready | _on_subprocess_worker_ready |
| T5 | Backend ready | _on_preload_finished |
| T6 | 首次可交互 | _on_preload_finished |

设置 `VIBEOCR_STARTUP_TRACE=<path>` 输出 JSONL；`scripts/profile_startup.py --runs 10`
采集多次独立进程 p50/p95。

## 并发修复

Phase 0 消除了所有生产 `QThread.terminate()` 调用：

1. **预加载任务**（Task 0.5）：PreloadTask + PreloadWithWarmupTask 注入 threading.Event
   协作取消；shutdown 先 cancel 再关闭 service。
2. **PDF 缩略图**（Task 0.6）：render_thumbnail 加有界 HTTP 超时；ThreadPoolExecutor
   用 cancel_futures=True；_wait_thread 返回 bool 不调 terminate。

验证：`rg "QThread\.terminate|worker\.terminate\(\)" src/vibeocr` 只命中注释。

## 架构改进

- **AppPaths**（Task 0.4）：路径单一边界，production/winui-dev profile，UI-free。
- **Application facades**（Task 0.7）：OCR/PDF/Settings facade，UI-free 边界，
   供 WorkerHost 和 WinUI 壳共享。
- **Startup metrics**（Task 0.3）：T0–T6 可信测量，修复 profile_imports bug。

## ZIP 体积

- GitHub 干净构建约 160 MB（不含 output/，与 v0.4.22 一致）。
- 历史"本地脏工作区 728 MB"反例由 build_manifest manifest 校验拦截。

## 与 Phase 1 的衔接

Phase 0 基线为 Phase 1（WorkerHost 协议）提供：
- 可复现的构建和测试门禁
- UI-free application facades（WorkerHost handler 直接委托）
- 协作取消的参考实现（PreloadTask 的 threading.Event 模式）
- AppPaths 的 profile 机制（winui-dev 旁路开发）

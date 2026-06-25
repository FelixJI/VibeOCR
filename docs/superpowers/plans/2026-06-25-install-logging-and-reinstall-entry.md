# 安装日志接入 logging + 设置页重装入口 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让安装过程日志写入 `vibeocr.log`（便于客户报错排查），并在设置页"应用设置"新增"环境维护"分组，支持重装 Python 运行时和重装 OCR 依赖。

**Architecture:** ① `env_manager` 安装函数的 `print` → `logging`（并行 `progress_callback` 保留）；② 新增 `reinstall_embedded_python`（删 `python/` 后重装）；③ `InstallWorker` / `BackendChoiceDialog` 加 `reinstall_python` 参数透传；④ `main_window.ui` + 生成的 `ui_main_window.py` 加分组；⑤ `settings_page_controller` 连接按钮 + 确认对话框。

**Tech Stack:** Python 3.13, PySide6, pytest + pytest-qt (qtbot), PyInstaller (打包态)。

**关联设计文档:** `docs/superpowers/specs/2026-06-25-install-logging-and-reinstall-entry-design.md`

---

## 关键上下文（工程师必读）

- **`print` → `logging` 转换规则**：普通 `print(f"[{stage}] {msg}")` → `logger.info("[%s] %s", stage, msg)`；带 `\r` 的进度刷新 `print(f"\r[{desc}] 进度: {p}% ...", end="")` → `logger.info("[%s] 进度: %d%% (%dMB/%dMB)", ...)`（去掉 `\r` 和 `end=""`，日志文件不需要原地刷新）；失败 `print(f"... 失败: {e}")` → `logger.error("... 失败: %s", e)`。统一用 `%` 占位符（logging 惯例）。
- **`progress_callback` 机制保留不动**：UI 进度仍走 `progress_callback(stage, msg)` 信号到 `QTextEdit`，logging 是额外并行落盘。
- **删除范围（最关键安全点）**：`reinstall_embedded_python` 仅删 `project_root/python/` 整个目录，**不删** `.venv`、`config/`、`resources/`、`logs/`、模型缓存、机器检测缓存。`重装 OCR 依赖` 不删任何目录。
- **测试惯例**：`tests/test_env_manager_install.py` 用 `unittest.mock.patch` patch `vibeocr.env_manager.<func>`，用 `tmp_path` fixture。widget 测试用 `qtbot` fixture + `qtbot.addWidget(widget)` + `qtbot.waitSignal(worker.finished)`。
- **开发态保护**：`get_environment_mode()` 非 `"portable"` 时禁用两按钮（仅打包态 portable 模式可用重装）。
- **不要重新生成 ui_main_window.py**：本项目手工同步 `.ui` XML 和生成的 `.py`。两个文件都要改，保持一致。

---

## Task 1: env_manager 加 logger + download_file_with_progress 改 logging

**Files:**
- Modify: `src/vibeocr/env_manager.py:3` (加 import logging)
- Modify: `src/vibeocr/env_manager.py:80` (加 logger 实例)
- Modify: `src/vibeocr/env_manager.py:292-325` (`download_file_with_progress`)
- Test: `tests/test_env_manager_install.py` (新增 TestInstallLogging 类)

- [ ] **Step 1: 写失败测试 — 验证 download_file_with_progress 用 logging**

在 `tests/test_env_manager_install.py` 末尾追加：

```python
class TestInstallLogging:
    """安装过程日志应走 logging（写 vibeocr.log）而非 print"""

    def test_download_with_progress_uses_logging(self, tmp_path, caplog):
        """download_file_with_progress 应通过 logger.info 输出，不依赖 print"""
        import logging
        from vibeocr.env_manager import download_file_with_progress

        dest = tmp_path / "fake.tar.gz"
        with (
            patch("vibeocr.env_manager.urlopen") as mock_urlopen,
            patch("vibeocr.env_manager.Request"),
        ):
            # 构造一个最小 response：content-length=4，body=b"data"
            fake_resp = MagicMock()
            fake_resp.headers = {"content-length": "4"}
            fake_resp.__enter__ = MagicMock(return_value=fake_resp)
            fake_resp.__exit__ = MagicMock(return_value=False)
            fake_resp.read.return_value = b"data"
            mock_urlopen.return_value = fake_resp

            with caplog.at_level(logging.INFO, logger="vibeocr.env_manager"):
                ok = download_file_with_progress("http://x/y.tar.gz", dest, "Python(镜像)")

        assert ok
        # 应有 info 级日志记录下载开始（message 含"正在下载"）
        info_msgs = [r.message for r in caplog.records if r.levelno == logging.INFO]
        assert any("正在下载" in m for m in info_msgs), (
            f"应通过 logger.info 输出下载开始，实际 records: {info_msgs}"
        )
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_env_manager_install.py::TestInstallLogging::test_download_with_progress_uses_logging -xvs`
Expected: FAIL（当前用 print，caplog 捕获不到 info 日志）

- [ ] **Step 3: 实现 — 加 logger + 改 download_file_with_progress**

在 `src/vibeocr/env_manager.py`，先在 import 区（第 3 行 `import os` 附近）加：

```python
import logging
```

然后在第 80 行 `_dep_specs_cache: dict[str, str] | None = None` 上方加：

```python
logger = logging.getLogger(__name__)
```

然后把 `download_file_with_progress`（L292-325）整体替换为：

```python
def download_file_with_progress(
    url: str, dest_path: Path, description: str = "下载"
) -> bool:
    """下载文件并显示进度"""
    try:
        logger.info("[%s] 正在下载: %s", description, url)
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})

        with urlopen(req, timeout=30) as response:
            total_size = int(response.headers.get("content-length", 0))
            downloaded = 0
            chunk_size = 8192
            last_pct = -1

            with open(dest_path, "wb") as f:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)

                    if total_size > 0:
                        progress = int(downloaded / total_size * 100)
                        # 每 10% 记一条日志（避免日志爆炸）
                        if progress >= last_pct + 10:
                            last_pct = (progress // 10) * 10
                            logger.info(
                                "[%s] 进度: %d%% (%dMB / %dMB)",
                                description,
                                progress,
                                downloaded // 1024 // 1024,
                                total_size // 1024 // 1024,
                            )

        logger.info("[%s] 下载完成", description)
        return True

    except Exception as e:
        logger.error("[%s] 下载失败: %s", description, e)
        return False
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/test_env_manager_install.py::TestInstallLogging::test_download_with_progress_uses_logging -xvs`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/vibeocr/env_manager.py tests/test_env_manager_install.py
git commit -m "refactor(env): download_file_with_progress 改用 logging 落盘日志"
```

---

## Task 2: install_embedded_python 改 logging

**Files:**
- Modify: `src/vibeocr/env_manager.py:361-363, 386, 402, 436, 461-467` (`install_embedded_python` 内的 print)
- Test: `tests/test_env_manager_install.py` (扩展 TestInstallLogging)

- [ ] **Step 1: 写失败测试 — 验证 install_embedded_python 各阶段有日志**

在 `tests/test_env_manager_install.py` 的 `TestInstallLogging` 类中追加方法：

```python
    def test_install_python_logs_stages(self, tmp_path, caplog):
        """install_embedded_python 各阶段（安装开始/下载源/解压/pip自检）应有日志"""
        import logging
        from vibeocr.env_manager import install_embedded_python

        with (
            patch("vibeocr.env_manager.get_environment_mode", return_value="none"),
            patch("vibeocr.env_manager.download_file_with_progress") as mock_dl,
            patch("tarfile.open", wraps=tarfile.open),
            patch(
                "vibeocr.env_manager.get_embedded_python_executable",
                return_value=tmp_path / "python" / "python.exe",
            ),
            patch("vibeocr.env_manager.subprocess.run") as mock_run,
        ):
            # 让下载写一个最小 tar.gz
            def _fake_dl(url, dest, *a, **kw):
                dest.write_bytes(self._make_standalone_tar_bytes())
                return True

            mock_dl.side_effect = _fake_dl
            mock_run.return_value = MagicMock(returncode=0, stdout="pip 25.0", stderr="")

            with caplog.at_level(logging.INFO, logger="vibeocr.env_manager"):
                ok, _msg = install_embedded_python(tmp_path)

        assert ok
        all_msgs = " ".join(r.message for r in caplog.records)
        assert "安装 Python 运行时" in all_msgs, "应记录安装开始"
        assert "尝试下载源" in all_msgs, "应记录下载源尝试"
        assert "解压完成" in all_msgs, "应记录解压完成"
        assert "pip 可用" in all_msgs, "应记录 pip 自检结果"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_env_manager_install.py::TestInstallLogging::test_install_python_logs_stages -xvs`
Expected: FAIL（当前 print，caplog 捕获不到）

- [ ] **Step 3: 实现 — 替换 install_embedded_python 内所有 print**

在 `src/vibeocr/env_manager.py`，逐处替换（注意行号会因 Task 1 改动略移，按文本匹配）：

替换（L361-363，安装开始横幅）：
```python
    print("\n" + "=" * 50)
    print("[环境安装] 安装 Python 运行时（python-build-standalone）")
    print("=" * 50)
```
为：
```python
    logger.info("==================================================")
    logger.info("[环境安装] 安装 Python 运行时（python-build-standalone）")
    logger.info("==================================================")
```

替换（L386，下载源尝试）：
```python
            print(f"[环境安装] 尝试下载源 {i}/{len(urls)}: {label}")
```
为：
```python
            logger.info("[环境安装] 尝试下载源 %d/%d: %s", i, len(urls), label)
```

替换（L402，解压开始）：
```python
        print(f"[环境安装] 下载完成，正在解压 {PYTHON_BUILD_STANDALONE_ASSET}...")
```
为：
```python
        logger.info("[环境安装] 下载完成，正在解压 %s...", PYTHON_BUILD_STANDALONE_ASSET)
```

替换（L436，解压完成）：
```python
            print("[环境安装] 解压完成")
```
为：
```python
            logger.info("[环境安装] 解压完成")
```

替换（L439-442，解压失败——注意 `import shutil` 要移到文件顶部，见下方说明）：
```python        except Exception as e:
            # 解压失败时清理半成品目录，避免误判为已安装
            import shutil

            shutil.rmtree(python_dir, ignore_errors=True)
            return False, f"解压失败: {e}"
```
为：
```python
        except Exception as e:
            # 解压失败时清理半成品目录，避免误判为已安装
            shutil.rmtree(python_dir, ignore_errors=True)
            logger.error("[环境安装] 解压失败: %s", e)
            return False, f"解压失败: {e}"
```

**同时**在文件顶部 import 区（`import os` 附近）加：
```python
import shutil
```

替换（L460-467，pip 自检三处）：
```python
        if result.returncode == 0:
            print(f"[环境安装] pip 可用: {result.stdout.strip()}")
        else:
            print(
                f"[环境安装] 警告: pip 自检失败: {result.stderr[-200:] if result.stderr else ''}"
            )
    except Exception as e:
        print(f"[环境安装] 警告: pip 自检异常: {e}")
```
为：
```python
        if result.returncode == 0:
            logger.info("[环境安装] pip 可用: %s", result.stdout.strip())
        else:
            logger.warning(
                "[环境安装] pip 自检失败: %s",
                result.stderr[-200:] if result.stderr else "",
            )
    except Exception as e:
        logger.warning("[环境安装] pip 自检异常: %s", e)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/test_env_manager_install.py::TestInstallLogging -xvs`
Expected: PASS（两个测试都过）

- [ ] **Step 5: 回归 — 运行全部 env_manager 安装测试**

Run: `python -m pytest tests/test_env_manager_install.py -x -q`
Expected: PASS（无回归）

- [ ] **Step 6: 提交**

```bash
git add src/vibeocr/env_manager.py tests/test_env_manager_install.py
git commit -m "refactor(env): install_embedded_python 改用 logging 落盘日志"
```

---

## Task 3: install_embedded_dependencies + switch_paddle_backend 的 report 闭包改 logging

**Files:**
- Modify: `src/vibeocr/env_manager.py:865-868` (`install_embedded_dependencies` 的 report)
- Modify: `src/vibeocr/env_manager.py:1063-1066` (`switch_paddle_backend` 的 report)
- Test: `tests/test_env_manager_install.py` (扩展 TestInstallLogging)

- [ ] **Step 1: 写失败测试 — 验证依赖安装/后端切换的 report 走 logging**

在 `tests/test_env_manager_install.py` 的 `TestInstallLogging` 类中追加：

```python
    def test_install_deps_logs_report(self, tmp_path, caplog):
        """install_embedded_dependencies 的 report 应通过 logger.info 输出"""
        import logging
        from vibeocr.env_manager import install_embedded_dependencies

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            return r

        with (
            patch("vibeocr.env_manager.get_pip_source", return_value="https://pypi.org/simple"),
            patch("vibeocr.env_manager.get_embedded_python_executable", return_value=python_exe),
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
        ):
            with caplog.at_level(logging.INFO, logger="vibeocr.env_manager"):
                ok, _msg = install_embedded_dependencies(
                    tmp_path, progress_callback=lambda s, m: None
                )

        assert ok
        info_msgs = " ".join(r.message for r in caplog.records)
        assert "开始安装OCR依赖" in info_msgs, "应记录安装开始"
        assert "pip源" in info_msgs, "应记录 pip 源"

    def test_switch_backend_logs_report(self, tmp_path, caplog):
        """switch_paddle_backend 的 report 应通过 logger.info 输出"""
        import logging
        from vibeocr.env_manager import switch_paddle_backend

        python_exe = tmp_path / "python.exe"
        python_exe.touch()

        def mock_run(cmd, **kw):
            r = MagicMock()
            r.returncode = 0
            r.stderr = ""
            r.stdout = ""
            return r

        with (
            patch("vibeocr.env_manager.get_pip_source", return_value="https://pypi.org/simple"),
            patch("vibeocr.env_manager.get_embedded_python_executable", return_value=python_exe),
            patch("vibeocr.env_manager.subprocess.run", side_effect=mock_run),
            patch("vibeocr.env_manager.detect_gpu", return_value=(True, "cu126")),
            patch("vibeocr.env_manager.update_cache_field", return_value=True),
        ):
            with caplog.at_level(logging.INFO, logger="vibeocr.env_manager"):
                ok, _msg = switch_paddle_backend(
                    tmp_path, "cpu", progress_callback=lambda s, m: None
                )

        assert ok
        info_msgs = " ".join(r.message for r in caplog.records)
        assert "开始切换到 CPU" in info_msgs, "应记录切换开始"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_env_manager_install.py::TestInstallLogging::test_install_deps_logs_report tests/test_env_manager_install.py::TestInstallLogging::test_switch_backend_logs_report -xvs`
Expected: FAIL（当前 report 用 print）

- [ ] **Step 3: 实现 — 改两个 report 闭包**

在 `src/vibeocr/env_manager.py`，替换 `install_embedded_dependencies` 的 report（约 L865-868）：
```python
    def report(stage: str, msg: str):
        print(f"[{stage}] {msg}")
        if progress_callback:
            progress_callback(stage, msg)
```
为：
```python
    def report(stage: str, msg: str):
        logger.info("[%s] %s", stage, msg)
        if progress_callback:
            progress_callback(stage, msg)
```

替换 `switch_paddle_backend` 的 report（约 L1063-1066）为完全相同的实现：
```python
    def report(stage: str, msg: str):
        logger.info("[%s] %s", stage, msg)
        if progress_callback:
            progress_callback(stage, msg)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/test_env_manager_install.py::TestInstallLogging -xvs`
Expected: PASS（四个测试都过）

- [ ] **Step 5: 回归 — 全部 env_manager 测试**

Run: `python -m pytest tests/test_env_manager_install.py -x -q`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add src/vibeocr/env_manager.py tests/test_env_manager_install.py
git commit -m "refactor(env): 依赖安装/后端切换 report 闭包改用 logging"
```

---

## Task 4: 新增 reinstall_embedded_python 函数

**Files:**
- Modify: `src/vibeocr/env_manager.py` (在 `install_embedded_python` 函数后，约 L470 后新增)
- Test: `tests/test_env_manager_install.py` (新增 TestReinstallPython 类)

- [ ] **Step 1: 写失败测试**

在 `tests/test_env_manager_install.py` 末尾追加：

```python
class TestReinstallPython:
    """reinstall_embedded_python：强制删除 python/ 后重装"""

    def test_deletes_python_dir_then_installs(self, tmp_path):
        """应先 rmtree(python/) 再调 install_embedded_python"""
        from vibeocr.env_manager import reinstall_embedded_python

        python_dir = tmp_path / "python"
        python_dir.mkdir()
        (python_dir / "python.exe").write_bytes(b"old")

        call_order = []

        def fake_rmtree(path, *a, **kw):
            call_order.append(("rmtree", str(path)))

        def fake_install(project_root, network_type="domestic", progress_callback=None):
            call_order.append(("install", str(project_root)))
            return True, "ok"

        with (
            patch("vibeocr.env_manager.shutil.rmtree", side_effect=fake_rmtree),
            patch(
                "vibeocr.env_manager.install_embedded_python", side_effect=fake_install
            ),
        ):
            ok, msg = reinstall_embedded_python(tmp_path)

        assert ok
        # 先删后装
        assert call_order[0][0] == "rmtree", "应先删除 python/"
        assert "python" in call_order[0][1], "应删除 python/ 目录"
        assert call_order[1][0] == "install", "删除后应调用安装"

    def test_rmtree_ignores_errors_when_dir_missing(self, tmp_path):
        """python/ 不存在时 rmtree(ignore_errors=True) 不报错，继续安装"""
        from vibeocr.env_manager import reinstall_embedded_python

        with (
            patch("vibeocr.env_manager.shutil.rmtree") as mock_rmtree,
            patch(
                "vibeocr.env_manager.install_embedded_python",
                return_value=(True, "ok"),
            ),
        ):
            ok, _msg = reinstall_embedded_python(tmp_path)

        assert ok
        mock_rmtree.assert_called_once()
        # 应以 ignore_errors=True 调用
        assert mock_rmtree.call_args.kwargs.get("ignore_errors") is True

    def test_progress_callback_receives_cleanup_stage(self, tmp_path):
        """progress_callback 应收到'清理'阶段"""
        from vibeocr.env_manager import reinstall_embedded_python

        stages = []
        with (
            patch("vibeocr.env_manager.shutil.rmtree"),
            patch(
                "vibeocr.env_manager.install_embedded_python",
                return_value=(True, "ok"),
            ),
        ):
            ok, _msg = reinstall_embedded_python(
                tmp_path, progress_callback=lambda s, m: stages.append((s, m))
            )

        assert ok
        cleanup_stages = [s for s in stages if "清理" in s[1] or "清理" in s[0]]
        assert len(cleanup_stages) > 0, f"应收到清理阶段回调，实际: {stages}"

    def test_returns_false_when_install_fails(self, tmp_path):
        """install_embedded_python 失败时应返回 False"""
        from vibeocr.env_manager import reinstall_embedded_python

        with (
            patch("vibeocr.env_manager.shutil.rmtree"),
            patch(
                "vibeocr.env_manager.install_embedded_python",
                return_value=(False, "下载失败"),
            ),
        ):
            ok, msg = reinstall_embedded_python(tmp_path)

        assert not ok
        assert "下载失败" in msg
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/test_env_manager_install.py::TestReinstallPython -xvs`
Expected: FAIL（`ImportError: cannot import name 'reinstall_embedded_python'`）

- [ ] **Step 3: 实现 — 新增 reinstall_embedded_python**

在 `src/vibeocr/env_manager.py` 的 `install_embedded_python` 函数结束后（`return True, f"Python 运行时安装成功: {python_exe}"` 那行之后，`check_current_environment_dependencies` 之前）插入：

```python
def reinstall_embedded_python(
    project_root: Path,
    network_type: Literal["domestic", "international"] = "domestic",
    progress_callback: Callable[[str, str], None] | None = None,
) -> tuple[bool, str]:
    """强制删除现有 python/ 目录后重新安装 Python 运行时。

    删除范围：仅 project_root/python/ 整个目录。
    不删除：.venv、config/、resources/、logs/、模型缓存、机器检测缓存。
    删除 python/ 后 OCR 依赖随之消失，调用方应在成功后继续装依赖。

    Args:
        project_root: 项目根目录
        network_type: 网络类型
        progress_callback: 进度回调 (stage, message)

    Returns:
        (是否成功, 消息)
    """
    python_dir = project_root / "python"

    def report(stage: str, msg: str):
        logger.info("[%s] %s", stage, msg)
        if progress_callback:
            progress_callback(stage, msg)

    report("环境安装", f"正在清理旧目录: {python_dir}（仅删除 python/，不影响配置/缓存/日志）")
    shutil.rmtree(python_dir, ignore_errors=True)

    report("环境安装", "清理完成，开始重新安装 Python 运行时...")
    return install_embedded_python(project_root, network_type)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/test_env_manager_install.py::TestReinstallPython -xvs`
Expected: PASS（四个测试都过）

- [ ] **Step 5: 提交**

```bash
git add src/vibeocr/env_manager.py tests/test_env_manager_install.py
git commit -m "feat(env): 新增 reinstall_embedded_python 强制删除后重装"
```

---

## Task 5: InstallWorker 加 reinstall_python 参数 + 日志镜像

**Files:**
- Modify: `src/vibeocr/widgets/install_dialog.py:19-78` (InstallWorker)
- Test: `tests/widgets/test_install_worker_reinstall.py` (新建)

- [ ] **Step 1: 写失败测试**

新建 `tests/widgets/test_install_worker_reinstall.py`：

```python
"""InstallWorker 的 reinstall_python 参数测试"""

import logging
from unittest.mock import MagicMock, patch

from vibeocr.widgets.install_dialog import InstallWorker


def test_reinstall_python_calls_reinstall_then_install(qtbot, tmp_path):
    """reinstall_python=True 应先调 reinstall_embedded_python 再调 install_embedded_dependencies"""
    worker = InstallWorker(tmp_path, reinstall_python=True)

    call_order = []

    with (
        patch("vibeocr.widgets.install_dialog.NetworkDetector") as mock_nd,
        patch("vibeocr.widgets.install_dialog.env_manager") as mock_em,
        patch("vibeocr.widgets.install_dialog.logger") as mock_logger,
    ):
        mock_nd.return_value.network_type = "domestic"
        mock_em.reinstall_embedded_python.side_effect = lambda *a, **kw: (
            call_order.append("reinstall"),
            (True, "ok"),
        )[1]
        mock_em.install_embedded_dependencies.return_value = (True, "ok")

        with qtbot.waitSignal(worker.finished, timeout=5000):
            worker.start()

    assert call_order == ["reinstall"], "应先调 reinstall_embedded_python"
    mock_em.install_embedded_dependencies.assert_called_once()
    # reinstall 成功后才装依赖
    assert mock_em.method_calls[0][0] == "reinstall_embedded_python"
    assert any(
        m[0] == "install_embedded_dependencies" for m in mock_em.method_calls
    )


def test_reinstall_python_aborts_when_reinstall_fails(qtbot, tmp_path):
    """reinstall_python=True 但 reinstall 失败时应终止，不装依赖"""
    worker = InstallWorker(tmp_path, reinstall_python=True)

    with (
        patch("vibeocr.widgets.install_dialog.NetworkDetector") as mock_nd,
        patch("vibeocr.widgets.install_dialog.env_manager") as mock_em,
    ):
        mock_nd.return_value.network_type = "domestic"
        mock_em.reinstall_embedded_python.return_value = (False, "下载失败")

        with qtbot.waitSignal(worker.finished, timeout=5000) as blocker:
            worker.start()

    ok, msg = blocker.args
    assert not ok
    assert "下载失败" in msg
    mock_em.install_embedded_dependencies.assert_not_called()


def test_progress_signal_also_logged(qtbot, tmp_path, caplog):
    """progress 信号触发时应同时 logger.info 一份（确保 UI 进度落盘）"""
    worker = InstallWorker(tmp_path, force_backend="cpu")

    with (
        patch("vibeocr.widgets.install_dialog.NetworkDetector") as mock_nd,
        patch("vibeocr.widgets.install_dialog.env_manager") as mock_em,
    ):
        mock_nd.return_value.network_type = "domestic"
        mock_em.get_embedded_python_executable.return_value = tmp_path / "python.exe"
        (tmp_path / "python.exe").touch()
        mock_em.install_embedded_dependencies.return_value = (True, "ok")

        with caplog.at_level(logging.INFO, logger="vibeocr.widgets.install_dialog"):
            with qtbot.waitSignal(worker.finished, timeout=5000):
                worker.start()

    info_msgs = " ".join(r.message for r in caplog.records)
    assert "依赖安装" in info_msgs or "安装" in info_msgs, (
        "progress 回调应同时写入 logger，便于落盘"
    )
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/widgets/test_install_worker_reinstall.py -xvs`
Expected: FAIL（`InstallWorker.__init__() got an unexpected keyword argument 'reinstall_python'`）

- [ ] **Step 3: 实现 — InstallWorker 加参数 + logger + 进度镜像**

在 `src/vibeocr/widgets/install_dialog.py` 顶部 import 区加：
```python
import logging
```
在 import 后加：
```python
logger = logging.getLogger(__name__)
```

替换 `InstallWorker.__init__` 和 `run`（L19-78）为：

```python
class InstallWorker(QThread):
    """安装工作线程"""

    progress = Signal(str, str)  # (stage, message)
    finished = Signal(bool, str)  # (success, message)

    def __init__(
        self,
        project_root: Path,
        force_backend: str | None = None,
        reinstall_python: bool = False,
    ) -> None:
        super().__init__()
        self._project_root = project_root
        self._force_backend = force_backend
        self._reinstall_python = reinstall_python

    def run(self) -> None:
        """执行安装"""
        try:
            # 1. 检测网络环境
            self.progress.emit("网络检测", "正在检测网络环境...")
            detector = NetworkDetector(self._project_root)
            network_type = detector.network_type

            # 2. 决定后端：force_backend 指定时跳过自动检测
            if self._force_backend:
                has_gpu = self._force_backend == "gpu"
                cuda_version = None
                if has_gpu:
                    # GPU 需要 cuda_version（cu-tag）选 paddle index
                    self.progress.emit("硬件检测", "正在检测 GPU CUDA 版本...")
                    _detected, cuda_version = env_manager.detect_gpu()
            else:
                self.progress.emit("硬件检测", "正在检测GPU...")
                has_gpu, cuda_version = env_manager.detect_gpu()

            # 3. Python 运行时
            if self._reinstall_python:
                # 重装模式：强制删除 python/ 后重下（连带丢失依赖，后续装依赖）
                self.progress.emit(
                    "环境安装", "正在重装 Python 运行时（删除 python/ 后重新下载）..."
                )
                success, msg = env_manager.reinstall_embedded_python(
                    self._project_root,
                    network_type,
                    progress_callback=lambda stage, message: self.progress.emit(
                        stage, message
                    ),
                )
                if not success:
                    self.finished.emit(False, f"重装 Python 运行时失败:\n{msg}")
                    return
            else:
                # 常规模式：检查嵌入式Python是否存在，不存在才装
                python_exe = env_manager.get_embedded_python_executable(
                    self._project_root
                )
                if not python_exe.exists():
                    self.progress.emit("环境安装", "正在安装嵌入式Python...")
                    success, msg = env_manager.install_embedded_python(
                        self._project_root, network_type
                    )
                    if not success:
                        self.finished.emit(False, f"安装嵌入式Python失败:\n{msg}")
                        return

            # 4. 安装OCR依赖
            self.progress.emit("依赖安装", "正在安装OCR依赖...")
            success, msg = env_manager.install_embedded_dependencies(
                self._project_root,
                network_type,
                has_gpu,
                cuda_version,
                progress_callback=lambda stage, message: self.progress.emit(
                    stage, message
                ),
                force_backend=self._force_backend,
            )

            self.finished.emit(success, msg)

        except Exception as e:
            logger.error("安装异常: %s", e)
            self.finished.emit(False, f"安装异常: {e}")
```

然后在 `_on_progress` 槽（`InstallDialog` 类内，约 L139-143）中加日志镜像。替换：

```python
    @Slot(str, str)
    def _on_progress(self, stage: str, message: str) -> None:
        """进度更新"""
        self._stage_label.setText(f"[{stage}] {message}")
        self._log(f"[{stage}] {message}")
```
为：
```python
    @Slot(str, str)
    def _on_progress(self, stage: str, message: str) -> None:
        """进度更新（同时写入 logger，确保 UI 进度落盘到 vibeocr.log）"""
        logger.info("[%s] %s", stage, message)
        self._stage_label.setText(f"[{stage}] {message}")
        self._log(f"[{stage}] {message}")
```

- [ ] **Step 4: 运行新测试验证通过**

Run: `python -m pytest tests/widgets/test_install_worker_reinstall.py -xvs`
Expected: PASS（三个测试都过）

- [ ] **Step 5: 回归 — 原 force_backend 测试不破坏**

Run: `python -m pytest tests/widgets/test_install_worker_force_backend.py -xvs`
Expected: PASS（向后兼容）

- [ ] **Step 6: 提交**

```bash
git add src/vibeocr/widgets/install_dialog.py tests/widgets/test_install_worker_reinstall.py
git commit -m "feat(install): InstallWorker 加 reinstall_python + 进度日志镜像"
```

---

## Task 6: BackendChoiceDialog 透传 reinstall_python

**Files:**
- Modify: `src/vibeocr/widgets/backend_choice_dialog.py:38-44, 112-127` (构造函数 + _on_install_clicked)
- Test: `tests/widgets/test_backend_choice_dialog.py` (扩展)

- [ ] **Step 1: 写失败测试**

先查看现有测试文件结构：`head -20 tests/widgets/test_backend_choice_dialog.py`。然后在 `tests/widgets/test_backend_choice_dialog.py` 末尾追加：

```python
def test_reinstall_python_passed_to_worker(qtbot, tmp_path):
    """reinstall_python=True 应透传给 InstallWorker"""
    from unittest.mock import patch

    from vibeocr.widgets.backend_choice_dialog import BackendChoiceDialog

    captured = {}

    class FakeWorker:
        def __init__(self, project_root, force_backend=None, reinstall_python=False):
            captured["force_backend"] = force_backend
            captured["reinstall_python"] = reinstall_python

        progress = None  # 信号占位（不实际 connect）
        finished = None
        def start(self): pass

    with patch("vibeocr.widgets.backend_choice_dialog.env_manager") as mock_em:
        mock_em.detect_gpu.return_value = (False, None)  # CPU 模式，避免 GPU 检测
        with patch(
            "vibeocr.widgets.backend_choice_dialog.InstallWorker", FakeWorker
        ):
            dialog = BackendChoiceDialog(tmp_path, reinstall_python=True)
            qtbot.addWidget(dialog)
            dialog._on_install_clicked()

    assert captured.get("reinstall_python") is True, (
        "reinstall_python 应透传给 InstallWorker"
    )
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/widgets/test_backend_choice_dialog.py::test_reinstall_python_passed_to_worker -xvs`
Expected: FAIL（`BackendChoiceDialog.__init__() got an unexpected keyword argument 'reinstall_python'`）

- [ ] **Step 3: 实现 — 透传参数**

在 `src/vibeocr/widgets/backend_choice_dialog.py`，替换 `__init__`（L38-44）：
```python
    def __init__(self, project_root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._worker: InstallWorker | None = None
        self._has_gpu = False
        self._setup_ui()
        self._detect_and_set_default()
```
为：
```python
    def __init__(
        self,
        project_root: Path,
        parent: QWidget | None = None,
        reinstall_python: bool = False,
    ) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._worker: InstallWorker | None = None
        self._has_gpu = False
        self._reinstall_python = reinstall_python
        self._setup_ui()
        self._detect_and_set_default()
```

替换 `_on_install_clicked` 中创建 worker 的那行（约 L124）：
```python
        self._worker = InstallWorker(self._project_root, force_backend=backend)
```
为：
```python
        self._worker = InstallWorker(
            self._project_root,
            force_backend=backend,
            reinstall_python=self._reinstall_python,
        )
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/widgets/test_backend_choice_dialog.py::test_reinstall_python_passed_to_worker -xvs`
Expected: PASS

- [ ] **Step 5: 回归 — backend_choice_dialog 全部测试**

Run: `python -m pytest tests/widgets/test_backend_choice_dialog.py -xvs`
Expected: PASS（向后兼容，默认 reinstall_python=False）

- [ ] **Step 6: 提交**

```bash
git add src/vibeocr/widgets/backend_choice_dialog.py tests/widgets/test_backend_choice_dialog.py
git commit -m "feat(dialog): BackendChoiceDialog 透传 reinstall_python"
```

---

## Task 7: main_window.ui 加"环境维护"分组

**Files:**
- Modify: `src/vibeocr/ui/main_window.ui` (pageAppSettings 内，groupAppSettings 后)
- Modify: `src/vibeocr/ui/ui_main_window.py` (同步生成代码)

- [ ] **Step 1: 改 .ui XML — 在 groupAppSettings 后插入新分组**

在 `src/vibeocr/ui/main_window.ui`，找到 `groupAppSettings` 结束（`</widget>` 后跟 `<item>` 包裹 `spacerAppPage`）。即这段：

```xml
             </layout>
            </widget>
           </item>
           <item>
            <spacer name="spacerAppPage">
```

在 `</widget>`（groupAppSettings 结束）所在的 `</item>` 之后、`spacerAppPage` 的 `<item>` 之前，插入：

```xml
           <item>
            <widget class="QGroupBox" name="groupEnvMaintenance">
             <property name="title">
              <string>环境维护</string>
             </property>
             <layout class="QVBoxLayout" name="envMaintenanceLayout">
              <property name="spacing">
               <number>8</number>
              </property>
              <item>
               <widget class="QLabel" name="labelEnvStatus">
                <property name="text">
                 <string>Python 运行时：检测中...</string>
                </property>
                <property name="wordWrap">
                 <bool>true</bool>
                </property>
               </widget>
              </item>
              <item>
               <widget class="QPushButton" name="btnReinstallPython">
                <property name="toolTip">
                 <string>删除 python/ 目录后重新下载安装 Python 运行时及 OCR 依赖。仅删除 python/，不影响配置、模型缓存和日志。</string>
                </property>
                <property name="text">
                 <string>重装 Python 运行时</string>
                </property>
               </widget>
              </item>
              <item>
               <widget class="QPushButton" name="btnReinstallDeps">
                <property name="toolTip">
                 <string>使用 pip 重新安装 paddle/torch/mineru 等 OCR 依赖，不删除任何目录。</string>
                </property>
                <property name="text">
                 <string>重装 OCR 依赖</string>
                </property>
               </widget>
              </item>
             </layout>
            </widget>
           </item>
```

- [ ] **Step 2: 同步 ui_main_window.py — build 区插入控件**

在 `src/vibeocr/ui/ui_main_window.py`，找到 `self.pageAppLayout.addWidget(self.groupAppSettings)`（约 L360）。在它之后、`self.spacerAppPage = QSpacerItem(` 之前，插入：

```python
        self.groupEnvMaintenance = QGroupBox(self.pageAppSettings)
        self.groupEnvMaintenance.setObjectName("groupEnvMaintenance")
        self.envMaintenanceLayout = QVBoxLayout(self.groupEnvMaintenance)
        self.envMaintenanceLayout.setSpacing(8)
        self.envMaintenanceLayout.setObjectName("envMaintenanceLayout")
        self.labelEnvStatus = QLabel(self.groupEnvMaintenance)
        self.labelEnvStatus.setObjectName("labelEnvStatus")
        self.labelEnvStatus.setWordWrap(True)

        self.envMaintenanceLayout.addWidget(self.labelEnvStatus)

        self.btnReinstallPython = QPushButton(self.groupEnvMaintenance)
        self.btnReinstallPython.setObjectName("btnReinstallPython")

        self.envMaintenanceLayout.addWidget(self.btnReinstallPython)

        self.btnReinstallDeps = QPushButton(self.groupEnvMaintenance)
        self.btnReinstallDeps.setObjectName("btnReinstallDeps")

        self.envMaintenanceLayout.addWidget(self.btnReinstallDeps)

        self.pageAppLayout.addWidget(self.groupEnvMaintenance)
```

**确认** `QGroupBox`、`QLabel`、`QPushButton` 已在文件顶部 import（检查 `from PySide6.QtWidgets import ...`，若缺则补）。

- [ ] **Step 3: 同步 ui_main_window.py — retranslateUi 区插入文案**

在 `retranslateUi` 方法中，找到 `self.chkAutoStart.setText(...)` 块之后（约 L663 后），插入：

```python
        self.groupEnvMaintenance.setTitle(
            QCoreApplication.translate("MainWindowWidget", "\u73af\u5883\u7ef4\u62a4", None)
        )
        self.labelEnvStatus.setText(
            QCoreApplication.translate(
                "MainWindowWidget",
                "Python \u8fd0\u884c\u65f6\uff1a\u68c0\u6d4b\u4e2d...",
                None,
            )
        )
        # if QT_CONFIG(tooltip)
        self.btnReinstallPython.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u5220\u9664 python/ \u76ee\u5f55\u540e\u91cd\u65b0\u4e0b\u8f7d\u5b89\u88c5"
                " Python \u8fd0\u884c\u65f6\u53ca OCR \u4f9d\u8d56\u3002"
                "\u4ec5\u5220\u9664 python/\uff0c\u4e0d\u5f71\u54cd\u914d\u7f6e\u3001\u6a21\u578b\u7f13\u5b58\u548c\u65e5\u5fd7\u3002",
                None,
            )
        )
        # endif // QT_CONFIG(tooltip)
        self.btnReinstallPython.setText(
            QCoreApplication.translate(
                "MainWindowWidget", "\u91cd\u88c5 Python \u8fd0\u884c\u65f6", None
            )
        )
        # if QT_CONFIG(tooltip)
        self.btnReinstallDeps.setToolTip(
            QCoreApplication.translate(
                "MainWindowWidget",
                "\u4f7f\u7528 pip \u91cd\u65b0\u5b89\u88c5 paddle/torch/mineru \u7b49"
                " OCR \u4f9d\u8d56\uff0c\u4e0d\u5220\u9664\u4efb\u4f55\u76ee\u5f55\u3002",
                None,
            )
        )
        # endif // QT_CONFIG(tooltip)
        self.btnReinstallDeps.setText(
            QCoreApplication.translate(
                "MainWindowWidget", "\u91cd\u88c5 OCR \u4f9d\u8d56", None
            )
        )
```

（这些 `\uXXXX` 转义对应中文字符串，与 uic 生成的风格一致。）

- [ ] **Step 4: 冒烟测试 — 应用能加载 UI**

Run: `python -c "from vibeocr.ui.ui_main_window import Ui_MainWindowWidget; Ui_MainWindowWidget()"`
Expected: 无异常（说明 UI 控件定义无语法错误）

如果类名不是 `Ui_MainWindowWidget`，先查：`grep "^class " src/vibeocr/ui/ui_main_window.py`

- [ ] **Step 5: 提交**

```bash
git add src/vibeocr/ui/main_window.ui src/vibeocr/ui/ui_main_window.py
git commit -m "feat(ui): 应用设置页新增'环境维护'分组（重装按钮）"
```

---

## Task 8: settings_page_controller 连接重装按钮

**Files:**
- Modify: `src/vibeocr/views/settings_page_controller.py` (connect_signals 加连接 + 新增方法)
- Test: `tests/views/test_settings_reinstall.py` (新建)

- [ ] **Step 1: 写失败测试**

新建 `tests/views/test_settings_reinstall.py`：

```python
"""设置页重装入口测试"""

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtWidgets import QWidget

from vibeocr.ui.ui_main_window import Ui_MainWindowWidget
from vibeocr.views.settings_page_controller import SettingsPageController


@pytest.fixture
def controller(qtbot, tmp_path):
    """构造带真实 UI 的 SettingsPageController

    connect_signals 会触发 _init_backend_options / _init_settings_page，
    这些会访问 ConfigManager、machine_cache、pipelines、BackendOptionsWidget。
    为保证测试隔离，patch 掉这些重依赖。
    """
    host = QWidget()
    qtbot.addWidget(host)
    ui = Ui_MainWindowWidget()
    ui.setupUi(host)

    with (
        # BackendOptionsWidget 构造读 env_manager / machine_cache
        patch("vibeocr.widgets.backend_options_widget.env_manager") as mock_em,
        patch(
            "vibeocr.widgets.backend_options_widget.is_cache_valid",
            return_value=(False, None),
        ),
        # _init_settings_page 读 ConfigManager / machine_cache / pipelines
        patch(
            "vibeocr.views.settings_page_controller.is_cache_valid",
            return_value=(False, None),
        ),
        patch("vibeocr.managers.config_manager.ConfigManager") as mock_cm,
        patch(
            "vibeocr.core.pipelines.get_preloadable_pipelines",
            return_value=[],
        ),
    ):
        mock_em.detect_gpu.return_value = (False, None)
        mock_cm.instance.return_value = MagicMock()

        ctrl = SettingsPageController(
            ui=host,
            project_root=tmp_path,
            status_callback=lambda msg: None,
            ocr_ready_callback=lambda: True,
            subprocess_manager=MagicMock(),
        )
        ctrl.connect_signals()
    return ctrl, host


def test_reinstall_python_button_exists(controller):
    """重装 Python 按钮应在 UI 中可找到"""
    ctrl, host = controller
    from PySide6.QtWidgets import QPushButton

    btn = host.findChild(QPushButton, "btnReinstallPython")
    assert btn is not None, "btnReinstallPython 应存在"


def test_click_reinstall_python_confirms_then_opens_dialog(controller, monkeypatch):
    """点重装 Python：确认 Yes 后应弹 BackendChoiceDialog(reinstall_python=True)"""
    ctrl, host = controller
    from PySide6.QtWidgets import QMessageBox, QPushButton

    # 模拟用户点"是"
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes
    )
    # mock BackendChoiceDialog 避免真弹窗
    instances = []

    class FakeDialog:
        def __init__(self, *args, **kwargs):
            instances.append(kwargs)
            self.reinstall_python = kwargs.get("reinstall_python", False)

        def exec(self):
            return 1

        finished = MagicMock()
        install_succeeded = MagicMock()

    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.BackendChoiceDialog", FakeDialog
    )

    btn = host.findChild(QPushButton, "btnReinstallPython")
    btn.click()

    assert len(instances) == 1, "应弹出一次对话框"
    assert instances[0].get("reinstall_python") is True


def test_click_reinstall_python_cancel_does_nothing(controller, monkeypatch):
    """点重装 Python：确认 No 后不应弹对话框"""
    ctrl, host = controller
    from PySide6.QtWidgets import QMessageBox, QPushButton

    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.No
    )
    opened = []
    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.BackendChoiceDialog",
        lambda *a, **kw: opened.append(kw),
    )

    btn = host.findChild(QPushButton, "btnReinstallPython")
    btn.click()

    assert len(opened) == 0, "取消时不应弹对话框"


def test_click_reinstall_deps_opens_dialog_without_reinstall(controller, monkeypatch):
    """点重装 OCR 依赖：应弹 BackendChoiceDialog(reinstall_python=False)"""
    ctrl, host = controller
    from PySide6.QtWidgets import QMessageBox, QPushButton

    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **kw: QMessageBox.StandardButton.Yes
    )
    instances = []

    class FakeDialog:
        def __init__(self, *args, **kwargs):
            instances.append(kwargs)

        def exec(self):
            return 1

        finished = MagicMock()
        install_succeeded = MagicMock()

    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.BackendChoiceDialog", FakeDialog
    )

    btn = host.findChild(QPushButton, "btnReinstallDeps")
    btn.click()

    assert len(instances) == 1
    assert instances[0].get("reinstall_python") is False


def test_buttons_disabled_in_non_portable_mode(controller, monkeypatch):
    """非 portable 模式（venv/none）时两按钮应禁用"""
    ctrl, host = controller
    from PySide6.QtWidgets import QPushButton

    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.get_environment_mode",
        lambda root: "venv",
    )
    ctrl._refresh_env_maintenance_state()

    btn_py = host.findChild(QPushButton, "btnReinstallPython")
    btn_deps = host.findChild(QPushButton, "btnReinstallDeps")
    assert not btn_py.isEnabled(), "venv 模式应禁用重装 Python"
    assert not btn_deps.isEnabled(), "venv 模式应禁用重装依赖"


def test_env_status_label_shows_python_info(controller, monkeypatch):
    """labelEnvStatus 应显示 Python 路径/就绪状态"""
    ctrl, host = controller
    from PySide6.QtWidgets import QLabel

    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.get_embedded_python_info",
        lambda root: {"path": "C:/app/python/python.exe", "mode": "portable", "ready": True},
    )
    monkeypatch.setattr(
        "vibeocr.views.settings_page_controller.get_environment_mode",
        lambda root: "portable",
    )
    ctrl._refresh_env_maintenance_state()

    label = host.findChild(QLabel, "labelEnvStatus")
    text = label.text()
    assert "python.exe" in text or "就绪" in text or "已安装" in text, (
        f"应显示 Python 状态，实际: {text}"
    )
```

- [ ] **Step 2: 运行测试验证失败**

Run: `python -m pytest tests/views/test_settings_reinstall.py -xvs`
Expected: FAIL（`AttributeError: '_on_reinstall_python'` 或按钮找不到槽）

- [ ] **Step 3: 实现 — 连接信号 + 方法**

在 `src/vibeocr/views/settings_page_controller.py`：

**(a) 顶部加 import**（在现有 import 块中，`from vibeocr.machine_cache import ...` 或类似位置加）：
```python
from vibeocr.env_manager import (
    get_embedded_python_info,
    get_environment_mode,
)
from vibeocr.widgets.backend_choice_dialog import BackendChoiceDialog
```

**(b) connect_signals 中加按钮连接**——在 `self._restore_pipeline_ttl_state()` 那行（约 L97）之前插入：
```python
        # --- 环境维护：重装 Python 运行时 / 重装 OCR 依赖 ---
        btn_reinstall_python = self._ui.findChild(QPushButton, "btnReinstallPython")
        if btn_reinstall_python:
            btn_reinstall_python.clicked.connect(self._on_reinstall_python)

        btn_reinstall_deps = self._ui.findChild(QPushButton, "btnReinstallDeps")
        if btn_reinstall_deps:
            btn_reinstall_deps.clicked.connect(self._on_reinstall_deps)

        self._refresh_env_maintenance_state()
```

**(c) 新增方法**——在 `_on_clear_cache_clicked` 方法之前（或 `_update_cache_status` 之后，类内任意合适位置）加：

```python
    def _on_reinstall_python(self) -> None:
        """重装 Python 运行时按钮：确认后弹 BackendChoiceDialog(reinstall_python=True)"""
        reply = QMessageBox.question(
            None,
            "确认重装 Python 运行时",
            "将删除 python/ 目录（含所有 OCR 依赖）后重新下载安装 Python 运行时。\n\n"
            "删除范围：仅 python/ 目录。\n"
            "不受影响：用户配置、模型缓存、日志、机器检测缓存。\n\n"
            "是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        dialog = BackendChoiceDialog(
            self._project_root, reinstall_python=True
        )
        dialog.finished.connect(lambda _r: self._refresh_env_maintenance_state())
        dialog.exec()

    def _on_reinstall_deps(self) -> None:
        """重装 OCR 依赖按钮：确认后弹 BackendChoiceDialog(reinstall_python=False)"""
        reply = QMessageBox.question(
            None,
            "确认重装 OCR 依赖",
            "将使用 pip 重新安装 OCR 依赖（paddle/torch/mineru）。\n\n"
            "此操作不删除任何文件，仅重装 pip 包。\n\n"
            "是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        dialog = BackendChoiceDialog(
            self._project_root, reinstall_python=False
        )
        dialog.finished.connect(lambda _r: self._refresh_env_maintenance_state())
        dialog.exec()

    def _refresh_env_maintenance_state(self) -> None:
        """刷新环境维护区状态：显示 Python 路径/就绪，非 portable 模式禁用按钮"""
        label = self._ui.findChild(QLabel, "labelEnvStatus")
        btn_py = self._ui.findChild(QPushButton, "btnReinstallPython")
        btn_deps = self._ui.findChild(QPushButton, "btnReinstallDeps")

        mode = get_environment_mode(self._project_root)
        info = get_embedded_python_info(self._project_root)

        if label:
            if mode == "portable":
                status = "已安装" if info.get("ready") else "未安装"
                label.setText(f"Python 运行时：{status}\n路径：{info.get('path', '未知')}")
            elif mode == "venv":
                label.setText("开发模式（.venv），请用 uv sync 管理环境")
            else:
                label.setText("Python 运行时：未安装")

        # 仅 portable 模式启用重装按钮（开发态 .venv 由 uv 管理）
        enabled = mode == "portable"
        if btn_py:
            btn_py.setEnabled(enabled)
        if btn_deps:
            btn_deps.setEnabled(enabled)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `python -m pytest tests/views/test_settings_reinstall.py -xvs`
Expected: PASS（六个测试都过）

- [ ] **Step 5: 回归 — settings_page_controller 相关测试**

Run: `python -m pytest tests/views/ tests/widgets/test_backend_options_widget.py -x -q`
Expected: PASS（无回归）

- [ ] **Step 6: 提交**

```bash
git add src/vibeocr/views/settings_page_controller.py tests/views/test_settings_reinstall.py
git commit -m "feat(settings): 应用设置页连接重装 Python/依赖按钮 + 状态刷新"
```

---

## Task 9: 全量回归 + ruff/pyright

**Files:** 无（验证任务）

- [ ] **Step 1: 运行全部相关测试**

Run: `python -m pytest tests/test_env_manager_install.py tests/widgets/test_install_worker_reinstall.py tests/widgets/test_install_worker_force_backend.py tests/widgets/test_backend_choice_dialog.py tests/views/test_settings_reinstall.py -v`
Expected: 全部 PASS

- [ ] **Step 2: ruff 检查改动文件**

Run: `python -m ruff check src/vibeocr/env_manager.py src/vibeocr/widgets/install_dialog.py src/vibeocr/widgets/backend_choice_dialog.py src/vibeocr/views/settings_page_controller.py src/vibeocr/ui/ui_main_window.py`
Expected: 无 error（warning 可接受但应尽量清零）

- [ ] **Step 3: pyright 检查改动文件**

Run: `python -m pyright src/vibeocr/env_manager.py src/vibeocr/widgets/install_dialog.py src/vibeocr/widgets/backend_choice_dialog.py src/vibeocr/views/settings_page_controller.py`
Expected: 无新增 error

- [ ] **Step 4: 手动冒烟（可选，需打包环境）**

若条件允许，打包后在 portable 模式下：
1. 打开设置 → 应用设置 → 看到"环境维护"分组
2. 点"重装 OCR 依赖" → 确认 → 弹后端选择 → 完成
3. 检查 `vibeocr.log` 是否有 `[依赖安装]` 等日志行

- [ ] **Step 5: 最终提交（如有 lint 修复）**

```bash
git add -A
git commit -m "chore: lint 清理（安装日志 + 重装入口）" || echo "无 lint 改动"
```

---

## 验收清单

对照设计文档逐项确认：

- [ ] **安装日志落盘**：`install_embedded_python` / `install_embedded_dependencies` / `switch_paddle_backend` / `download_file_with_progress` 全部走 `logger.info`，不再有 `print`（Task 1-3）
- [ ] **reinstall_embedded_python**：删 `python/` 后重装，删除范围仅 `python/`（Task 4）
- [ ] **InstallWorker.reinstall_python**：True 时走 reinstall→install deps，进度日志镜像（Task 5）
- [ ] **BackendChoiceDialog 透传**：reinstall_python 传到 worker（Task 6）
- [ ] **UI 分组**：应用设置页有"环境维护"分组 + 两按钮 + 状态 label，toolTip 说明删除范围（Task 7）
- [ ] **按钮逻辑**：确认对话框明确删除范围 → 弹 BackendChoiceDialog → 完成刷新状态；非 portable 模式禁用（Task 8）
- [ ] **测试**：env_manager logging 测试、reinstall 测试、worker 测试、dialog 测试、settings 测试全过（Task 9）

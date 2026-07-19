"""设置页"推理后端"组件测试"""

from unittest.mock import patch

import pytest
from PySide6.QtCore import QObject, Signal


class _StubGpuDetectWorker(QObject):
    """替代 _GpuDetectWorker 的桩：不启动真线程，start() 为空操作。

    信号 finished_info 与真 worker 同名，测试可显式 emit 触发回填，
    使断言不依赖 QThread 异步时序。
    """

    finished_info = Signal(dict)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

    def start(self):  # 真线程启动的空操作
        pass


    def isRunning(self):
        return False

    def quit(self):
        pass

    def wait(self, _timeout_ms):
        return True


class _RunningStubGpuDetectWorker(QObject):
    finished_info = Signal(dict)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.quit_called = False
        self.wait_calls: list[int] = []
        self._running = False
        self.cancel_called = False

    def start(self):
        self._running = True

    def isRunning(self):
        return self._running

    def quit(self):
        self.quit_called = True

    def cancel(self):
        self.cancel_called = True

    def wait(self, timeout_ms):
        self.wait_calls.append(timeout_ms)
        self._running = False
        return True


def _make_widget(
    tmp_path,
    has_gpu=True,
    cached_hardware_gpu=False,
    pending=None,
    worker_cls: type[_StubGpuDetectWorker] | type[_RunningStubGpuDetectWorker] = _StubGpuDetectWorker,
):
    """构造 BackendOptionsWidget（在 patch 作用域外也能用，patch 注入到模块）。

    返回 widget。由于 widget 的 _apply 在构造后才调用，patch 必须覆盖调用时刻。
    本函数把 update_cache_field 的 mock 存到 widget._mock_update 以便断言。

    GPU 探测由后台 _GpuDetectWorker 完成；测试用 _StubGpuDetectWorker 替换，
    构造后显式 emit finished_info 同步触发回填，避免依赖真线程时序。
    """
    from vibeocr.widgets import backend_options_widget as bow

    # 直接替换模块级引用（widget 内 from ... import 拿到的就是模块属性）
    orig_em = bow.env_manager
    orig_cache = bow.is_cache_valid
    orig_worker = bow._GpuDetectWorker

    mock_em = patch.object(bow, "env_manager").start()
    mock_cache = patch.object(bow, "is_cache_valid").start()
    mock_update = patch.object(bow, "update_cache_field").start()

    mock_em.detect_gpu.return_value = (has_gpu, "cu126") if has_gpu else (False, None)
    detect_info = {
        "has_gpu": has_gpu,
        "name": "NVIDIA GeForce RTX 4090" if has_gpu else "",
        "vram_mb": 24564 if has_gpu else 0,
        "cuda": "cu126" if has_gpu else None,
    }
    mock_em.detect_gpu_info.return_value = detect_info
    # resolve_use_gpu 决定"当前后端"展示值，须与实际推理（main_window 启动 worker）
    # 一致。此处用 cached_hardware_gpu/pending 推导期望值，模拟 resolve_use_gpu 逻辑。
    if pending == "gpu":
        resolved_gpu = True
    elif pending == "cpu":
        resolved_gpu = False
    else:
        resolved_gpu = cached_hardware_gpu
    mock_em.resolve_use_gpu.return_value = resolved_gpu
    mock_cache.return_value = (
        True,
        {
            "hardware_info": {"has_gpu": cached_hardware_gpu},
            "pending_backend": pending,
        },
    )
    mock_update.return_value = True

    # 用桩替换真 worker 类，构造时不会启动真线程
    bow._GpuDetectWorker = worker_cls

    try:
        widget = bow.BackendOptionsWidget(tmp_path)
        # 显式触发回填（模拟后台探测完成回调在主线程执行）
        assert widget._detect_worker is not None
        widget._detect_worker.finished_info.emit(detect_info)
    finally:
        # 恢复模块引用（构造已完成，状态已读入 widget 实例）
        patch.object(bow, "env_manager", orig_em).start()
        patch.object(bow, "is_cache_valid", orig_cache).start()
        bow._GpuDetectWorker = orig_worker
        # 注意：update_cache_field 保持 mock，因为 _apply 才调用
        bow.update_cache_field = mock_update
    widget._mock_update = mock_update
    return widget


@pytest.fixture
def _cleanup():
    yield
    patch.stopall()


def test_shows_current_backend_gpu(_cleanup, qtbot, tmp_path):
    """有 GPU 时应显示当前后端为 GPU，GPU 单选默认选中"""
    widget = _make_widget(tmp_path, has_gpu=True, cached_hardware_gpu=True)
    qtbot.addWidget(widget)
    assert widget.current_backend() == "gpu"
    assert widget._gpu_radio.isChecked()
    assert not widget._cpu_radio.isChecked()


def test_shows_current_backend_cpu_when_no_gpu(_cleanup, qtbot, tmp_path):
    """无 GPU 时 CPU 单选默认选中，GPU 禁用"""
    widget = _make_widget(tmp_path, has_gpu=False, cached_hardware_gpu=False)
    qtbot.addWidget(widget)
    assert widget.current_backend() == "cpu"
    assert widget._cpu_radio.isChecked()
    assert not widget._gpu_radio.isEnabled()


def test_shows_pending_status(_cleanup, qtbot, tmp_path):
    """pending_backend 存在时应显示待切换状态"""
    widget = _make_widget(
        tmp_path, has_gpu=True, cached_hardware_gpu=False, pending="gpu"
    )
    qtbot.addWidget(widget)
    assert (
        "待切换" in widget._status_label.text() or "重启" in widget._status_label.text()
    )


def test_apply_writes_pending_backend(_cleanup, qtbot, tmp_path):
    """点应用应写 pending_backend 到缓存"""
    widget = _make_widget(tmp_path, has_gpu=True, cached_hardware_gpu=True)
    qtbot.addWidget(widget)
    # 当前是 gpu，切到 cpu
    widget._cpu_radio.setChecked(True)
    widget._apply()
    widget._mock_update.assert_called_once()
    args = widget._mock_update.call_args[0]
    assert args[1] == "pending_backend"
    assert args[2] == "cpu"


def test_apply_disabled_when_already_pending_same(_cleanup, qtbot, tmp_path):
    """当前已是待切换目标时，应用按钮应禁用"""
    widget = _make_widget(
        tmp_path, has_gpu=True, cached_hardware_gpu=True, pending="gpu"
    )
    qtbot.addWidget(widget)
    # 当前 gpu，pending 也是 gpu → 单选选中 gpu → 无变化
    widget._gpu_radio.setChecked(True)
    assert not widget._can_apply()


def test_apply_emits_backend_changed(_cleanup, qtbot, tmp_path):
    """应用成功后应发射 backend_changed 信号"""
    widget = _make_widget(tmp_path, has_gpu=True, cached_hardware_gpu=True)
    qtbot.addWidget(widget)
    received = []
    widget.backend_changed.connect(lambda: received.append(True))
    widget._cpu_radio.setChecked(True)
    widget._apply()
    assert received == [True]


def test_current_backend_matches_resolve_use_gpu_not_live_detect(
    _cleanup, qtbot, tmp_path
):
    """问题5：实时 nvidia-smi 探测失败（has_gpu=False）但 resolve_use_gpu=True
    （缓存 has_gpu=True）时，"当前后端"应显示 GPU（与实际推理一致），而非 CPU。

    早期版本用 detect_gpu_info 的 has_gpu 直接覆盖 _current，导致 UI 显示 CPU
    而推理实为 GPU。修复后 _current 由 resolve_use_gpu 决定。
    """
    widget = _make_widget(
        tmp_path,
        has_gpu=False,  # 实时探测失败（nvidia-smi 超时/不可用）
        cached_hardware_gpu=True,  # 缓存记录有 GPU → resolve_use_gpu 返回 True
    )
    qtbot.addWidget(widget)
    assert widget.current_backend() == "gpu"
    assert "GPU" in widget._current_label.text()


def test_close_stops_running_gpu_detection_worker(_cleanup, qtbot, tmp_path):
    """Closing the widget should not leave GPU detection running."""
    widget = _make_widget(
        tmp_path,
        has_gpu=True,
        cached_hardware_gpu=True,
        worker_cls=_RunningStubGpuDetectWorker,
    )
    qtbot.addWidget(widget)
    worker = widget._detect_worker
    assert worker is not None
    assert worker.isRunning()

    widget.close()

    assert isinstance(worker, _RunningStubGpuDetectWorker)
    assert worker.cancel_called
    assert worker.quit_called
    assert worker.wait_calls

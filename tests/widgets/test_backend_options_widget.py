"""设置页"推理后端"组件测试"""

from unittest.mock import patch

import pytest


def _make_widget(tmp_path, has_gpu=True, cached_hardware_gpu=False, pending=None):
    """构造 BackendOptionsWidget（在 patch 作用域外也能用，patch 注入到模块）。

    返回 widget。由于 widget 的 _apply 在构造后才调用，patch 必须覆盖调用时刻。
    本函数把 update_cache_field 的 mock 存到 widget._mock_update 以便断言。
    """
    from vibeocr.widgets import backend_options_widget as bow

    # 直接替换模块级引用（widget 内 from ... import 拿到的就是模块属性）
    orig_em = bow.env_manager
    orig_cache = bow.is_cache_valid

    mock_em = patch.object(bow, "env_manager").start()
    mock_cache = patch.object(bow, "is_cache_valid").start()
    mock_update = patch.object(bow, "update_cache_field").start()

    mock_em.detect_gpu.return_value = (has_gpu, "cu130") if has_gpu else (False, None)
    mock_cache.return_value = (
        True,
        {
            "hardware_info": {"has_gpu": cached_hardware_gpu},
            "pending_backend": pending,
        },
    )
    mock_update.return_value = True

    try:
        widget = bow.BackendOptionsWidget(tmp_path)
    finally:
        # 恢复模块引用（构造已完成，状态已读入 widget 实例）
        patch.object(bow, "env_manager", orig_em).start()
        patch.object(bow, "is_cache_valid", orig_cache).start()
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

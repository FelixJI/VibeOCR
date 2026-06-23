"""首启 GPU/CPU 选择对话框测试"""

from unittest.mock import patch

import pytest

from vibeocr.widgets import backend_choice_dialog as bcd_module


@pytest.fixture
def _cleanup():
    yield
    patch.stopall()


def _make_dialog(tmp_path, has_gpu=True):
    """构造 BackendChoiceDialog，mock env_manager.detect_gpu"""
    mock_em = patch.object(bcd_module, "env_manager").start()
    mock_em.detect_gpu.return_value = (has_gpu, "cu126") if has_gpu else (False, None)
    return bcd_module.BackendChoiceDialog(tmp_path)


def test_gpu_available_defaults_to_gpu(_cleanup, qtbot, tmp_path):
    """有 GPU 时默认选 GPU，两项启用"""
    dlg = _make_dialog(tmp_path, has_gpu=True)
    qtbot.addWidget(dlg)
    assert dlg._gpu_radio.isChecked()
    assert dlg._gpu_radio.isEnabled()
    assert dlg._cpu_radio.isEnabled()


def test_no_gpu_disables_gpu_defaults_cpu(_cleanup, qtbot, tmp_path):
    """无 GPU 时 GPU 禁用，默认 CPU"""
    dlg = _make_dialog(tmp_path, has_gpu=False)
    qtbot.addWidget(dlg)
    assert not dlg._gpu_radio.isEnabled()
    assert dlg._cpu_radio.isChecked()


def test_selected_backend_returns_choice(_cleanup, qtbot, tmp_path):
    """selected_backend 反映单选"""
    dlg = _make_dialog(tmp_path, has_gpu=True)
    qtbot.addWidget(dlg)
    dlg._cpu_radio.setChecked(True)
    assert dlg.selected_backend() == "cpu"
    dlg._gpu_radio.setChecked(True)
    assert dlg.selected_backend() == "gpu"


def test_install_button_visible_initially(_cleanup, qtbot, tmp_path):
    """初始应显示"开始安装"按钮（未 show 时 isVisible 为 False 属正常，
    改测 button 存在且文本正确 + 未隐藏）"""
    dlg = _make_dialog(tmp_path, has_gpu=True)
    qtbot.addWidget(dlg)
    assert not dlg._install_button.isHidden()
    assert dlg._install_button.isEnabled()
    assert "安装" in dlg._install_button.text()

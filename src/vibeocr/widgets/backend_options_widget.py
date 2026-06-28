"""设置页"推理后端"组件

显示当前 OCR 后端（GPU/CPU），允许用户标记待切换（下次重启自动下载安装）。
不立即执行切换——纯写 pending_backend 标记。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from vibeocr import env_manager
from vibeocr.machine_cache import is_cache_valid, update_cache_field
from vibeocr.ui import theme

if TYPE_CHECKING:
    from pathlib import Path


class BackendOptionsWidget(QWidget):
    """推理后端设置组件"""

    backend_changed = Signal()  # pending_backend 写入后发射

    def __init__(self, project_root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._has_gpu = False
        self._current = "cpu"
        self._pending: str | None = None
        self._setup_ui()
        self._load_state()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        group = QGroupBox("推理后端")
        group_layout = QVBoxLayout(group)

        self._current_label = QLabel("当前后端：检测中...")
        group_layout.addWidget(self._current_label)

        # 单选（放进 QButtonGroup 确保互斥）
        self._radio_group = QButtonGroup(self)
        radio_layout = QHBoxLayout()
        self._gpu_radio = QRadioButton("GPU 加速（推荐）")
        self._gpu_radio.setToolTip("约 1.5GB，识别更快，需 NVIDIA GPU")
        self._cpu_radio = QRadioButton("CPU 模式")
        self._cpu_radio.setToolTip("约 150MB，兼容性广")
        self._radio_group.addButton(self._gpu_radio)
        self._radio_group.addButton(self._cpu_radio)
        radio_layout.addWidget(self._gpu_radio)
        radio_layout.addWidget(self._cpu_radio)
        radio_layout.addStretch()
        group_layout.addLayout(radio_layout)

        # 提示文字
        self._hint_label = QLabel(
            "GPU：约 1.5GB，识别更快，需 NVIDIA GPU\nCPU：约 150MB，兼容性广"
        )
        self._hint_label.setWordWrap(True)
        group_layout.addWidget(self._hint_label)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet(f"color: {theme.Colors.text_muted};")
        group_layout.addWidget(self._status_label)

        self._apply_button = QPushButton("应用（下次重启生效）")
        self._apply_button.clicked.connect(self._apply)
        group_layout.addWidget(self._apply_button)

        layout.addWidget(group)
        layout.addStretch()

        # 单选变化时更新应用按钮状态
        self._gpu_radio.toggled.connect(self._update_apply_state)

    def _load_state(self) -> None:
        """从 detect_gpu + 缓存加载当前/待切换状态"""
        self._has_gpu, _cuda = env_manager.detect_gpu()
        is_valid, cached = is_cache_valid(self._project_root)
        hw = (cached or {}).get("hardware_info", {}) if is_valid else {}
        self._current = "gpu" if hw.get("has_gpu") else "cpu"
        self._pending = (cached or {}).get("pending_backend") if is_valid else None

        if not self._has_gpu:
            self._gpu_radio.setEnabled(False)
            self._gpu_radio.setToolTip("未检测到 NVIDIA GPU")
            self._current = "cpu"

        name = "GPU" if self._current == "gpu" else "CPU"
        self._current_label.setText(f"当前后端：{name}")

        # 单选反映"待切换目标"（若有）否则"当前"
        target = self._pending or self._current
        if target == "gpu" and self._has_gpu:
            self._gpu_radio.setChecked(True)
        else:
            self._cpu_radio.setChecked(True)

        self._refresh_status(self._pending)
        self._update_apply_state()

    def current_backend(self) -> str:
        return self._current

    def _refresh_status(self, pending: str | None) -> None:
        if pending:
            name = "GPU" if pending == "gpu" else "CPU"
            self._status_label.setText(f"⏳ 待切换到 {name}，下次重启自动下载并生效")
        else:
            self._status_label.setText("")

    def _can_apply(self) -> bool:
        """当前单选目标是否与待切换/当前不同（即有变化可应用）"""
        target = "gpu" if self._gpu_radio.isChecked() else "cpu"
        return target != (self._pending or self._current)

    def _update_apply_state(self) -> None:
        self._apply_button.setEnabled(self._can_apply())

    def _apply(self) -> None:
        if not self._can_apply():
            return
        target = "gpu" if self._gpu_radio.isChecked() else "cpu"
        ok = update_cache_field(self._project_root, "pending_backend", target)
        if ok:
            self._pending = target
            self._refresh_status(target)
            self._update_apply_state()
            self.backend_changed.emit()
        else:
            self._status_label.setText("⚠ 写入缓存失败，请重试")

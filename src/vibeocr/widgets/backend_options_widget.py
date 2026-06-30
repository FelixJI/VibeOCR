"""设置页"推理后端"组件

显示当前 OCR 后端（GPU/CPU），允许用户标记待切换（下次重启自动下载安装）。
不立即执行切换——纯写 pending_backend 标记。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QThread, Signal
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

logger = logging.getLogger(__name__)


class _GpuDetectWorker(QThread):
    """后台 GPU 探测 worker。

    ``env_manager.detect_gpu_info`` 内部会同步 ``subprocess.run(["nvidia-smi"],
    timeout=5)``，在有 NVIDIA GPU 的机器上耗时显著。放到后台线程避免阻塞
    设置页控件构造（进而避免阻塞应用启动——该控件在 MainWindow.__init__ 的
    _connect_signals 链中被构造）。探测完成后通过信号把 info dict 回主线程。
    """

    finished_info = Signal(dict)  # detect_gpu_info() 的返回值

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

    def run(self) -> None:
        try:
            info = env_manager.detect_gpu_info()
        except Exception:
            # detect_gpu_info 自身有兜底，理论上不抛；防御性捕获避免线程静默挂起。
            logger.exception("[BackendOptions] 后台 GPU 探测异常")
            info = {"has_gpu": False, "name": "", "vram_mb": 0, "cuda": None}
        self.finished_info.emit(dict(info))


class BackendOptionsWidget(QWidget):
    """推理后端设置组件"""

    backend_changed = Signal()  # pending_backend 写入后发射

    def __init__(self, project_root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project_root = project_root
        self._has_gpu = False
        self._current = "cpu"
        self._pending: str | None = None
        self._detect_worker: _GpuDetectWorker | None = None
        self._setup_ui()
        # 缓存读取（纯文件 IO，无 subprocess）可在构造期同步完成；
        # detect_gpu_info 的 nvidia-smi 探测改为后台线程，避免阻塞启动。
        self._load_cached_state()
        self._start_gpu_detection()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        group = QGroupBox("推理后端")
        group_layout = QVBoxLayout(group)

        self._current_label = QLabel("当前后端：检测中...")
        group_layout.addWidget(self._current_label)

        # 硬件信息展示（GPU 型号/显存/CUDA 或未检测到）
        # 探测完成前显示"检测中..."，由 _apply_detected_state 回填。
        self._hw_label = QLabel("硬件检测中...")
        self._hw_label.setWordWrap(True)
        self._hw_label.setStyleSheet(f"color: {theme.Colors.text_muted};")
        group_layout.addWidget(self._hw_label)

        # 单选（放进 QButtonGroup 确保互斥）
        self._radio_group = QButtonGroup(self)
        radio_layout = QHBoxLayout()
        self._gpu_radio = QRadioButton("GPU 加速（推荐）")
        self._gpu_radio.setToolTip("约 1.5GB，识别更快，需 NVIDIA GPU")
        self._cpu_radio = QRadioButton("CPU 模式")
        self._cpu_radio.setToolTip("约 150MB，兼容性广")
        # 探测完成前禁用，避免基于未知硬件状态误操作后端切换。
        self._gpu_radio.setEnabled(False)
        self._cpu_radio.setEnabled(False)
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

    def _load_cached_state(self) -> None:
        """从缓存加载当前/待切换状态（纯文件 IO，无 subprocess，可在构造期同步执行）。

        注意：``_current`` 来自缓存 hardware_info.has_gpu（上次检测写入），
        ``_has_gpu``（能否选 GPU）要等实时探测 ``_apply_detected_state`` 回填。
        在探测完成前，radio/apply 均禁用，仅展示"检测中..."。
        """
        is_valid, cached = is_cache_valid(self._project_root)
        hw = (cached or {}).get("hardware_info", {}) if is_valid else {}
        self._current = "gpu" if hw.get("has_gpu") else "cpu"
        self._pending = (cached or {}).get("pending_backend") if is_valid else None

        # 待切换状态可立即展示（无需 GPU 探测结果）。
        self._refresh_status(self._pending)

    def _start_gpu_detection(self) -> None:
        """启动后台线程探测 GPU，完成后回填 UI。"""
        self._detect_worker = _GpuDetectWorker(self)
        self._detect_worker.finished_info.connect(self._apply_detected_state)
        self._detect_worker.start()

    def _apply_detected_state(self, info: dict[str, Any]) -> None:
        """后台 GPU 探测完成后，在主线程回填 _has_gpu 与硬件展示、启用控件。

        Args:
            info: ``detect_gpu_info()`` 返回的 dict
                (has_gpu/name/vram_mb/cuda)
        """
        self._has_gpu = bool(info.get("has_gpu"))

        if not self._has_gpu:
            self._gpu_radio.setEnabled(False)
            self._gpu_radio.setToolTip("未检测到 NVIDIA GPU")
            self._current = "cpu"
            self._hw_label.setText(
                "未检测到符合 CUDA 条件的 NVIDIA GPU（文档解析 MinerU 与 VL 模型不可用）"
            )
        else:
            # CPU 单选始终可选；GPU 单选仅在检测到 GPU 时启用。
            self._cpu_radio.setEnabled(True)
            self._gpu_radio.setEnabled(True)
            gpu_name = info.get("name") or "NVIDIA GPU"
            vram = info.get("vram_mb") or 0
            vram_str = f"{vram // 1024}GB" if vram >= 1024 else f"{vram}MB"
            cuda = info.get("cuda")
            cuda_str = f"CUDA {cuda}" if cuda else "CUDA 版本未知"
            self._hw_label.setText(f"GPU：{gpu_name}（{vram_str}），{cuda_str}")

        name = "GPU" if self._current == "gpu" else "CPU"
        self._current_label.setText(f"当前后端：{name}")

        # 单选反映"待切换目标"（若有）否则"当前"
        target = self._pending or self._current
        if target == "gpu" and self._has_gpu:
            self._gpu_radio.setChecked(True)
        else:
            self._cpu_radio.setChecked(True)

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

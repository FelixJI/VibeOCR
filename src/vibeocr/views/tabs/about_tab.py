# src/vibeocr/views/tabs/about_tab.py
"""关于标签页 — 展示应用元信息"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGroupBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from vibeocr import __version__, env_manager

logger = logging.getLogger(__name__)

_APP_NAME = "VibeOCR"
_DESCRIPTION = "一款基于 PaddleOCR 的截图文字识别工具，支持表格识别、公式识别、文档解析等功能。"
_AUTHOR = "Felix Ji"
_COPYRIGHT = "© 2025 Felix Ji. All rights reserved."
_GITHUB_URL = "https://github.com/felixji/vibeocr"
_TECH_STACK = [
    ("PaddlePaddle / PaddleX", "OCR 引擎"),
    ("MinerU", "文档解析"),
    ("PySide6", "UI 框架"),
]


class AboutTab(QWidget):
    """关于标签页，展示应用元信息。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        # --- scroll area wrapping everything ---
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(16)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        # --- header: app name + version ---
        self._name_label = QLabel(_APP_NAME)
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self._name_label.font()
        font.setPointSize(24)
        font.setBold(True)
        self._name_label.setFont(font)

        self._version_label = QLabel(f"v{__version__}")
        self._version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v_font = self._version_label.font()
        v_font.setPointSize(14)
        self._version_label.setFont(v_font)
        self._version_label.setStyleSheet("color: gray;")

        layout.addWidget(self._name_label)
        layout.addWidget(self._version_label)

        # --- sections ---
        # 简介
        desc_label = QLabel(_DESCRIPTION)
        desc_label.setWordWrap(True)
        desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._create_section("简介", desc_label))

        # 技术栈
        tech_lines = "".join(
            f"<li><b>{name}</b> — {role}</li>" for name, role in _TECH_STACK
        )
        tech_label = QLabel(f"<ul>{tech_lines}</ul>")
        tech_label.setWordWrap(True)
        layout.addWidget(self._create_section("技术栈", tech_label))

        # 作者
        author_label = QLabel(_AUTHOR)
        author_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._create_section("作者", author_label))

        # 版权
        copyright_label = QLabel(_COPYRIGHT)
        copyright_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._create_section("版权", copyright_label))

        # 项目链接
        link_label = QLabel(
            f'<a href="{_GITHUB_URL}">{_GITHUB_URL}</a>'
        )
        link_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        link_label.setOpenExternalLinks(True)
        layout.addWidget(self._create_section("项目链接", link_label))

        # --- changelog ---
        self._changelog_browser = QTextBrowser()
        self._changelog_browser.setOpenExternalLinks(True)
        self._changelog_browser.setMaximumHeight(300)

        changelog_path: Path = env_manager.get_project_root() / "CHANGELOG.md"
        if changelog_path.exists():
            try:
                raw = changelog_path.read_text(encoding="utf-8")
                self._changelog_browser.setMarkdown(raw)
            except Exception:
                logger.exception("读取 CHANGELOG.md 失败")
                self._changelog_browser.setMarkdown("暂无更新日志")
        else:
            self._changelog_browser.setMarkdown("暂无更新日志")

        layout.addWidget(self._create_section("更新日志", self._changelog_browser))

        # --- check update button ---
        update_btn = QPushButton("检查更新")
        update_btn.setMaximumWidth(120)
        update_btn.clicked.connect(self._on_check_update)
        layout.addWidget(update_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # --- bottom stretch ---
        layout.addStretch()

        scroll.setWidget(container)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _create_section(title: str, widget: QWidget) -> QGroupBox:
        """创建一个带有标题的 QGroupBox 包裹 widget。"""
        group = QGroupBox(title)
        vbox = QVBoxLayout(group)
        vbox.addWidget(widget)
        return group

    def _on_check_update(self) -> None:
        """手动检查更新"""
        import asyncio

        try:
            from vibeocr.services.update_service import UpdateService

            app_dir = env_manager.get_project_root()
            service = UpdateService(app_dir)
            asyncio.ensure_future(service.check_and_prompt(self))
        except Exception as e:
            logger.exception(f"检查更新失败: {e}")

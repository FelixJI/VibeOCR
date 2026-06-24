# src/vibeocr/views/tabs/about_tab.py
"""关于标签页 — 展示应用元信息（卡片化布局）"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from vibeocr import __version__, env_manager
from vibeocr.ui import theme

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

_APP_NAME = "VibeOCR"
_DESCRIPTION = (
    "一款基于 PaddleOCR 的截图文字识别工具，支持表格识别、公式识别、文档解析等功能。"
)
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
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        container = QWidget()
        container.setMaximumWidth(720)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(
            theme.Spacing.xxl, theme.Spacing.xl,
            theme.Spacing.xxl, theme.Spacing.xl,
        )
        layout.setSpacing(theme.Spacing.lg)

        # 品牌卡片
        layout.addWidget(self._create_brand_card())
        # 详细信息卡片
        layout.addWidget(self._create_info_card())
        # 更新日志卡片
        layout.addWidget(self._create_changelog_card())

        # 检查更新按钮
        update_btn = QPushButton("检查更新")
        update_btn.setFixedWidth(160)
        update_btn.setStyleSheet(theme.button_qss("primary"))
        update_btn.clicked.connect(self._on_check_update)
        layout.addWidget(update_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        layout.addStretch()

        scroll.setWidget(container)

        # 居中 container（宽屏下左右留白）
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch()
        outer.addWidget(scroll, stretch=1)
        outer.addStretch()

    def _create_card(self) -> tuple[QFrame, QVBoxLayout]:
        """创建一张卡片容器（QFrame + card_qss）。

        Returns:
            (card_frame, card_layout) 元组。
        """
        card = QFrame()
        card.setObjectName("card")
        card.setStyleSheet(theme.card_qss())
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(
            theme.Spacing.lg, theme.Spacing.lg,
            theme.Spacing.lg, theme.Spacing.lg,
        )
        card_layout.setSpacing(theme.Spacing.sm)
        return card, card_layout

    def _create_brand_card(self) -> QFrame:
        """品牌卡片：图标 + 应用名 + 版本徽标 + 简介。"""
        card, card_layout = self._create_card()

        logo = self._create_logo_label(96)
        if logo is not None:
            card_layout.addWidget(logo, alignment=Qt.AlignmentFlag.AlignCenter)

        name_label = QLabel(_APP_NAME)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = name_label.font()
        f.setPointSize(theme.Typography.title)
        f.setBold(True)
        name_label.setFont(f)
        self._name_label = name_label
        card_layout.addWidget(name_label)

        # 版本药丸徽标
        version_label = QLabel(f" v{__version__} ")
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version_label.setStyleSheet(
            f"background: {theme.Colors.accent_soft}; color: {theme.Colors.accent};"
            f" border-radius: {theme.Radius.md}px;"
            f" padding: 2px {theme.Spacing.sm}px;"
            f" font-size: {theme.Typography.body}px;"
        )
        self._version_label = version_label
        card_layout.addWidget(version_label, alignment=Qt.AlignmentFlag.AlignCenter)

        card_layout.addSpacing(theme.Spacing.sm)
        desc_label = QLabel(_DESCRIPTION)
        desc_label.setWordWrap(True)
        desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_label.setStyleSheet(f"color: {theme.Colors.text_muted};")
        card_layout.addWidget(desc_label)
        return card

    def _create_info_card(self) -> QFrame:
        """详细信息卡片：键值对。"""
        card, card_layout = self._create_card()

        title = QLabel("详细信息")
        title.setStyleSheet(
            f"font-size: {theme.Typography.h1}px;"
            f" font-weight: {theme.Typography.weight_bold};"
            f" color: {theme.Colors.text};"
        )
        card_layout.addWidget(title)

        form = QFormLayout()
        form.setSpacing(theme.Spacing.sm)
        label_style = f"color: {theme.Colors.text_muted};"

        def make_label(text: str) -> QLabel:
            lbl = QLabel(text)
            lbl.setStyleSheet(label_style)
            return lbl

        tech = " · ".join(name for name, _ in _TECH_STACK)
        link = QLabel(
            f'<a href="{_GITHUB_URL}" style="color:{theme.Colors.accent};">'
            f"{_GITHUB_URL}</a>"
        )
        link.setOpenExternalLinks(True)

        form.addRow(make_label("作者"), QLabel(_AUTHOR))
        form.addRow(make_label("版权"), QLabel(_COPYRIGHT))
        form.addRow(make_label("技术栈"), QLabel(tech))
        form.addRow(make_label("项目"), link)
        card_layout.addLayout(form)
        return card

    def _create_changelog_card(self) -> QFrame:
        """更新日志卡片。"""
        card, card_layout = self._create_card()

        title = QLabel("更新日志")
        title.setStyleSheet(
            f"font-size: {theme.Typography.h1}px;"
            f" font-weight: {theme.Typography.weight_bold};"
            f" color: {theme.Colors.text};"
        )
        card_layout.addWidget(title)

        self._changelog_browser = QTextBrowser()
        self._changelog_browser.setOpenExternalLinks(True)
        self._changelog_browser.setMaximumHeight(320)
        self._changelog_browser.setFrameShape(QTextBrowser.Shape.NoFrame)
        self._changelog_browser.setStyleSheet("background: transparent;")

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
        card_layout.addWidget(self._changelog_browser)
        return card

    @staticmethod
    def _create_logo_label(size: int = 128) -> QLabel | None:
        """创建关于页 Logo 标签。

        优先读取多分辨率 app_icon.ico；缺失时返回 None（不破坏布局）。
        """
        icon_path = env_manager.get_project_root() / "resources" / "app_icon.ico"
        if not icon_path.exists():
            logger.warning(f"应用图标不存在: {icon_path}")
            return None

        pixmap = QPixmap(str(icon_path))
        if pixmap.isNull():
            logger.warning(f"应用图标加载失败: {icon_path}")
            return None

        label = QLabel()
        label.setPixmap(
            pixmap.scaled(
                QSize(size, size),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        return label

    def _on_check_update(self) -> None:
        """手动检查更新"""
        import asyncio

        try:
            from vibeocr.services.update_service import UpdateService

            app_dir = env_manager.get_project_root()
            service = UpdateService(app_dir)
            _update_task = asyncio.ensure_future(service.check_and_prompt(self))  # noqa: RUF006
        except Exception as e:
            logger.exception(f"检查更新失败: {e}")

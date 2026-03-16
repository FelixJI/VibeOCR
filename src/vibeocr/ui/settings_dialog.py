"""设置对话框

独立设置对话框，可从系统托盘菜单访问。
用于配置工具栏自动隐藏、系统托盘、开机自启动等选项。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QDialog, QWidget

from vibeocr.ui.ui_settings_dialog import Ui_SettingsDialog

if TYPE_CHECKING:
    from vibeocr.utils.app_settings import AppSettings

logger = logging.getLogger(__name__)


class SettingsDialog(QDialog):
    """设置对话框

    提供工具栏和系统设置的配置界面。
    """

    def __init__(
        self,
        app_settings: AppSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._app_settings = app_settings

        self._ui = Ui_SettingsDialog()
        self._ui.setupUi(self)

        self._load_from_settings()
        self._connect_signals()

    def _load_from_settings(self) -> None:
        """从 AppSettings 加载当前值到 UI"""
        self._ui.chkAutoHide.setChecked(self._app_settings.auto_hide_toolbar)
        self._ui.spinHideDelay.setValue(self._app_settings.hide_delay_ms)
        self._ui.chkMinimizeToTray.setChecked(self._app_settings.minimize_to_tray)
        self._ui.chkAutoStart.setChecked(self._app_settings.auto_start)

        # 延迟控件跟随自动隐藏开关
        self._ui.spinHideDelay.setEnabled(self._app_settings.auto_hide_toolbar)
        self._ui.labelHideDelay.setEnabled(self._app_settings.auto_hide_toolbar)

    def _connect_signals(self) -> None:
        """连接信号"""
        self._ui.btnSave.clicked.connect(self._on_save)
        self._ui.btnCancel.clicked.connect(self.reject)
        self._ui.chkAutoHide.toggled.connect(self._on_auto_hide_toggled)

    def _on_auto_hide_toggled(self, checked: bool) -> None:
        """自动隐藏复选框切换"""
        self._ui.spinHideDelay.setEnabled(checked)
        self._ui.labelHideDelay.setEnabled(checked)

    def _on_save(self) -> None:
        """保存设置"""
        self._app_settings.auto_hide_toolbar = self._ui.chkAutoHide.isChecked()
        self._app_settings.hide_delay_ms = self._ui.spinHideDelay.value()
        self._app_settings.minimize_to_tray = self._ui.chkMinimizeToTray.isChecked()

        # 开机自启动需要调用系统 API
        new_auto_start = self._ui.chkAutoStart.isChecked()
        if new_auto_start != self._app_settings.auto_start:
            from vibeocr.utils.autostart import set_autostart

            if set_autostart(new_auto_start):
                self._app_settings.auto_start = new_auto_start
                logger.info(f"开机自启动: {'启用' if new_auto_start else '禁用'}")
            else:
                logger.warning("设置开机自启动失败")

        self._app_settings.save()
        logger.info("设置已保存")
        self.accept()

################################################################################
## Form generated from reading UI file 'settings_dialog.ui'
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import QCoreApplication, QMetaObject, QSize
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QSpinBox,
    QVBoxLayout,
)


class Ui_SettingsDialog:
    def setupUi(self, SettingsDialog):
        if not SettingsDialog.objectName():
            SettingsDialog.setObjectName("SettingsDialog")
        SettingsDialog.setMinimumSize(QSize(380, 280))

        self.mainLayout = QVBoxLayout(SettingsDialog)
        self.mainLayout.setSpacing(12)
        self.mainLayout.setObjectName("mainLayout")
        self.mainLayout.setContentsMargins(16, 16, 16, 16)

        # ---- 工具栏组 ----
        self.groupToolbar = QGroupBox(SettingsDialog)
        self.groupToolbar.setObjectName("groupToolbar")
        self.toolbarLayout = QVBoxLayout(self.groupToolbar)
        self.toolbarLayout.setSpacing(8)
        self.toolbarLayout.setObjectName("toolbarLayout")

        self.chkAutoHide = QCheckBox(self.groupToolbar)
        self.chkAutoHide.setObjectName("chkAutoHide")
        self.chkAutoHide.setToolTip(
            "工具栏停靠在屏幕边缘时自动隐藏，鼠标靠近边缘时自动弹出"
        )
        self.toolbarLayout.addWidget(self.chkAutoHide)

        self.delayLayout = QHBoxLayout()
        self.delayLayout.setSpacing(8)
        self.delayLayout.setObjectName("delayLayout")
        self.delayLayout.setContentsMargins(20, 0, 0, 0)

        self.labelHideDelay = QLabel(self.groupToolbar)
        self.labelHideDelay.setObjectName("labelHideDelay")
        self.delayLayout.addWidget(self.labelHideDelay)

        self.spinHideDelay = QSpinBox(self.groupToolbar)
        self.spinHideDelay.setObjectName("spinHideDelay")
        self.spinHideDelay.setMinimum(100)
        self.spinHideDelay.setMaximum(5000)
        self.spinHideDelay.setSingleStep(100)
        self.spinHideDelay.setValue(500)
        self.spinHideDelay.setSuffix(" 毫秒")
        self.delayLayout.addWidget(self.spinHideDelay)

        self.delayLayout.addItem(
            QSpacerItem(
                40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
            )
        )
        self.toolbarLayout.addLayout(self.delayLayout)
        self.mainLayout.addWidget(self.groupToolbar)

        # ---- 系统组 ----
        self.groupSystem = QGroupBox(SettingsDialog)
        self.groupSystem.setObjectName("groupSystem")
        self.systemLayout = QVBoxLayout(self.groupSystem)
        self.systemLayout.setSpacing(8)
        self.systemLayout.setObjectName("systemLayout")

        self.chkMinimizeToTray = QCheckBox(self.groupSystem)
        self.chkMinimizeToTray.setObjectName("chkMinimizeToTray")
        self.chkMinimizeToTray.setToolTip("关闭主窗口时最小化到系统托盘而不是退出程序")
        self.systemLayout.addWidget(self.chkMinimizeToTray)

        self.chkAutoStart = QCheckBox(self.groupSystem)
        self.chkAutoStart.setObjectName("chkAutoStart")
        self.chkAutoStart.setToolTip("系统启动时自动运行 VibeOCR")
        self.systemLayout.addWidget(self.chkAutoStart)

        self.mainLayout.addWidget(self.groupSystem)

        # ---- 弹性空间 ----
        self.mainLayout.addItem(
            QSpacerItem(
                20, 20, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding
            )
        )

        # ---- 按钮行 ----
        self.buttonLayout = QHBoxLayout()
        self.buttonLayout.setObjectName("buttonLayout")

        self.buttonLayout.addItem(
            QSpacerItem(
                40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
            )
        )

        self.btnSave = QPushButton(SettingsDialog)
        self.btnSave.setObjectName("btnSave")
        self.buttonLayout.addWidget(self.btnSave)

        self.btnCancel = QPushButton(SettingsDialog)
        self.btnCancel.setObjectName("btnCancel")
        self.buttonLayout.addWidget(self.btnCancel)

        self.mainLayout.addLayout(self.buttonLayout)

        self.retranslateUi(SettingsDialog)
        QMetaObject.connectSlotsByName(SettingsDialog)

    def retranslateUi(self, SettingsDialog):
        SettingsDialog.setWindowTitle(
            QCoreApplication.translate("SettingsDialog", "VibeOCR 设置", None)
        )
        self.groupToolbar.setTitle(
            QCoreApplication.translate("SettingsDialog", "工具栏", None)
        )
        self.chkAutoHide.setText(
            QCoreApplication.translate("SettingsDialog", "自动隐藏边缘工具栏", None)
        )
        self.labelHideDelay.setText(
            QCoreApplication.translate("SettingsDialog", "隐藏延迟:", None)
        )
        self.groupSystem.setTitle(
            QCoreApplication.translate("SettingsDialog", "系统", None)
        )
        self.chkMinimizeToTray.setText(
            QCoreApplication.translate("SettingsDialog", "最小化到系统托盘", None)
        )
        self.chkAutoStart.setText(
            QCoreApplication.translate("SettingsDialog", "开机自启动", None)
        )
        self.btnSave.setText(QCoreApplication.translate("SettingsDialog", "保存", None))
        self.btnCancel.setText(
            QCoreApplication.translate("SettingsDialog", "取消", None)
        )

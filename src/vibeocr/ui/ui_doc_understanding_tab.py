################################################################################
## Form generated from reading UI file 'doc_understanding_tab.ui'
##
## Created by: Qt User Interface Compiler version 6.10.2
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import QCoreApplication, QMetaObject, QSize, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QSplitter,
    QVBoxLayout,
    QWidget,
)


class Ui_DocUnderstandingTab:
    def setupUi(self, DocUnderstandingTab):
        if not DocUnderstandingTab.objectName():
            DocUnderstandingTab.setObjectName("DocUnderstandingTab")
        DocUnderstandingTab.resize(900, 600)
        self.verticalLayout = QVBoxLayout(DocUnderstandingTab)
        self.verticalLayout.setSpacing(8)
        self.verticalLayout.setObjectName("verticalLayout")
        self.verticalLayout.setContentsMargins(8, 8, 8, 8)
        self.topPanel = QWidget(DocUnderstandingTab)
        self.topPanel.setObjectName("topPanel")
        self.topLayout = QHBoxLayout(self.topPanel)
        self.topLayout.setObjectName("topLayout")
        self.topLayout.setContentsMargins(0, 0, 0, 0)
        self.labelModel = QLabel(self.topPanel)
        self.labelModel.setObjectName("labelModel")

        self.topLayout.addWidget(self.labelModel)

        self.comboModel = QComboBox(self.topPanel)
        self.comboModel.addItem("")
        self.comboModel.addItem("")
        self.comboModel.addItem("")
        self.comboModel.setObjectName("comboModel")

        self.topLayout.addWidget(self.comboModel)

        self.horizontalSpacer = QSpacerItem(
            0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )

        self.topLayout.addItem(self.horizontalSpacer)

        self.labelStatus = QLabel(self.topPanel)
        self.labelStatus.setObjectName("labelStatus")

        self.topLayout.addWidget(self.labelStatus)

        self.verticalLayout.addWidget(self.topPanel)

        self.mainSplitter = QSplitter(DocUnderstandingTab)
        self.mainSplitter.setObjectName("mainSplitter")
        self.mainSplitter.setOrientation(Qt.Horizontal)
        self.leftPanel = QWidget(self.mainSplitter)
        self.leftPanel.setObjectName("leftPanel")
        self.leftLayout = QVBoxLayout(self.leftPanel)
        self.leftLayout.setSpacing(8)
        self.leftLayout.setObjectName("leftLayout")
        self.leftLayout.setContentsMargins(0, 0, 0, 0)
        self.labelFiles = QLabel(self.leftPanel)
        self.labelFiles.setObjectName("labelFiles")

        self.leftLayout.addWidget(self.labelFiles)

        self.fileListWidget = QListWidget(self.leftPanel)
        self.fileListWidget.setObjectName("fileListWidget")
        self.fileListWidget.setSelectionMode(QAbstractItemView.SingleSelection)

        self.leftLayout.addWidget(self.fileListWidget)

        self.fileButtonsLayout = QHBoxLayout()
        self.fileButtonsLayout.setObjectName("fileButtonsLayout")
        self.btnAddFile = QPushButton(self.leftPanel)
        self.btnAddFile.setObjectName("btnAddFile")

        self.fileButtonsLayout.addWidget(self.btnAddFile)

        self.btnRemoveFile = QPushButton(self.leftPanel)
        self.btnRemoveFile.setObjectName("btnRemoveFile")

        self.fileButtonsLayout.addWidget(self.btnRemoveFile)

        self.leftLayout.addLayout(self.fileButtonsLayout)

        self.mainSplitter.addWidget(self.leftPanel)
        self.rightPanel = QWidget(self.mainSplitter)
        self.rightPanel.setObjectName("rightPanel")
        self.rightLayout = QVBoxLayout(self.rightPanel)
        self.rightLayout.setSpacing(8)
        self.rightLayout.setObjectName("rightLayout")
        self.rightLayout.setContentsMargins(0, 0, 0, 0)
        self.labelPreview = QLabel(self.rightPanel)
        self.labelPreview.setObjectName("labelPreview")

        self.rightLayout.addWidget(self.labelPreview)

        self.previewLabel = QLabel(self.rightPanel)
        self.previewLabel.setObjectName("previewLabel")
        self.previewLabel.setMinimumSize(QSize(0, 150))
        self.previewLabel.setAlignment(Qt.AlignCenter)
        self.previewLabel.setStyleSheet(
            "background-color: #f0f0f0; border: 1px solid #ccc; border-radius: 4px;"
        )

        self.rightLayout.addWidget(self.previewLabel)

        self.labelChat = QLabel(self.rightPanel)
        self.labelChat.setObjectName("labelChat")

        self.rightLayout.addWidget(self.labelChat)

        self.chatContainer = QWidget(self.rightPanel)
        self.chatContainer.setObjectName("chatContainer")
        self.chatContainerLayout = QVBoxLayout(self.chatContainer)
        self.chatContainerLayout.setObjectName("chatContainerLayout")
        self.chatContainerLayout.setContentsMargins(0, 0, 0, 0)

        self.rightLayout.addWidget(self.chatContainer)

        self.mainSplitter.addWidget(self.rightPanel)

        self.verticalLayout.addWidget(self.mainSplitter)

        self.retranslateUi(DocUnderstandingTab)

        QMetaObject.connectSlotsByName(DocUnderstandingTab)

    # setupUi

    def retranslateUi(self, DocUnderstandingTab):
        self.labelModel.setText(
            QCoreApplication.translate("DocUnderstandingTab", "\u6a21\u578b:", None)
        )
        self.comboModel.setItemText(
            0, QCoreApplication.translate("DocUnderstandingTab", "PP-DocBee2-3B", None)
        )
        self.comboModel.setItemText(
            1, QCoreApplication.translate("DocUnderstandingTab", "PP-DocBee-2B", None)
        )
        self.comboModel.setItemText(
            2, QCoreApplication.translate("DocUnderstandingTab", "PP-DocBee-7B", None)
        )

        self.labelStatus.setText(
            QCoreApplication.translate(
                "DocUnderstandingTab", "\u72b6\u6001: \u25cb \u672a\u8fde\u63a5", None
            )
        )
        self.labelFiles.setText(
            QCoreApplication.translate(
                "DocUnderstandingTab", "\u6587\u4ef6\u5217\u8868", None
            )
        )
        self.btnAddFile.setText(
            QCoreApplication.translate("DocUnderstandingTab", "\u6dfb\u52a0", None)
        )
        self.btnRemoveFile.setText(
            QCoreApplication.translate("DocUnderstandingTab", "\u5220\u9664", None)
        )
        self.labelPreview.setText(
            QCoreApplication.translate(
                "DocUnderstandingTab", "\u6587\u6863\u9884\u89c8", None
            )
        )
        self.previewLabel.setText(
            QCoreApplication.translate(
                "DocUnderstandingTab",
                "\u9009\u62e9\u6587\u4ef6\u540e\u663e\u793a\u9884\u89c8",
                None,
            )
        )
        self.labelChat.setText(
            QCoreApplication.translate("DocUnderstandingTab", "\u5bf9\u8bdd", None)
        )
        pass

    # retranslateUi

################################################################################
## Form generated from reading UI file 'extraction_tab.ui'
##
## Created by: Qt User Interface Compiler version 6.10.2
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import QCoreApplication, QMetaObject, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpacerItem,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from vibeocr.widgets.batch_file_list_widget import BatchFileListWidget


class Ui_ExtractionTab:
    def setupUi(self, ExtractionTab):
        if not ExtractionTab.objectName():
            ExtractionTab.setObjectName("ExtractionTab")
        ExtractionTab.resize(900, 600)
        self.verticalLayout = QVBoxLayout(ExtractionTab)
        self.verticalLayout.setSpacing(8)
        self.verticalLayout.setObjectName("verticalLayout")
        self.verticalLayout.setContentsMargins(8, 8, 8, 8)
        self.mainSplitter = QSplitter(ExtractionTab)
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

        self.fileListWidget = BatchFileListWidget(self.leftPanel)
        self.fileListWidget.setObjectName("fileListWidget")

        self.leftLayout.addWidget(self.fileListWidget)

        self.labelKeys = QLabel(self.leftPanel)
        self.labelKeys.setObjectName("labelKeys")

        self.leftLayout.addWidget(self.labelKeys)

        self.comboTemplate = QComboBox(self.leftPanel)
        self.comboTemplate.addItem("")
        self.comboTemplate.setObjectName("comboTemplate")

        self.leftLayout.addWidget(self.comboTemplate)

        self.labelCustomKeys = QLabel(self.leftPanel)
        self.labelCustomKeys.setObjectName("labelCustomKeys")

        self.leftLayout.addWidget(self.labelCustomKeys)

        self.textCustomKeys = QPlainTextEdit(self.leftPanel)
        self.textCustomKeys.setObjectName("textCustomKeys")

        self.leftLayout.addWidget(self.textCustomKeys)

        self.mainSplitter.addWidget(self.leftPanel)
        self.rightPanel = QWidget(self.mainSplitter)
        self.rightPanel.setObjectName("rightPanel")
        self.rightLayout = QVBoxLayout(self.rightPanel)
        self.rightLayout.setSpacing(8)
        self.rightLayout.setObjectName("rightLayout")
        self.rightLayout.setContentsMargins(0, 0, 0, 0)
        self.groupOptions = QGroupBox(self.rightPanel)
        self.groupOptions.setObjectName("groupOptions")
        self.optionsLayout = QGridLayout(self.groupOptions)
        self.optionsLayout.setObjectName("optionsLayout")
        self.chkDocOrientation = QCheckBox(self.groupOptions)
        self.chkDocOrientation.setObjectName("chkDocOrientation")
        self.chkDocOrientation.setChecked(True)

        self.optionsLayout.addWidget(self.chkDocOrientation, 0, 0, 1, 1)

        self.chkDocUnwarping = QCheckBox(self.groupOptions)
        self.chkDocUnwarping.setObjectName("chkDocUnwarping")
        self.chkDocUnwarping.setChecked(True)

        self.optionsLayout.addWidget(self.chkDocUnwarping, 0, 1, 1, 1)

        self.chkGeneralOCR = QCheckBox(self.groupOptions)
        self.chkGeneralOCR.setObjectName("chkGeneralOCR")
        self.chkGeneralOCR.setChecked(True)

        self.optionsLayout.addWidget(self.chkGeneralOCR, 1, 0, 1, 1)

        self.chkTableRecognition = QCheckBox(self.groupOptions)
        self.chkTableRecognition.setObjectName("chkTableRecognition")
        self.chkTableRecognition.setChecked(True)

        self.optionsLayout.addWidget(self.chkTableRecognition, 1, 1, 1, 1)

        self.chkSealRecognition = QCheckBox(self.groupOptions)
        self.chkSealRecognition.setObjectName("chkSealRecognition")

        self.optionsLayout.addWidget(self.chkSealRecognition, 2, 0, 1, 1)

        self.rightLayout.addWidget(self.groupOptions)

        self.groupLLM = QGroupBox(self.rightPanel)
        self.groupLLM.setObjectName("groupLLM")
        self.llmLayout = QVBoxLayout(self.groupLLM)
        self.llmLayout.setObjectName("llmLayout")
        self.labelMLLMStatus = QLabel(self.groupLLM)
        self.labelMLLMStatus.setObjectName("labelMLLMStatus")

        self.llmLayout.addWidget(self.labelMLLMStatus)

        self.labelLLMStatus = QLabel(self.groupLLM)
        self.labelLLMStatus.setObjectName("labelLLMStatus")

        self.llmLayout.addWidget(self.labelLLMStatus)

        self.btnGoToSettings = QPushButton(self.groupLLM)
        self.btnGoToSettings.setObjectName("btnGoToSettings")

        self.llmLayout.addWidget(self.btnGoToSettings)

        self.rightLayout.addWidget(self.groupLLM)

        self.labelResults = QLabel(self.rightPanel)
        self.labelResults.setObjectName("labelResults")

        self.rightLayout.addWidget(self.labelResults)

        self.resultsContainer = QWidget(self.rightPanel)
        self.resultsContainer.setObjectName("resultsContainer")
        self.resultsContainerLayout = QVBoxLayout(self.resultsContainer)
        self.resultsContainerLayout.setObjectName("resultsContainerLayout")
        self.resultsContainerLayout.setContentsMargins(0, 0, 0, 0)

        self.rightLayout.addWidget(self.resultsContainer)

        self.mainSplitter.addWidget(self.rightPanel)

        self.verticalLayout.addWidget(self.mainSplitter)

        self.progressLayout = QHBoxLayout()
        self.progressLayout.setObjectName("progressLayout")
        self.btnStart = QPushButton(ExtractionTab)
        self.btnStart.setObjectName("btnStart")

        self.progressLayout.addWidget(self.btnStart)

        self.progressBar = QProgressBar(ExtractionTab)
        self.progressBar.setObjectName("progressBar")
        self.progressBar.setValue(0)

        self.progressLayout.addWidget(self.progressBar)

        self.labelProgress = QLabel(ExtractionTab)
        self.labelProgress.setObjectName("labelProgress")

        self.progressLayout.addWidget(self.labelProgress)

        self.btnCancel = QPushButton(ExtractionTab)
        self.btnCancel.setObjectName("btnCancel")
        self.btnCancel.setEnabled(False)

        self.progressLayout.addWidget(self.btnCancel)

        self.verticalLayout.addLayout(self.progressLayout)

        self.exportLayout = QHBoxLayout()
        self.exportLayout.setObjectName("exportLayout")
        self.labelExportMode = QLabel(ExtractionTab)
        self.labelExportMode.setObjectName("labelExportMode")

        self.exportLayout.addWidget(self.labelExportMode)

        self.radioExportSeparate = QRadioButton(ExtractionTab)
        self.radioExportSeparate.setObjectName("radioExportSeparate")

        self.exportLayout.addWidget(self.radioExportSeparate)

        self.radioExportMerged = QRadioButton(ExtractionTab)
        self.radioExportMerged.setObjectName("radioExportMerged")
        self.radioExportMerged.setChecked(True)

        self.exportLayout.addWidget(self.radioExportMerged)

        self.labelFormat = QLabel(ExtractionTab)
        self.labelFormat.setObjectName("labelFormat")

        self.exportLayout.addWidget(self.labelFormat)

        self.comboFormat = QComboBox(ExtractionTab)
        self.comboFormat.addItem("")
        self.comboFormat.addItem("")
        self.comboFormat.setObjectName("comboFormat")

        self.exportLayout.addWidget(self.comboFormat)

        self.btnExport = QPushButton(ExtractionTab)
        self.btnExport.setObjectName("btnExport")

        self.exportLayout.addWidget(self.btnExport)

        self.horizontalSpacer = QSpacerItem(
            0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum
        )

        self.exportLayout.addItem(self.horizontalSpacer)

        self.verticalLayout.addLayout(self.exportLayout)

        self.retranslateUi(ExtractionTab)

        QMetaObject.connectSlotsByName(ExtractionTab)

    # setupUi

    def retranslateUi(self, ExtractionTab):
        self.labelFiles.setText(
            QCoreApplication.translate(
                "ExtractionTab", "\u6587\u4ef6\u5217\u8868", None
            )
        )
        self.labelKeys.setText(
            QCoreApplication.translate(
                "ExtractionTab", "\u62bd\u53d6\u5b57\u6bb5", None
            )
        )
        self.comboTemplate.setItemText(
            0,
            QCoreApplication.translate(
                "ExtractionTab", "\u4e0d\u4f7f\u7528\u6a21\u677f", None
            ),
        )

        self.labelCustomKeys.setText(
            QCoreApplication.translate(
                "ExtractionTab",
                "\u81ea\u5b9a\u4e49\u5b57\u6bb5\uff08\u6bcf\u884c\u4e00\u4e2a\uff09:",
                None,
            )
        )
        self.textCustomKeys.setPlaceholderText(
            QCoreApplication.translate(
                "ExtractionTab",
                "\u8f93\u5165\u8981\u62bd\u53d6\u7684\u5b57\u6bb5\u540d\u79f0\uff0c\u6bcf\u884c\u4e00\u4e2a...",
                None,
            )
        )
        self.groupOptions.setTitle(
            QCoreApplication.translate(
                "ExtractionTab", "PP-ChatOCRv4 \u9009\u9879", None
            )
        )
        self.chkDocOrientation.setText(
            QCoreApplication.translate(
                "ExtractionTab", "\u6587\u6863\u65b9\u5411\u5206\u7c7b", None
            )
        )
        self.chkDocUnwarping.setText(
            QCoreApplication.translate(
                "ExtractionTab", "\u53bb\u5f2f\u66f2\u77eb\u6b63", None
            )
        )
        self.chkGeneralOCR.setText(
            QCoreApplication.translate("ExtractionTab", "\u901a\u7528 OCR", None)
        )
        self.chkTableRecognition.setText(
            QCoreApplication.translate(
                "ExtractionTab", "\u8868\u683c\u8bc6\u522b", None
            )
        )
        self.chkSealRecognition.setText(
            QCoreApplication.translate(
                "ExtractionTab", "\u5370\u7ae0\u8bc6\u522b", None
            )
        )
        self.groupLLM.setTitle(
            QCoreApplication.translate(
                "ExtractionTab", "LLM \u670d\u52a1\u72b6\u6001", None
            )
        )
        self.labelMLLMStatus.setText(
            QCoreApplication.translate(
                "ExtractionTab", "MLLM: \u25cb \u672a\u914d\u7f6e", None
            )
        )
        self.labelLLMStatus.setText(
            QCoreApplication.translate(
                "ExtractionTab", "LLM: \u25cb \u672a\u914d\u7f6e", None
            )
        )
        self.btnGoToSettings.setText(
            QCoreApplication.translate(
                "ExtractionTab", "\u524d\u5f80\u8bbe\u7f6e\u914d\u7f6e", None
            )
        )
        self.labelResults.setText(
            QCoreApplication.translate(
                "ExtractionTab", "\u62bd\u53d6\u7ed3\u679c", None
            )
        )
        self.btnStart.setText(
            QCoreApplication.translate(
                "ExtractionTab", "\u5f00\u59cb\u62bd\u53d6", None
            )
        )
        self.labelProgress.setText(
            QCoreApplication.translate("ExtractionTab", "0/0", None)
        )
        self.btnCancel.setText(
            QCoreApplication.translate("ExtractionTab", "\u53d6\u6d88", None)
        )
        self.labelExportMode.setText(
            QCoreApplication.translate("ExtractionTab", "\u5bfc\u51fa:", None)
        )
        self.radioExportSeparate.setText(
            QCoreApplication.translate(
                "ExtractionTab", "\u5355\u72ec\u5bfc\u51fa", None
            )
        )
        self.radioExportMerged.setText(
            QCoreApplication.translate(
                "ExtractionTab", "\u5408\u5e76\u5bfc\u51fa", None
            )
        )
        self.labelFormat.setText(
            QCoreApplication.translate("ExtractionTab", "\u683c\u5f0f:", None)
        )
        self.comboFormat.setItemText(
            0, QCoreApplication.translate("ExtractionTab", "JSON", None)
        )
        self.comboFormat.setItemText(
            1, QCoreApplication.translate("ExtractionTab", "Excel", None)
        )

        self.btnExport.setText(
            QCoreApplication.translate(
                "ExtractionTab", "\u5bfc\u51fa\u7ed3\u679c", None
            )
        )

    # retranslateUi

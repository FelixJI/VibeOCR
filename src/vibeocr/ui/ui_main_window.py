# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'main_window.ui'
##
## Created by: Qt User Interface Compiler version 6.11.0
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor,
    QFont, QFontDatabase, QGradient, QIcon,
    QImage, QKeySequence, QLinearGradient, QPainter,
    QPalette, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QFrame,
    QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QSpacerItem, QSpinBox,
    QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
    QTextEdit, QVBoxLayout, QWidget)

from vibeocr.widgets.preview_widget import PreviewWidget

class Ui_MainWindowWidget(object):
    def setupUi(self, MainWindowWidget):
        if not MainWindowWidget.objectName():
            MainWindowWidget.setObjectName(u"MainWindowWidget")
        self.verticalLayout = QVBoxLayout(MainWindowWidget)
        self.verticalLayout.setSpacing(0)
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.verticalLayout.setContentsMargins(0, 0, 0, 0)
        self.mainSplitter = QSplitter(MainWindowWidget)
        self.mainSplitter.setObjectName(u"mainSplitter")
        self.mainSplitter.setOrientation(Qt.Vertical)
        self.tabWidget = QTabWidget(self.mainSplitter)
        self.tabWidget.setObjectName(u"tabWidget")
        self.tabOCR = QWidget()
        self.tabOCR.setObjectName(u"tabOCR")
        self.verticalLayout_ocr = QVBoxLayout(self.tabOCR)
        self.verticalLayout_ocr.setSpacing(0)
        self.verticalLayout_ocr.setObjectName(u"verticalLayout_ocr")
        self.verticalLayout_ocr.setContentsMargins(9, 9, 9, 9)
        self.ocrSplitter = QSplitter(self.tabOCR)
        self.ocrSplitter.setObjectName(u"ocrSplitter")
        self.ocrSplitter.setOrientation(Qt.Horizontal)
        self.previewWidget = PreviewWidget(self.ocrSplitter)
        self.previewWidget.setObjectName(u"previewWidget")
        self.previewWidget.setMinimumSize(QSize(300, 0))
        self.ocrSplitter.addWidget(self.previewWidget)
        self.resultPanel = QWidget(self.ocrSplitter)
        self.resultPanel.setObjectName(u"resultPanel")
        self.resultPanel.setMinimumSize(QSize(300, 0))
        self.verticalLayout_2 = QVBoxLayout(self.resultPanel)
        self.verticalLayout_2.setSpacing(6)
        self.verticalLayout_2.setObjectName(u"verticalLayout_2")
        self.verticalLayout_2.setContentsMargins(0, 0, 0, 0)
        self.headerLayout = QHBoxLayout()
        self.headerLayout.setObjectName(u"headerLayout")
        self.labelResultTitle = QLabel(self.resultPanel)
        self.labelResultTitle.setObjectName(u"labelResultTitle")

        self.headerLayout.addWidget(self.labelResultTitle)

        self.horizontalSpacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.headerLayout.addItem(self.horizontalSpacer)


        self.verticalLayout_2.addLayout(self.headerLayout)

        self.pipelineLayout = QHBoxLayout()
        self.pipelineLayout.setSpacing(2)
        self.pipelineLayout.setObjectName(u"pipelineLayout")
        self.labelPipeline = QLabel(self.resultPanel)
        self.labelPipeline.setObjectName(u"labelPipeline")

        self.pipelineLayout.addWidget(self.labelPipeline)

        self.btnPipelineOCR = QPushButton(self.resultPanel)
        self.btnPipelineOCR.setObjectName(u"btnPipelineOCR")
        self.btnPipelineOCR.setCheckable(True)
        self.btnPipelineOCR.setChecked(True)
        self.btnPipelineOCR.setAutoExclusive(True)

        self.pipelineLayout.addWidget(self.btnPipelineOCR)

        self.btnPipelineTable = QPushButton(self.resultPanel)
        self.btnPipelineTable.setObjectName(u"btnPipelineTable")
        self.btnPipelineTable.setCheckable(True)
        self.btnPipelineTable.setChecked(False)
        self.btnPipelineTable.setAutoExclusive(True)

        self.pipelineLayout.addWidget(self.btnPipelineTable)

        self.btnPipelineFormula = QPushButton(self.resultPanel)
        self.btnPipelineFormula.setObjectName(u"btnPipelineFormula")
        self.btnPipelineFormula.setCheckable(True)
        self.btnPipelineFormula.setChecked(False)
        self.btnPipelineFormula.setAutoExclusive(True)

        self.pipelineLayout.addWidget(self.btnPipelineFormula)

        self.btnPipelineStructure = QPushButton(self.resultPanel)
        self.btnPipelineStructure.setObjectName(u"btnPipelineStructure")
        self.btnPipelineStructure.setCheckable(True)
        self.btnPipelineStructure.setChecked(False)
        self.btnPipelineStructure.setAutoExclusive(True)

        self.pipelineLayout.addWidget(self.btnPipelineStructure)

        self.btnPipelinePaddleOCRVL = QPushButton(self.resultPanel)
        self.btnPipelinePaddleOCRVL.setObjectName(u"btnPipelinePaddleOCRVL")
        self.btnPipelinePaddleOCRVL.setCheckable(True)
        self.btnPipelinePaddleOCRVL.setChecked(False)
        self.btnPipelinePaddleOCRVL.setAutoExclusive(True)

        self.pipelineLayout.addWidget(self.btnPipelinePaddleOCRVL)

        self.horizontalSpacerPipeline = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.pipelineLayout.addItem(self.horizontalSpacerPipeline)


        self.verticalLayout_2.addLayout(self.pipelineLayout)

        self.preprocessLayout = QHBoxLayout()
        self.preprocessLayout.setSpacing(2)
        self.preprocessLayout.setObjectName(u"preprocessLayout")
        self.labelPreprocess = QLabel(self.resultPanel)
        self.labelPreprocess.setObjectName(u"labelPreprocess")

        self.preprocessLayout.addWidget(self.labelPreprocess)

        self.btnOrient = QPushButton(self.resultPanel)
        self.btnOrient.setObjectName(u"btnOrient")
        self.btnOrient.setCheckable(True)
        self.btnOrient.setChecked(False)

        self.preprocessLayout.addWidget(self.btnOrient)

        self.btnUnwarp = QPushButton(self.resultPanel)
        self.btnUnwarp.setObjectName(u"btnUnwarp")
        self.btnUnwarp.setCheckable(True)
        self.btnUnwarp.setChecked(False)

        self.preprocessLayout.addWidget(self.btnUnwarp)

        self.btnTextline = QPushButton(self.resultPanel)
        self.btnTextline.setObjectName(u"btnTextline")
        self.btnTextline.setCheckable(True)
        self.btnTextline.setChecked(True)

        self.preprocessLayout.addWidget(self.btnTextline)

        self.btnLayout = QPushButton(self.resultPanel)
        self.btnLayout.setObjectName(u"btnLayout")
        self.btnLayout.setCheckable(True)
        self.btnLayout.setChecked(False)

        self.preprocessLayout.addWidget(self.btnLayout)

        self.horizontalSpacerPreprocess = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.preprocessLayout.addItem(self.horizontalSpacerPreprocess)


        self.verticalLayout_2.addLayout(self.preprocessLayout)

        self.subPipelineOptions = QWidget(self.resultPanel)
        self.subPipelineOptions.setObjectName(u"subPipelineOptions")
        self.subPipelineOptions.setVisible(False)
        self.subPipelineLayout = QVBoxLayout(self.subPipelineOptions)
        self.subPipelineLayout.setSpacing(4)
        self.subPipelineLayout.setObjectName(u"subPipelineLayout")
        self.subPipelineLayout.setContentsMargins(0, 0, 0, 0)
        self.ppStructureLayout = QHBoxLayout()
        self.ppStructureLayout.setObjectName(u"ppStructureLayout")
        self.labelSubPipelines = QLabel(self.subPipelineOptions)
        self.labelSubPipelines.setObjectName(u"labelSubPipelines")

        self.ppStructureLayout.addWidget(self.labelSubPipelines)

        self.btnSubTable = QPushButton(self.subPipelineOptions)
        self.btnSubTable.setObjectName(u"btnSubTable")
        self.btnSubTable.setCheckable(True)
        self.btnSubTable.setChecked(True)

        self.ppStructureLayout.addWidget(self.btnSubTable)

        self.btnSubFormula = QPushButton(self.subPipelineOptions)
        self.btnSubFormula.setObjectName(u"btnSubFormula")
        self.btnSubFormula.setCheckable(True)
        self.btnSubFormula.setChecked(True)

        self.ppStructureLayout.addWidget(self.btnSubFormula)

        self.btnSubSeal = QPushButton(self.subPipelineOptions)
        self.btnSubSeal.setObjectName(u"btnSubSeal")
        self.btnSubSeal.setCheckable(True)
        self.btnSubSeal.setChecked(False)

        self.ppStructureLayout.addWidget(self.btnSubSeal)

        self.btnSubChart = QPushButton(self.subPipelineOptions)
        self.btnSubChart.setObjectName(u"btnSubChart")
        self.btnSubChart.setCheckable(True)
        self.btnSubChart.setChecked(False)

        self.ppStructureLayout.addWidget(self.btnSubChart)

        self.horizontalSpacerSub = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.ppStructureLayout.addItem(self.horizontalSpacerSub)


        self.subPipelineLayout.addLayout(self.ppStructureLayout)

        self.paddleocrVlLayout = QHBoxLayout()
        self.paddleocrVlLayout.setObjectName(u"paddleocrVlLayout")
        self.labelVlOptions = QLabel(self.subPipelineOptions)
        self.labelVlOptions.setObjectName(u"labelVlOptions")

        self.paddleocrVlLayout.addWidget(self.labelVlOptions)

        self.btnVlLayout = QPushButton(self.subPipelineOptions)
        self.btnVlLayout.setObjectName(u"btnVlLayout")
        self.btnVlLayout.setCheckable(True)
        self.btnVlLayout.setChecked(True)

        self.paddleocrVlLayout.addWidget(self.btnVlLayout)

        self.btnVlChart = QPushButton(self.subPipelineOptions)
        self.btnVlChart.setObjectName(u"btnVlChart")
        self.btnVlChart.setCheckable(True)
        self.btnVlChart.setChecked(False)

        self.paddleocrVlLayout.addWidget(self.btnVlChart)

        self.btnVlSeal = QPushButton(self.subPipelineOptions)
        self.btnVlSeal.setObjectName(u"btnVlSeal")
        self.btnVlSeal.setCheckable(True)
        self.btnVlSeal.setChecked(False)

        self.paddleocrVlLayout.addWidget(self.btnVlSeal)

        self.btnVlFormat = QPushButton(self.subPipelineOptions)
        self.btnVlFormat.setObjectName(u"btnVlFormat")
        self.btnVlFormat.setCheckable(True)
        self.btnVlFormat.setChecked(False)

        self.paddleocrVlLayout.addWidget(self.btnVlFormat)

        self.btnVlOcrImage = QPushButton(self.subPipelineOptions)
        self.btnVlOcrImage.setObjectName(u"btnVlOcrImage")
        self.btnVlOcrImage.setCheckable(True)
        self.btnVlOcrImage.setChecked(False)

        self.paddleocrVlLayout.addWidget(self.btnVlOcrImage)

        self.horizontalSpacerVl = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.paddleocrVlLayout.addItem(self.horizontalSpacerVl)


        self.subPipelineLayout.addLayout(self.paddleocrVlLayout)


        self.verticalLayout_2.addWidget(self.subPipelineOptions)

        self.textResult = QTextEdit(self.resultPanel)
        self.textResult.setObjectName(u"textResult")
        self.textResult.setReadOnly(False)

        self.verticalLayout_2.addWidget(self.textResult)

        self.copyButtonsLayout = QHBoxLayout()
        self.copyButtonsLayout.setSpacing(4)
        self.copyButtonsLayout.setObjectName(u"copyButtonsLayout")
        self.btnCopyRich = QPushButton(self.resultPanel)
        self.btnCopyRich.setObjectName(u"btnCopyRich")

        self.copyButtonsLayout.addWidget(self.btnCopyRich)

        self.btnCopyMarkdown = QPushButton(self.resultPanel)
        self.btnCopyMarkdown.setObjectName(u"btnCopyMarkdown")

        self.copyButtonsLayout.addWidget(self.btnCopyMarkdown)

        self.btnCopyPlain = QPushButton(self.resultPanel)
        self.btnCopyPlain.setObjectName(u"btnCopyPlain")

        self.copyButtonsLayout.addWidget(self.btnCopyPlain)


        self.verticalLayout_2.addLayout(self.copyButtonsLayout)

        self.ocrSplitter.addWidget(self.resultPanel)

        self.verticalLayout_ocr.addWidget(self.ocrSplitter)

        self.tabWidget.addTab(self.tabOCR, "")
        self.tabSettings = QWidget()
        self.tabSettings.setObjectName(u"tabSettings")
        self.verticalLayout_3 = QVBoxLayout(self.tabSettings)
        self.verticalLayout_3.setSpacing(0)
        self.verticalLayout_3.setObjectName(u"verticalLayout_3")
        self.verticalLayout_3.setContentsMargins(0, 0, 0, 0)
        self.scrollAreaSettings = QScrollArea(self.tabSettings)
        self.scrollAreaSettings.setObjectName(u"scrollAreaSettings")
        self.scrollAreaSettings.setWidgetResizable(True)
        self.scrollAreaSettings.setFrameShape(QFrame.NoFrame)
        self.scrollAreaSettingsContent = QWidget()
        self.scrollAreaSettingsContent.setObjectName(u"scrollAreaSettingsContent")
        self.scrollAreaSettingsContent.setGeometry(QRect(0, 0, 100, 100))
        self.scrollContentLayout = QVBoxLayout(self.scrollAreaSettingsContent)
        self.scrollContentLayout.setSpacing(12)
        self.scrollContentLayout.setObjectName(u"scrollContentLayout")
        self.scrollContentLayout.setContentsMargins(16, 16, 16, 16)
        self.groupPreload = QGroupBox(self.scrollAreaSettingsContent)
        self.groupPreload.setObjectName(u"groupPreload")
        self.preloadLayout = QVBoxLayout(self.groupPreload)
        self.preloadLayout.setSpacing(8)
        self.preloadLayout.setObjectName(u"preloadLayout")
        self.chkEnablePreload = QCheckBox(self.groupPreload)
        self.chkEnablePreload.setObjectName(u"chkEnablePreload")
        self.chkEnablePreload.setChecked(True)

        self.preloadLayout.addWidget(self.chkEnablePreload)

        self.preloadOptions = QWidget(self.groupPreload)
        self.preloadOptions.setObjectName(u"preloadOptions")
        self.preloadOptionsLayout = QVBoxLayout(self.preloadOptions)
        self.preloadOptionsLayout.setSpacing(6)
        self.preloadOptionsLayout.setObjectName(u"preloadOptionsLayout")
        self.preloadOptionsLayout.setContentsMargins(20, 0, 0, 0)
        self.labelPreloadPipelines = QLabel(self.preloadOptions)
        self.labelPreloadPipelines.setObjectName(u"labelPreloadPipelines")

        self.preloadOptionsLayout.addWidget(self.labelPreloadPipelines)

        self.preloadPipelinesLayout = QHBoxLayout()
        self.preloadPipelinesLayout.setSpacing(4)
        self.preloadPipelinesLayout.setObjectName(u"preloadPipelinesLayout")
        self.chkPreloadOCR = QCheckBox(self.preloadOptions)
        self.chkPreloadOCR.setObjectName(u"chkPreloadOCR")
        self.chkPreloadOCR.setChecked(True)

        self.preloadPipelinesLayout.addWidget(self.chkPreloadOCR)

        self.chkPreloadTable = QCheckBox(self.preloadOptions)
        self.chkPreloadTable.setObjectName(u"chkPreloadTable")
        self.chkPreloadTable.setChecked(False)

        self.preloadPipelinesLayout.addWidget(self.chkPreloadTable)

        self.chkPreloadFormula = QCheckBox(self.preloadOptions)
        self.chkPreloadFormula.setObjectName(u"chkPreloadFormula")
        self.chkPreloadFormula.setChecked(False)

        self.preloadPipelinesLayout.addWidget(self.chkPreloadFormula)

        self.chkPreloadStructure = QCheckBox(self.preloadOptions)
        self.chkPreloadStructure.setObjectName(u"chkPreloadStructure")
        self.chkPreloadStructure.setChecked(False)

        self.preloadPipelinesLayout.addWidget(self.chkPreloadStructure)

        self.chkPreloadPaddleOCRVL = QCheckBox(self.preloadOptions)
        self.chkPreloadPaddleOCRVL.setObjectName(u"chkPreloadPaddleOCRVL")
        self.chkPreloadPaddleOCRVL.setChecked(False)

        self.preloadPipelinesLayout.addWidget(self.chkPreloadPaddleOCRVL)

        self.horizontalSpacerPreload = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.preloadPipelinesLayout.addItem(self.horizontalSpacerPreload)


        self.preloadOptionsLayout.addLayout(self.preloadPipelinesLayout)

        self.chkParallelPreload = QCheckBox(self.preloadOptions)
        self.chkParallelPreload.setObjectName(u"chkParallelPreload")
        self.chkParallelPreload.setChecked(False)

        self.preloadOptionsLayout.addWidget(self.chkParallelPreload)

        self.parallelOptions = QWidget(self.preloadOptions)
        self.parallelOptions.setObjectName(u"parallelOptions")
        self.parallelOptionsLayout = QHBoxLayout(self.parallelOptions)
        self.parallelOptionsLayout.setSpacing(8)
        self.parallelOptionsLayout.setObjectName(u"parallelOptionsLayout")
        self.parallelOptionsLayout.setContentsMargins(20, 0, 0, 0)
        self.labelMaxWorkers = QLabel(self.parallelOptions)
        self.labelMaxWorkers.setObjectName(u"labelMaxWorkers")

        self.parallelOptionsLayout.addWidget(self.labelMaxWorkers)

        self.spinMaxWorkers = QSpinBox(self.parallelOptions)
        self.spinMaxWorkers.setObjectName(u"spinMaxWorkers")
        self.spinMaxWorkers.setMinimum(1)
        self.spinMaxWorkers.setMaximum(4)
        self.spinMaxWorkers.setValue(2)

        self.parallelOptionsLayout.addWidget(self.spinMaxWorkers)

        self.labelMaxWorkersHint = QLabel(self.parallelOptions)
        self.labelMaxWorkersHint.setObjectName(u"labelMaxWorkersHint")
        self.labelMaxWorkersHint.setForegroundRole(QPalette.PlaceholderText)

        self.parallelOptionsLayout.addWidget(self.labelMaxWorkersHint)

        self.horizontalSpacerWorkers = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.parallelOptionsLayout.addItem(self.horizontalSpacerWorkers)


        self.preloadOptionsLayout.addWidget(self.parallelOptions)


        self.preloadLayout.addWidget(self.preloadOptions)

        self.btnPreloadNow = QPushButton(self.groupPreload)
        self.btnPreloadNow.setObjectName(u"btnPreloadNow")
        self.btnPreloadNow.setMaximumSize(QSize(150, 16777215))

        self.preloadLayout.addWidget(self.btnPreloadNow)


        self.scrollContentLayout.addWidget(self.groupPreload)

        self.groupCache = QGroupBox(self.scrollAreaSettingsContent)
        self.groupCache.setObjectName(u"groupCache")
        self.cacheLayout = QVBoxLayout(self.groupCache)
        self.cacheLayout.setSpacing(8)
        self.cacheLayout.setObjectName(u"cacheLayout")
        self.cacheButtonsLayout = QHBoxLayout()
        self.cacheButtonsLayout.setSpacing(8)
        self.cacheButtonsLayout.setObjectName(u"cacheButtonsLayout")
        self.btnRefreshCache = QPushButton(self.groupCache)
        self.btnRefreshCache.setObjectName(u"btnRefreshCache")

        self.cacheButtonsLayout.addWidget(self.btnRefreshCache)

        self.btnClearCache = QPushButton(self.groupCache)
        self.btnClearCache.setObjectName(u"btnClearCache")

        self.cacheButtonsLayout.addWidget(self.btnClearCache)

        self.horizontalSpacerCache = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.cacheButtonsLayout.addItem(self.horizontalSpacerCache)


        self.cacheLayout.addLayout(self.cacheButtonsLayout)

        self.labelCacheStatus = QLabel(self.groupCache)
        self.labelCacheStatus.setObjectName(u"labelCacheStatus")
        self.labelCacheStatus.setForegroundRole(QPalette.PlaceholderText)

        self.cacheLayout.addWidget(self.labelCacheStatus)


        self.scrollContentLayout.addWidget(self.groupCache)

        self.groupStatus = QGroupBox(self.scrollAreaSettingsContent)
        self.groupStatus.setObjectName(u"groupStatus")
        self.statusLayout = QVBoxLayout(self.groupStatus)
        self.statusLayout.setSpacing(8)
        self.statusLayout.setObjectName(u"statusLayout")
        self.labelPreloadStatus = QLabel(self.groupStatus)
        self.labelPreloadStatus.setObjectName(u"labelPreloadStatus")
        self.labelPreloadStatus.setWordWrap(True)

        self.statusLayout.addWidget(self.labelPreloadStatus)

        self.progressPreload = QProgressBar(self.groupStatus)
        self.progressPreload.setObjectName(u"progressPreload")
        self.progressPreload.setValue(0)
        self.progressPreload.setTextVisible(True)
        self.progressPreload.setVisible(False)

        self.statusLayout.addWidget(self.progressPreload)


        self.scrollContentLayout.addWidget(self.groupStatus)

        self.groupLLMConfig = QGroupBox(self.scrollAreaSettingsContent)
        self.groupLLMConfig.setObjectName(u"groupLLMConfig")
        self.llmConfigLayout = QVBoxLayout(self.groupLLMConfig)
        self.llmConfigLayout.setSpacing(8)
        self.llmConfigLayout.setObjectName(u"llmConfigLayout")
        self.labelLLMHint = QLabel(self.groupLLMConfig)
        self.labelLLMHint.setObjectName(u"labelLLMHint")
        self.labelLLMHint.setForegroundRole(QPalette.PlaceholderText)

        self.llmConfigLayout.addWidget(self.labelLLMHint)

        self.llmGrid = QGridLayout()
        self.llmGrid.setObjectName(u"llmGrid")
        self.llmGrid.setHorizontalSpacing(8)
        self.llmGrid.setVerticalSpacing(6)
        self.labelMLLMUrl = QLabel(self.groupLLMConfig)
        self.labelMLLMUrl.setObjectName(u"labelMLLMUrl")

        self.llmGrid.addWidget(self.labelMLLMUrl, 0, 0, 1, 1)

        self.editMLLMUrl = QLineEdit(self.groupLLMConfig)
        self.editMLLMUrl.setObjectName(u"editMLLMUrl")

        self.llmGrid.addWidget(self.editMLLMUrl, 0, 1, 1, 1)

        self.labelMLLMModel = QLabel(self.groupLLMConfig)
        self.labelMLLMModel.setObjectName(u"labelMLLMModel")

        self.llmGrid.addWidget(self.labelMLLMModel, 1, 0, 1, 1)

        self.editMLLMModel = QLineEdit(self.groupLLMConfig)
        self.editMLLMModel.setObjectName(u"editMLLMModel")

        self.llmGrid.addWidget(self.editMLLMModel, 1, 1, 1, 1)

        self.labelMLLMApiKey = QLabel(self.groupLLMConfig)
        self.labelMLLMApiKey.setObjectName(u"labelMLLMApiKey")

        self.llmGrid.addWidget(self.labelMLLMApiKey, 2, 0, 1, 1)

        self.editMLLMApiKey = QLineEdit(self.groupLLMConfig)
        self.editMLLMApiKey.setObjectName(u"editMLLMApiKey")
        self.editMLLMApiKey.setEchoMode(QLineEdit.Password)

        self.llmGrid.addWidget(self.editMLLMApiKey, 2, 1, 1, 1)

        self.labelLLMUrl = QLabel(self.groupLLMConfig)
        self.labelLLMUrl.setObjectName(u"labelLLMUrl")

        self.llmGrid.addWidget(self.labelLLMUrl, 3, 0, 1, 1)

        self.editLLMUrl = QLineEdit(self.groupLLMConfig)
        self.editLLMUrl.setObjectName(u"editLLMUrl")

        self.llmGrid.addWidget(self.editLLMUrl, 3, 1, 1, 1)

        self.labelLLMModel = QLabel(self.groupLLMConfig)
        self.labelLLMModel.setObjectName(u"labelLLMModel")

        self.llmGrid.addWidget(self.labelLLMModel, 4, 0, 1, 1)

        self.editLLMModel = QLineEdit(self.groupLLMConfig)
        self.editLLMModel.setObjectName(u"editLLMModel")

        self.llmGrid.addWidget(self.editLLMModel, 4, 1, 1, 1)

        self.labelLLMApiKey = QLabel(self.groupLLMConfig)
        self.labelLLMApiKey.setObjectName(u"labelLLMApiKey")

        self.llmGrid.addWidget(self.labelLLMApiKey, 5, 0, 1, 1)

        self.editLLMApiKey = QLineEdit(self.groupLLMConfig)
        self.editLLMApiKey.setObjectName(u"editLLMApiKey")
        self.editLLMApiKey.setEchoMode(QLineEdit.Password)

        self.llmGrid.addWidget(self.editLLMApiKey, 5, 1, 1, 1)


        self.llmConfigLayout.addLayout(self.llmGrid)

        self.llmButtonLayout = QHBoxLayout()
        self.llmButtonLayout.setObjectName(u"llmButtonLayout")
        self.horizontalSpacerLLM = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.llmButtonLayout.addItem(self.horizontalSpacerLLM)

        self.btnSaveLLMConfig = QPushButton(self.groupLLMConfig)
        self.btnSaveLLMConfig.setObjectName(u"btnSaveLLMConfig")

        self.llmButtonLayout.addWidget(self.btnSaveLLMConfig)


        self.llmConfigLayout.addLayout(self.llmButtonLayout)


        self.scrollContentLayout.addWidget(self.groupLLMConfig)

        self.groupTemplate = QGroupBox(self.scrollAreaSettingsContent)
        self.groupTemplate.setObjectName(u"groupTemplate")
        self.templateLayout = QVBoxLayout(self.groupTemplate)
        self.templateLayout.setSpacing(8)
        self.templateLayout.setObjectName(u"templateLayout")
        self.labelTemplateHint = QLabel(self.groupTemplate)
        self.labelTemplateHint.setObjectName(u"labelTemplateHint")
        self.labelTemplateHint.setForegroundRole(QPalette.PlaceholderText)

        self.templateLayout.addWidget(self.labelTemplateHint)

        self.tableTemplates = QTableWidget(self.groupTemplate)
        if (self.tableTemplates.columnCount() < 2):
            self.tableTemplates.setColumnCount(2)
        __qtablewidgetitem = QTableWidgetItem()
        self.tableTemplates.setHorizontalHeaderItem(0, __qtablewidgetitem)
        __qtablewidgetitem1 = QTableWidgetItem()
        self.tableTemplates.setHorizontalHeaderItem(1, __qtablewidgetitem1)
        self.tableTemplates.setObjectName(u"tableTemplates")
        self.tableTemplates.setSelectionBehavior(QAbstractItemView.SelectRows)

        self.templateLayout.addWidget(self.tableTemplates)

        self.templateButtons = QHBoxLayout()
        self.templateButtons.setObjectName(u"templateButtons")
        self.btnAddTemplate = QPushButton(self.groupTemplate)
        self.btnAddTemplate.setObjectName(u"btnAddTemplate")

        self.templateButtons.addWidget(self.btnAddTemplate)

        self.btnEditTemplate = QPushButton(self.groupTemplate)
        self.btnEditTemplate.setObjectName(u"btnEditTemplate")

        self.templateButtons.addWidget(self.btnEditTemplate)

        self.btnDeleteTemplate = QPushButton(self.groupTemplate)
        self.btnDeleteTemplate.setObjectName(u"btnDeleteTemplate")

        self.templateButtons.addWidget(self.btnDeleteTemplate)


        self.templateLayout.addLayout(self.templateButtons)


        self.scrollContentLayout.addWidget(self.groupTemplate)

        self.groupAppSettings = QGroupBox(self.scrollAreaSettingsContent)
        self.groupAppSettings.setObjectName(u"groupAppSettings")
        self.appSettingsLayout = QVBoxLayout(self.groupAppSettings)
        self.appSettingsLayout.setSpacing(8)
        self.appSettingsLayout.setObjectName(u"appSettingsLayout")
        self.chkAutoHideToolbar = QCheckBox(self.groupAppSettings)
        self.chkAutoHideToolbar.setObjectName(u"chkAutoHideToolbar")

        self.appSettingsLayout.addWidget(self.chkAutoHideToolbar)

        self.chkMinimizeToTray = QCheckBox(self.groupAppSettings)
        self.chkMinimizeToTray.setObjectName(u"chkMinimizeToTray")

        self.appSettingsLayout.addWidget(self.chkMinimizeToTray)

        self.chkAutoStart = QCheckBox(self.groupAppSettings)
        self.chkAutoStart.setObjectName(u"chkAutoStart")

        self.appSettingsLayout.addWidget(self.chkAutoStart)


        self.scrollContentLayout.addWidget(self.groupAppSettings)

        self.verticalSpacer = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.scrollContentLayout.addItem(self.verticalSpacer)

        self.scrollAreaSettings.setWidget(self.scrollAreaSettingsContent)

        self.verticalLayout_3.addWidget(self.scrollAreaSettings)

        self.tabWidget.addTab(self.tabSettings, "")
        self.mainSplitter.addWidget(self.tabWidget)
        self.consoleContainer = QWidget(self.mainSplitter)
        self.consoleContainer.setObjectName(u"consoleContainer")
        self.consoleContainer.setMinimumSize(QSize(0, 120))
        self.mainSplitter.addWidget(self.consoleContainer)

        self.verticalLayout.addWidget(self.mainSplitter)


        self.retranslateUi(MainWindowWidget)

        self.tabWidget.setCurrentIndex(0)


        QMetaObject.connectSlotsByName(MainWindowWidget)
    # setupUi

    def retranslateUi(self, MainWindowWidget):
        self.labelResultTitle.setText(QCoreApplication.translate("MainWindowWidget", u"\u8bc6\u522b\u7ed3\u679c", None))
        self.labelPipeline.setText(QCoreApplication.translate("MainWindowWidget", u"\u7ba1\u9053:", None))
#if QT_CONFIG(tooltip)
        self.btnPipelineOCR.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u8bc6\u522b\u56fe\u7247\u4e2d\u7684\u6587\u5b57\u5185\u5bb9", None))
#endif // QT_CONFIG(tooltip)
        self.btnPipelineOCR.setText(QCoreApplication.translate("MainWindowWidget", u"\u901a\u7528", None))
#if QT_CONFIG(tooltip)
        self.btnPipelineTable.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u8bc6\u522b\u8868\u683c\u7ed3\u6784\uff0c\u8f93\u51fa HTML \u683c\u5f0f", None))
#endif // QT_CONFIG(tooltip)
        self.btnPipelineTable.setText(QCoreApplication.translate("MainWindowWidget", u"\u8868\u683c", None))
#if QT_CONFIG(tooltip)
        self.btnPipelineFormula.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u8bc6\u522b\u6570\u5b66\u516c\u5f0f\uff0c\u8f93\u51fa LaTeX \u683c\u5f0f", None))
#endif // QT_CONFIG(tooltip)
        self.btnPipelineFormula.setText(QCoreApplication.translate("MainWindowWidget", u"\u516c\u5f0f", None))
#if QT_CONFIG(tooltip)
        self.btnPipelineStructure.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u89e3\u6790\u6587\u6863\u7248\u9762\uff0c\u652f\u6301\u8868\u683c\u3001\u516c\u5f0f\u7b49\u5b50\u4ea7\u7ebf", None))
#endif // QT_CONFIG(tooltip)
        self.btnPipelineStructure.setText(QCoreApplication.translate("MainWindowWidget", u"\u7248\u9762", None))
#if QT_CONFIG(tooltip)
        self.btnPipelinePaddleOCRVL.setToolTip(QCoreApplication.translate("MainWindowWidget", u"PaddleOCR-VL\uff1a\u7aef\u5230\u7aef\u6587\u6863\u89e3\u6790\uff0c\u652f\u6301\u8868\u683c\u3001\u516c\u5f0f\u3001\u5370\u7ae0\u3001\u56fe\u8868\u7b49\uff08v1.5 \u652f\u6301\u5f02\u5f62\u6846\u5b9a\u4f4d\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.btnPipelinePaddleOCRVL.setText(QCoreApplication.translate("MainWindowWidget", u"PaddleOCR-VL", None))
        self.labelPreprocess.setText(QCoreApplication.translate("MainWindowWidget", u"\u9884\u5904\u7406:", None))
#if QT_CONFIG(tooltip)
        self.btnOrient.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u6587\u6863\u65b9\u5411\u5206\u7c7b\uff1a\u81ea\u52a8\u68c0\u6d4b\u5e76\u77eb\u6b63 0\u00b0/90\u00b0/180\u00b0/270\u00b0 \u65cb\u8f6c", None))
#endif // QT_CONFIG(tooltip)
        self.btnOrient.setText(QCoreApplication.translate("MainWindowWidget", u"\u65b9\u5411", None))
#if QT_CONFIG(tooltip)
        self.btnUnwarp.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u6587\u6863\u53bb\u5f2f\u66f2\uff1a\u77eb\u6b63\u62cd\u6444/\u626b\u63cf\u8fc7\u7a0b\u4e2d\u7684\u51e0\u4f55\u626d\u66f2", None))
#endif // QT_CONFIG(tooltip)
        self.btnUnwarp.setText(QCoreApplication.translate("MainWindowWidget", u"\u53bb\u5f2f", None))
#if QT_CONFIG(tooltip)
        self.btnTextline.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u6587\u672c\u884c\u65b9\u5411\uff1a\u68c0\u6d4b\u5e76\u77eb\u6b63\u6587\u672c\u884c\u7684\u65cb\u8f6c\u89d2\u5ea6\uff08\u4ec5\u901a\u7528 OCR\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.btnTextline.setText(QCoreApplication.translate("MainWindowWidget", u"\u884c\u5411", None))
#if QT_CONFIG(tooltip)
        self.btnLayout.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u7248\u9762\u68c0\u6d4b\uff1a\u68c0\u6d4b\u8868\u683c/\u516c\u5f0f\u533a\u57df\uff08\u4ec5\u8868\u683c\u548c\u516c\u5f0f\u7ba1\u9053\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.btnLayout.setText(QCoreApplication.translate("MainWindowWidget", u"\u7248\u9762", None))
        self.labelSubPipelines.setText(QCoreApplication.translate("MainWindowWidget", u"\u5b50\u4ea7\u7ebf:", None))
#if QT_CONFIG(tooltip)
        self.btnSubTable.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u542f\u7528\u8868\u683c\u8bc6\u522b\u5b50\u4ea7\u7ebf", None))
#endif // QT_CONFIG(tooltip)
        self.btnSubTable.setText(QCoreApplication.translate("MainWindowWidget", u"\u8868\u683c", None))
#if QT_CONFIG(tooltip)
        self.btnSubFormula.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u542f\u7528\u516c\u5f0f\u8bc6\u522b\u5b50\u4ea7\u7ebf", None))
#endif // QT_CONFIG(tooltip)
        self.btnSubFormula.setText(QCoreApplication.translate("MainWindowWidget", u"\u516c\u5f0f", None))
#if QT_CONFIG(tooltip)
        self.btnSubSeal.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u542f\u7528\u5370\u7ae0\u8bc6\u522b\u5b50\u4ea7\u7ebf", None))
#endif // QT_CONFIG(tooltip)
        self.btnSubSeal.setText(QCoreApplication.translate("MainWindowWidget", u"\u5370\u7ae0", None))
#if QT_CONFIG(tooltip)
        self.btnSubChart.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u542f\u7528\u56fe\u8868\u8bc6\u522b\u5b50\u4ea7\u7ebf", None))
#endif // QT_CONFIG(tooltip)
        self.btnSubChart.setText(QCoreApplication.translate("MainWindowWidget", u"\u56fe\u8868", None))
        self.labelVlOptions.setText(QCoreApplication.translate("MainWindowWidget", u"V-L \u9009\u9879:", None))
#if QT_CONFIG(tooltip)
        self.btnVlLayout.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u542f\u7528\u7248\u9762\u533a\u57df\u68c0\u6d4b\u6392\u5e8f\uff08\u9ed8\u8ba4\u5f00\u542f\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.btnVlLayout.setText(QCoreApplication.translate("MainWindowWidget", u"\u7248\u9762\u68c0\u6d4b", None))
#if QT_CONFIG(tooltip)
        self.btnVlChart.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u542f\u7528\u56fe\u8868\u89e3\u6790\u529f\u80fd", None))
#endif // QT_CONFIG(tooltip)
        self.btnVlChart.setText(QCoreApplication.translate("MainWindowWidget", u"\u56fe\u8868", None))
#if QT_CONFIG(tooltip)
        self.btnVlSeal.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u542f\u7528\u5370\u7ae0\u8bc6\u522b\u529f\u80fd\uff08v1.5 \u65b0\u589e\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.btnVlSeal.setText(QCoreApplication.translate("MainWindowWidget", u"\u5370\u7ae0", None))
#if QT_CONFIG(tooltip)
        self.btnVlFormat.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u5c06\u7ed3\u679c\u683c\u5f0f\u5316\u4e3a Markdown \u683c\u5f0f", None))
#endif // QT_CONFIG(tooltip)
        self.btnVlFormat.setText(QCoreApplication.translate("MainWindowWidget", u"\u683c\u5f0f\u5316", None))
#if QT_CONFIG(tooltip)
        self.btnVlOcrImage.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u5bf9\u56fe\u7247\u5757\u4e2d\u7684\u6587\u5b57\u8fdb\u884c OCR \u8bc6\u522b", None))
#endif // QT_CONFIG(tooltip)
        self.btnVlOcrImage.setText(QCoreApplication.translate("MainWindowWidget", u"\u56fe\u5185 OCR", None))
        self.textResult.setPlaceholderText(QCoreApplication.translate("MainWindowWidget", u"\u8bc6\u522b\u7ed3\u679c\u5c06\u663e\u793a\u5728\u8fd9\u91cc...", None))
#if QT_CONFIG(tooltip)
        self.btnCopyRich.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u590d\u5236\u4e3a\u5bcc\u6587\u672c\u683c\u5f0f\uff0c\u53ef\u7c98\u8d34\u5230 Word/Excel \u4fdd\u7559\u8868\u683c\u683c\u5f0f", None))
#endif // QT_CONFIG(tooltip)
        self.btnCopyRich.setText(QCoreApplication.translate("MainWindowWidget", u"\u5bcc\u6587\u672c", None))
#if QT_CONFIG(tooltip)
        self.btnCopyMarkdown.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u590d\u5236\u4e3a Markdown \u683c\u5f0f\uff0c\u4fdd\u7559\u8868\u683c\u548c\u516c\u5f0f\u7ed3\u6784", None))
#endif // QT_CONFIG(tooltip)
        self.btnCopyMarkdown.setText(QCoreApplication.translate("MainWindowWidget", u"Markdown", None))
#if QT_CONFIG(tooltip)
        self.btnCopyPlain.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u590d\u5236\u4e3a\u7eaf\u6587\u672c\u683c\u5f0f", None))
#endif // QT_CONFIG(tooltip)
        self.btnCopyPlain.setText(QCoreApplication.translate("MainWindowWidget", u"\u7eaf\u6587\u672c", None))
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.tabOCR), QCoreApplication.translate("MainWindowWidget", u"OCR \u8bc6\u522b", None))
        self.groupPreload.setTitle(QCoreApplication.translate("MainWindowWidget", u"\u6a21\u578b\u9884\u52a0\u8f7d", None))
#if QT_CONFIG(tooltip)
        self.chkEnablePreload.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u542f\u52a8\u5e94\u7528\u65f6\u81ea\u52a8\u9884\u52a0\u8f7d\u9009\u4e2d\u7684\u7ba1\u9053\uff0c\u9996\u6b21\u8bc6\u522b\u65f6\u65e0\u9700\u7b49\u5f85", None))
#endif // QT_CONFIG(tooltip)
        self.chkEnablePreload.setText(QCoreApplication.translate("MainWindowWidget", u"\u542f\u52a8\u65f6\u81ea\u52a8\u9884\u52a0\u8f7d\u6a21\u578b", None))
        self.labelPreloadPipelines.setText(QCoreApplication.translate("MainWindowWidget", u"\u9884\u52a0\u8f7d\u7ba1\u9053:", None))
#if QT_CONFIG(tooltip)
        self.chkPreloadOCR.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u901a\u7528 OCR \u7ba1\u9053\uff08\u7ea6 600MB \u663e\u5b58\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.chkPreloadOCR.setText(QCoreApplication.translate("MainWindowWidget", u"\u901a\u7528 OCR", None))
#if QT_CONFIG(tooltip)
        self.chkPreloadTable.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u8868\u683c\u8bc6\u522b\u7ba1\u9053", None))
#endif // QT_CONFIG(tooltip)
        self.chkPreloadTable.setText(QCoreApplication.translate("MainWindowWidget", u"\u8868\u683c", None))
#if QT_CONFIG(tooltip)
        self.chkPreloadFormula.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u516c\u5f0f\u8bc6\u522b\u7ba1\u9053", None))
#endif // QT_CONFIG(tooltip)
        self.chkPreloadFormula.setText(QCoreApplication.translate("MainWindowWidget", u"\u516c\u5f0f", None))
#if QT_CONFIG(tooltip)
        self.chkPreloadStructure.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u7248\u9762\u89e3\u6790\u7ba1\u9053\uff08\u5305\u542b 15+ \u6a21\u578b\uff0c\u7ea6 2-3GB \u663e\u5b58\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.chkPreloadStructure.setText(QCoreApplication.translate("MainWindowWidget", u"\u7248\u9762", None))
#if QT_CONFIG(tooltip)
        self.chkPreloadPaddleOCRVL.setToolTip(QCoreApplication.translate("MainWindowWidget", u"PaddleOCR-VL \u7aef\u5230\u7aef\u6587\u6863\u89e3\u6790\uff08v1.5 \u652f\u6301\u5f02\u5f62\u6846\u5b9a\u4f4d\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.chkPreloadPaddleOCRVL.setText(QCoreApplication.translate("MainWindowWidget", u"PaddleOCR-VL", None))
#if QT_CONFIG(tooltip)
        self.chkParallelPreload.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u5e76\u884c\u52a0\u8f7d\u591a\u4e2a\u7ba1\u9053\u53ef\u52a0\u5feb\u901f\u5ea6\uff0c\u4f46\u4f1a\u589e\u52a0\u5cf0\u503c\u663e\u5b58\u5360\u7528", None))
#endif // QT_CONFIG(tooltip)
        self.chkParallelPreload.setText(QCoreApplication.translate("MainWindowWidget", u"\u5e76\u884c\u52a0\u8f7d\uff08\u52a0\u5feb\u901f\u5ea6\uff0c\u589e\u52a0\u663e\u5b58\uff09", None))
        self.labelMaxWorkers.setText(QCoreApplication.translate("MainWindowWidget", u"\u5e76\u884c\u6570\u91cf:", None))
#if QT_CONFIG(tooltip)
        self.spinMaxWorkers.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u540c\u65f6\u52a0\u8f7d\u7684\u7ba1\u9053\u6570\u91cf\uff0c\u5efa\u8bae\u4e0d\u8d85\u8fc7 2", None))
#endif // QT_CONFIG(tooltip)
        self.labelMaxWorkersHint.setText(QCoreApplication.translate("MainWindowWidget", u"(\u5efa\u8bae\u4e0d\u8d85\u8fc7 2)", None))
#if QT_CONFIG(tooltip)
        self.btnPreloadNow.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u7acb\u5373\u9884\u52a0\u8f7d\u9009\u4e2d\u7684\u7ba1\u9053", None))
#endif // QT_CONFIG(tooltip)
        self.btnPreloadNow.setText(QCoreApplication.translate("MainWindowWidget", u"\u7acb\u5373\u9884\u52a0\u8f7d", None))
        self.groupCache.setTitle(QCoreApplication.translate("MainWindowWidget", u"\u7f13\u5b58\u7ba1\u7406", None))
#if QT_CONFIG(tooltip)
        self.btnRefreshCache.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u91cd\u65b0\u626b\u63cf\u6a21\u578b\u7f13\u5b58\u72b6\u6001", None))
#endif // QT_CONFIG(tooltip)
        self.btnRefreshCache.setText(QCoreApplication.translate("MainWindowWidget", u"\u5237\u65b0\u7f13\u5b58\u72b6\u6001", None))
#if QT_CONFIG(tooltip)
        self.btnClearCache.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u6e05\u9664\u4f9d\u8d56\u68c0\u6d4b\u7f13\u5b58\uff08\u4e0d\u5f71\u54cd\u5df2\u4e0b\u8f7d\u7684\u6a21\u578b\uff09", None))
#endif // QT_CONFIG(tooltip)
        self.btnClearCache.setText(QCoreApplication.translate("MainWindowWidget", u"\u6e05\u9664\u7f13\u5b58", None))
        self.labelCacheStatus.setText(QCoreApplication.translate("MainWindowWidget", u"\u7f13\u5b58\u72b6\u6001: \u672a\u77e5", None))
        self.groupStatus.setTitle(QCoreApplication.translate("MainWindowWidget", u"\u9884\u52a0\u8f7d\u72b6\u6001", None))
        self.labelPreloadStatus.setText(QCoreApplication.translate("MainWindowWidget", u"\u5c1a\u672a\u9884\u52a0\u8f7d", None))
        self.groupLLMConfig.setTitle(QCoreApplication.translate("MainWindowWidget", u"LLM \u670d\u52a1\u914d\u7f6e", None))
        self.labelLLMHint.setText(QCoreApplication.translate("MainWindowWidget", u"\u914d\u7f6e LLM \u670d\u52a1\u4ee5\u542f\u7528\u4fe1\u606f\u62bd\u53d6\u529f\u80fd", None))
        self.labelMLLMUrl.setText(QCoreApplication.translate("MainWindowWidget", u"MLLM \u670d\u52a1\u5730\u5740:", None))
        self.editMLLMUrl.setPlaceholderText(QCoreApplication.translate("MainWindowWidget", u"http://127.0.0.1:8080/v1/chat/completions", None))
        self.labelMLLMModel.setText(QCoreApplication.translate("MainWindowWidget", u"MLLM \u6a21\u578b\u540d\u79f0:", None))
        self.editMLLMModel.setPlaceholderText(QCoreApplication.translate("MainWindowWidget", u"PP-DocBee2", None))
        self.labelMLLMApiKey.setText(QCoreApplication.translate("MainWindowWidget", u"MLLM API Key:", None))
        self.editMLLMApiKey.setPlaceholderText(QCoreApplication.translate("MainWindowWidget", u"\u53ef\u9009", None))
        self.labelLLMUrl.setText(QCoreApplication.translate("MainWindowWidget", u"LLM \u670d\u52a1\u5730\u5740:", None))
        self.editLLMUrl.setPlaceholderText(QCoreApplication.translate("MainWindowWidget", u"http://127.0.0.1:8080/v1/chat/completions", None))
        self.labelLLMModel.setText(QCoreApplication.translate("MainWindowWidget", u"LLM \u6a21\u578b\u540d\u79f0:", None))
        self.editLLMModel.setPlaceholderText(QCoreApplication.translate("MainWindowWidget", u"Qwen2.5", None))
        self.labelLLMApiKey.setText(QCoreApplication.translate("MainWindowWidget", u"LLM API Key:", None))
        self.editLLMApiKey.setPlaceholderText(QCoreApplication.translate("MainWindowWidget", u"\u53ef\u9009", None))
        self.btnSaveLLMConfig.setText(QCoreApplication.translate("MainWindowWidget", u"\u4fdd\u5b58\u914d\u7f6e", None))
        self.groupTemplate.setTitle(QCoreApplication.translate("MainWindowWidget", u"\u62bd\u53d6\u6a21\u677f\u7ba1\u7406", None))
        self.labelTemplateHint.setText(QCoreApplication.translate("MainWindowWidget", u"\u7ba1\u7406\u5e38\u7528\u7684\u62bd\u53d6\u5b57\u6bb5\u6a21\u677f", None))
        ___qtablewidgetitem = self.tableTemplates.horizontalHeaderItem(0)
        ___qtablewidgetitem.setText(QCoreApplication.translate("MainWindowWidget", u"\u6a21\u677f\u540d\u79f0", None))
        ___qtablewidgetitem1 = self.tableTemplates.horizontalHeaderItem(1)
        ___qtablewidgetitem1.setText(QCoreApplication.translate("MainWindowWidget", u"\u5b57\u6bb5\u5217\u8868", None))
        self.btnAddTemplate.setText(QCoreApplication.translate("MainWindowWidget", u"\u6dfb\u52a0\u6a21\u677f", None))
        self.btnEditTemplate.setText(QCoreApplication.translate("MainWindowWidget", u"\u7f16\u8f91\u6a21\u677f", None))
        self.btnDeleteTemplate.setText(QCoreApplication.translate("MainWindowWidget", u"\u5220\u9664\u6a21\u677f", None))
        self.groupAppSettings.setTitle(QCoreApplication.translate("MainWindowWidget", u"\u5e94\u7528\u8bbe\u7f6e", None))
        self.chkAutoHideToolbar.setText(QCoreApplication.translate("MainWindowWidget", u"\u81ea\u52a8\u9690\u85cf\u8fb9\u7f18\u5de5\u5177\u680f", None))
#if QT_CONFIG(tooltip)
        self.chkAutoHideToolbar.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u5de5\u5177\u680f\u505c\u9760\u5728\u5c4f\u5e55\u8fb9\u7f18\u65f6\u81ea\u52a8\u9690\u85cf\uff0c\u9f20\u6807\u9760\u8fd1\u8fb9\u7f18\u65f6\u81ea\u52a8\u5f39\u51fa", None))
#endif // QT_CONFIG(tooltip)
        self.chkMinimizeToTray.setText(QCoreApplication.translate("MainWindowWidget", u"\u6700\u5c0f\u5316\u5230\u7cfb\u7edf\u6258\u76d8", None))
#if QT_CONFIG(tooltip)
        self.chkMinimizeToTray.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u5173\u95ed\u4e3b\u7a97\u53e3\u65f6\u6700\u5c0f\u5316\u5230\u7cfb\u7edf\u6258\u76d8\u800c\u4e0d\u662f\u9000\u51fa\u7a0b\u5e8f", None))
#endif // QT_CONFIG(tooltip)
        self.chkAutoStart.setText(QCoreApplication.translate("MainWindowWidget", u"\u5f00\u673a\u81ea\u542f\u52a8", None))
#if QT_CONFIG(tooltip)
        self.chkAutoStart.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u7cfb\u7edf\u542f\u52a8\u65f6\u81ea\u52a8\u8fd0\u884c VibeOCR", None))
#endif // QT_CONFIG(tooltip)
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.tabSettings), QCoreApplication.translate("MainWindowWidget", u"\u8bbe\u7f6e", None))
        pass
    # retranslateUi


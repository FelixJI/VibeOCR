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
    QGroupBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QProgressBar, QPushButton, QSizePolicy,
    QSpacerItem, QSpinBox, QSplitter, QStackedWidget,
    QTabWidget, QVBoxLayout, QWidget)

from vibeocr.widgets.preview_widget import PreviewWidget

class Ui_MainWindowWidget(object):
    def setupUi(self, MainWindowWidget):
        if not MainWindowWidget.objectName():
            MainWindowWidget.setObjectName(u"MainWindowWidget")
        self.verticalLayout = QVBoxLayout(MainWindowWidget)
        self.verticalLayout.setSpacing(0)
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.verticalLayout.setContentsMargins(0, 0, 0, 0)
        self.tabWidget = QTabWidget(MainWindowWidget)
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
        self.settingsHLayout = QHBoxLayout(self.tabSettings)
        self.settingsHLayout.setSpacing(0)
        self.settingsHLayout.setObjectName(u"settingsHLayout")
        self.settingsHLayout.setContentsMargins(0, 0, 0, 0)
        self.settingsNavList = QListWidget(self.tabSettings)
        QListWidgetItem(self.settingsNavList)
        QListWidgetItem(self.settingsNavList)
        QListWidgetItem(self.settingsNavList)
        self.settingsNavList.setObjectName(u"settingsNavList")
        self.settingsNavList.setMaximumSize(QSize(160, 16777215))
        self.settingsNavList.setFrameShape(QFrame.NoFrame)
        self.settingsNavList.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.settingsNavList.setEditTriggers(QAbstractItemView.NoEditTriggers)

        self.settingsHLayout.addWidget(self.settingsNavList)

        self.settingsStackedWidget = QStackedWidget(self.tabSettings)
        self.settingsStackedWidget.setObjectName(u"settingsStackedWidget")
        self.pageModelManagement = QWidget()
        self.pageModelManagement.setObjectName(u"pageModelManagement")
        self.pageModelLayout = QVBoxLayout(self.pageModelManagement)
        self.pageModelLayout.setSpacing(12)
        self.pageModelLayout.setObjectName(u"pageModelLayout")
        self.pageModelLayout.setContentsMargins(16, 16, 16, 16)
        self.groupPreload = QGroupBox(self.pageModelManagement)
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

        self.horizontalSpacerPreload = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.preloadPipelinesLayout.addItem(self.horizontalSpacerPreload)


        self.preloadOptionsLayout.addLayout(self.preloadPipelinesLayout)


        self.preloadLayout.addWidget(self.preloadOptions)

        self.btnPreloadNow = QPushButton(self.groupPreload)
        self.btnPreloadNow.setObjectName(u"btnPreloadNow")
        self.btnPreloadNow.setMaximumSize(QSize(150, 16777215))

        self.preloadLayout.addWidget(self.btnPreloadNow)

        self.labelPreloadStatus = QLabel(self.groupPreload)
        self.labelPreloadStatus.setObjectName(u"labelPreloadStatus")
        self.labelPreloadStatus.setWordWrap(True)

        self.preloadLayout.addWidget(self.labelPreloadStatus)

        self.progressPreload = QProgressBar(self.groupPreload)
        self.progressPreload.setObjectName(u"progressPreload")
        self.progressPreload.setValue(0)
        self.progressPreload.setTextVisible(True)
        self.progressPreload.setVisible(False)

        self.preloadLayout.addWidget(self.progressPreload)


        self.pageModelLayout.addWidget(self.groupPreload)

        self.groupCache = QGroupBox(self.pageModelManagement)
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


        self.pageModelLayout.addWidget(self.groupCache)

        self.spacerModelPage = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.pageModelLayout.addItem(self.spacerModelPage)

        self.settingsStackedWidget.addWidget(self.pageModelManagement)
        self.pageAppSettings = QWidget()
        self.pageAppSettings.setObjectName(u"pageAppSettings")
        self.pageAppLayout = QVBoxLayout(self.pageAppSettings)
        self.pageAppLayout.setSpacing(12)
        self.pageAppLayout.setObjectName(u"pageAppLayout")
        self.pageAppLayout.setContentsMargins(16, 16, 16, 16)
        self.groupAppSettings = QGroupBox(self.pageAppSettings)
        self.groupAppSettings.setObjectName(u"groupAppSettings")
        self.appSettingsLayout = QVBoxLayout(self.groupAppSettings)
        self.appSettingsLayout.setSpacing(8)
        self.appSettingsLayout.setObjectName(u"appSettingsLayout")
        self.chkShowToolbar = QCheckBox(self.groupAppSettings)
        self.chkShowToolbar.setObjectName(u"chkShowToolbar")

        self.appSettingsLayout.addWidget(self.chkShowToolbar)

        self.toolbarSubOptions = QWidget(self.groupAppSettings)
        self.toolbarSubOptions.setObjectName(u"toolbarSubOptions")
        self.toolbarSubLayout = QVBoxLayout(self.toolbarSubOptions)
        self.toolbarSubLayout.setSpacing(8)
        self.toolbarSubLayout.setObjectName(u"toolbarSubLayout")
        self.toolbarSubLayout.setContentsMargins(20, 0, 0, 0)
        self.chkAutoHideToolbar = QCheckBox(self.toolbarSubOptions)
        self.chkAutoHideToolbar.setObjectName(u"chkAutoHideToolbar")

        self.toolbarSubLayout.addWidget(self.chkAutoHideToolbar)

        self.hideDelayLayout = QHBoxLayout()
        self.hideDelayLayout.setSpacing(8)
        self.hideDelayLayout.setObjectName(u"hideDelayLayout")
        self.labelHideDelay = QLabel(self.toolbarSubOptions)
        self.labelHideDelay.setObjectName(u"labelHideDelay")

        self.hideDelayLayout.addWidget(self.labelHideDelay)

        self.spinHideDelay = QSpinBox(self.toolbarSubOptions)
        self.spinHideDelay.setObjectName(u"spinHideDelay")
        self.spinHideDelay.setMinimum(100)
        self.spinHideDelay.setMaximum(5000)
        self.spinHideDelay.setSingleStep(100)
        self.spinHideDelay.setValue(500)

        self.hideDelayLayout.addWidget(self.spinHideDelay)

        self.hideDelaySpacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.hideDelayLayout.addItem(self.hideDelaySpacer)


        self.toolbarSubLayout.addLayout(self.hideDelayLayout)


        self.appSettingsLayout.addWidget(self.toolbarSubOptions)

        self.chkMinimizeToTray = QCheckBox(self.groupAppSettings)
        self.chkMinimizeToTray.setObjectName(u"chkMinimizeToTray")

        self.appSettingsLayout.addWidget(self.chkMinimizeToTray)

        self.chkAutoStart = QCheckBox(self.groupAppSettings)
        self.chkAutoStart.setObjectName(u"chkAutoStart")

        self.appSettingsLayout.addWidget(self.chkAutoStart)


        self.pageAppLayout.addWidget(self.groupAppSettings)

        self.spacerAppPage = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.pageAppLayout.addItem(self.spacerAppPage)

        self.settingsStackedWidget.addWidget(self.pageAppSettings)
        self.pageTools = QWidget()
        self.pageTools.setObjectName(u"pageTools")
        self.pageToolsLayout = QVBoxLayout(self.pageTools)
        self.pageToolsLayout.setSpacing(12)
        self.pageToolsLayout.setObjectName(u"pageToolsLayout")
        self.pageToolsLayout.setContentsMargins(16, 16, 16, 16)
        self.groupModelDownload = QGroupBox(self.pageTools)
        self.groupModelDownload.setObjectName(u"groupModelDownload")
        self.modelDownloadLayout = QVBoxLayout(self.groupModelDownload)
        self.modelDownloadLayout.setSpacing(8)
        self.modelDownloadLayout.setObjectName(u"modelDownloadLayout")
        self.btnDownloadModels = QPushButton(self.groupModelDownload)
        self.btnDownloadModels.setObjectName(u"btnDownloadModels")
        self.btnDownloadModels.setMaximumSize(QSize(150, 16777215))

        self.modelDownloadLayout.addWidget(self.btnDownloadModels)

        self.labelModelDownloadHint = QLabel(self.groupModelDownload)
        self.labelModelDownloadHint.setObjectName(u"labelModelDownloadHint")
        self.labelModelDownloadHint.setWordWrap(True)

        self.modelDownloadLayout.addWidget(self.labelModelDownloadHint)


        self.pageToolsLayout.addWidget(self.groupModelDownload)

        self.spacerToolsPage = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.pageToolsLayout.addItem(self.spacerToolsPage)

        self.settingsStackedWidget.addWidget(self.pageTools)

        self.settingsHLayout.addWidget(self.settingsStackedWidget)

        self.tabWidget.addTab(self.tabSettings, "")

        self.verticalLayout.addWidget(self.tabWidget)


        self.retranslateUi(MainWindowWidget)

        self.tabWidget.setCurrentIndex(0)
        self.settingsNavList.setCurrentRow(0)
        self.settingsStackedWidget.setCurrentIndex(0)


        QMetaObject.connectSlotsByName(MainWindowWidget)
    # setupUi

    def retranslateUi(self, MainWindowWidget):
        self.labelResultTitle.setText(QCoreApplication.translate("MainWindowWidget", u"\u8bc6\u522b\u7ed3\u679c", None))
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
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.tabOCR), QCoreApplication.translate("MainWindowWidget", u"\u5355\u6b21\u8bc6\u522b", None))

        __sortingEnabled = self.settingsNavList.isSortingEnabled()
        self.settingsNavList.setSortingEnabled(False)
        ___qlistwidgetitem = self.settingsNavList.item(0)
        ___qlistwidgetitem.setText(QCoreApplication.translate("MainWindowWidget", u"\u6a21\u578b\u7ba1\u7406", None))
        ___qlistwidgetitem1 = self.settingsNavList.item(1)
        ___qlistwidgetitem1.setText(QCoreApplication.translate("MainWindowWidget", u"\u5e94\u7528\u8bbe\u7f6e", None))
        ___qlistwidgetitem2 = self.settingsNavList.item(2)
        ___qlistwidgetitem2.setText(QCoreApplication.translate("MainWindowWidget", u"\u5de5\u5177", None))
        self.settingsNavList.setSortingEnabled(__sortingEnabled)

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
        self.btnPreloadNow.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u7acb\u5373\u9884\u52a0\u8f7d\u9009\u4e2d\u7684\u7ba1\u9053", None))
#endif // QT_CONFIG(tooltip)
        self.btnPreloadNow.setText(QCoreApplication.translate("MainWindowWidget", u"\u7acb\u5373\u9884\u52a0\u8f7d", None))
        self.labelPreloadStatus.setText(QCoreApplication.translate("MainWindowWidget", u"\u5c1a\u672a\u9884\u52a0\u8f7d", None))
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
        self.groupAppSettings.setTitle(QCoreApplication.translate("MainWindowWidget", u"\u5e94\u7528\u8bbe\u7f6e", None))
#if QT_CONFIG(tooltip)
        self.chkShowToolbar.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u663e\u793a\u684c\u9762\u8fb9\u7f18\u6d6e\u52a8\u5de5\u5177\u680f\uff0c\u63d0\u4f9b\u5feb\u901f\u622a\u56fe\u548c\u4e3b\u7a97\u53e3\u5165\u53e3", None))
#endif // QT_CONFIG(tooltip)
        self.chkShowToolbar.setText(QCoreApplication.translate("MainWindowWidget", u"\u663e\u793a\u8fb9\u7f18\u5de5\u5177\u680f", None))
#if QT_CONFIG(tooltip)
        self.chkAutoHideToolbar.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u5de5\u5177\u680f\u505c\u9760\u5728\u5c4f\u5e55\u8fb9\u7f18\u65f6\u81ea\u52a8\u9690\u85cf\uff0c\u9f20\u6807\u9760\u8fd1\u8fb9\u7f18\u65f6\u81ea\u52a8\u5f39\u51fa", None))
#endif // QT_CONFIG(tooltip)
        self.chkAutoHideToolbar.setText(QCoreApplication.translate("MainWindowWidget", u"\u81ea\u52a8\u9690\u85cf", None))
        self.labelHideDelay.setText(QCoreApplication.translate("MainWindowWidget", u"\u9690\u85cf\u5ef6\u8fdf:", None))
        self.spinHideDelay.setSuffix(QCoreApplication.translate("MainWindowWidget", u" \u6beb\u79d2", None))
#if QT_CONFIG(tooltip)
        self.chkMinimizeToTray.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u5173\u95ed\u4e3b\u7a97\u53e3\u65f6\u6700\u5c0f\u5316\u5230\u7cfb\u7edf\u6258\u76d8\u800c\u4e0d\u662f\u9000\u51fa\u7a0b\u5e8f", None))
#endif // QT_CONFIG(tooltip)
        self.chkMinimizeToTray.setText(QCoreApplication.translate("MainWindowWidget", u"\u6700\u5c0f\u5316\u5230\u7cfb\u7edf\u6258\u76d8", None))
#if QT_CONFIG(tooltip)
        self.chkAutoStart.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u7cfb\u7edf\u542f\u52a8\u65f6\u81ea\u52a8\u8fd0\u884c VibeOCR", None))
#endif // QT_CONFIG(tooltip)
        self.chkAutoStart.setText(QCoreApplication.translate("MainWindowWidget", u"\u5f00\u673a\u81ea\u542f\u52a8", None))
        self.groupModelDownload.setTitle(QCoreApplication.translate("MainWindowWidget", u"\u6a21\u578b\u4e0b\u8f7d", None))
#if QT_CONFIG(tooltip)
        self.btnDownloadModels.setToolTip(QCoreApplication.translate("MainWindowWidget", u"\u4e0b\u8f7d\u6216\u66f4\u65b0 OCR \u6a21\u578b\u6587\u4ef6", None))
#endif // QT_CONFIG(tooltip)
        self.btnDownloadModels.setText(QCoreApplication.translate("MainWindowWidget", u"\u4e0b\u8f7d\u6a21\u578b", None))
        self.labelModelDownloadHint.setText(QCoreApplication.translate("MainWindowWidget", u"\u4e0b\u8f7d\u6216\u66f4\u65b0 OCR \u6a21\u578b\u6587\u4ef6\uff08PaddleOCR\u3001\u8868\u683c\u8bc6\u522b\u3001\u516c\u5f0f\u8bc6\u522b\u7b49\uff09", None))
        self.tabWidget.setTabText(self.tabWidget.indexOf(self.tabSettings), QCoreApplication.translate("MainWindowWidget", u"\u8bbe\u7f6e", None))
        pass
    # retranslateUi


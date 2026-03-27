"""文档理解标签页

提供基于 PaddleX doc_understanding 管道的对话式文档问答功能。
"""

import logging
from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from vibeocr.ui.ui_doc_understanding_tab import Ui_DocUnderstandingTab
from vibeocr.views.tabs.base_tab import BaseOcrTab
from vibeocr.widgets.chat_widget import ChatWidget
from vibeocr.workers.doc_understanding_worker import DocUnderstandingWorker

logger = logging.getLogger(__name__)


class DocUnderstandingTab(BaseOcrTab):
    """文档理解标签页

    继承自 BaseOcrTab，提供基于 PaddleX doc_understanding 管道的对话式文档问答功能。

    布局：
    - 顶部：模型选择 + 状态显示
    - 左侧：单选文件列表
    - 右侧：文档预览 + 聊天对话区
    """

    # 信号
    status_changed = Signal(str)  # 状态变化

    # 支持的图片格式
    SUPPORTED_FORMATS = [".png", ".jpg", ".jpeg", ".bmp", ".pdf", ".tiff", ".webp"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ocr_service = None
        self._worker: DocUnderstandingWorker | None = None
        self._current_file: str | None = None
        self._conversation_history: dict[str, list[dict]] = {}  # 文件路径 -> 对话历史

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        """设置 UI"""
        self._ui = Ui_DocUnderstandingTab()
        self._ui.setupUi(self)

        # 添加 ChatWidget 到对话容器
        self._chat_widget = ChatWidget()
        container = self.findChild(QWidget, "chatContainer")
        if container:
            container_layout = container.layout()
            if not container_layout:
                container_layout = QVBoxLayout(container)
                container_layout.setContentsMargins(0, 0, 0, 0)
            container_layout.addWidget(self._chat_widget)

        # 获取 UI 控件引用
        self._file_list: QListWidget | None = self.findChild(
            QListWidget, "fileListWidget"
        )
        self._preview_label: QLabel | None = self.findChild(QLabel, "previewLabel")
        self._label_status: QLabel | None = self.findChild(QLabel, "labelStatus")
        self._combo_model = self.findChild(QComboBox, "comboModel")

        # 连接 ChatWidget 信号
        self._chat_widget.message_sent.connect(self._on_message_sent)

    def _create_ui_programmatically(self):
        """动态创建 UI（备用方案）"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # 顶部面板
        top_panel = QWidget()
        top_layout = QHBoxLayout(top_panel)
        top_layout.setContentsMargins(0, 0, 0, 0)

        top_layout.addWidget(QLabel("模型:"))
        self._combo_model = QComboBox()
        self._combo_model.addItems(["PP-DocBee2-3B", "PP-DocBee-2B", "PP-DocBee-7B"])
        top_layout.addWidget(self._combo_model)
        top_layout.addStretch()
        self._label_status = QLabel("状态: ○ 未连接")
        top_layout.addWidget(self._label_status)

        layout.addWidget(top_panel)

        # 主分割器
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧面板
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        left_layout.addWidget(QLabel("文件列表"))
        self._file_list = QListWidget()
        self._file_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        left_layout.addWidget(self._file_list)

        btn_layout = QHBoxLayout()
        btn_add = QPushButton("添加")
        btn_add.setObjectName("btnAddFile")
        btn_layout.addWidget(btn_add)
        btn_remove = QPushButton("删除")
        btn_remove.setObjectName("btnRemoveFile")
        btn_layout.addWidget(btn_remove)
        left_layout.addLayout(btn_layout)

        splitter.addWidget(left_panel)

        # 右侧面板
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        right_layout.addWidget(QLabel("文档预览"))
        self._preview_label = QLabel("选择文件后显示预览")
        self._preview_label.setMinimumHeight(150)
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setStyleSheet(
            "background-color: #f0f0f0; border: 1px solid #ccc; border-radius: 4px;"
        )
        right_layout.addWidget(self._preview_label)

        right_layout.addWidget(QLabel("对话"))
        chat_container = QWidget()
        chat_container.setObjectName("chatContainer")
        chat_container_layout = QVBoxLayout(chat_container)
        chat_container_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(chat_container)

        splitter.addWidget(right_panel)
        splitter.setSizes([200, 600])

        layout.addWidget(splitter)

    def _connect_signals(self):
        """连接信号"""
        btn_add = self.findChild(QPushButton, "btnAddFile")
        btn_remove = self.findChild(QPushButton, "btnRemoveFile")

        if btn_add:
            btn_add.clicked.connect(self._on_add_file)
        if btn_remove:
            btn_remove.clicked.connect(self._on_remove_file)

        if self._file_list:
            self._file_list.currentRowChanged.connect(self._on_file_selected)

    def _on_start(self) -> None:
        """开始处理（文档理解通过聊天交互触发，无需批量启动）"""

    @Slot()
    def _on_add_file(self):
        """添加文件"""
        filters = [
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.tiff *.webp)",
            "PDF 文件 (*.pdf)",
            "所有文件 (*)",
        ]

        files, _ = QFileDialog.getOpenFileNames(
            self, "选择文档", "", ";;".join(filters)
        )

        for file_path in files:
            if self._file_list:
                item = QListWidgetItem(Path(file_path).name)
                item.setData(0x0100, file_path)  # Qt.UserRole
                item.setToolTip(file_path)
                self._file_list.addItem(item)

                # 初始化对话历史
                if file_path not in self._conversation_history:
                    self._conversation_history[file_path] = []

    @Slot()
    def _on_remove_file(self):
        """删除选中文件"""
        if not self._file_list:
            return

        current_row = self._file_list.currentRow()
        if current_row >= 0:
            item = self._file_list.takeItem(current_row)
            if item:
                file_path = item.data(0x0100)
                # 清除对话历史
                if file_path in self._conversation_history:
                    del self._conversation_history[file_path]

    @Slot(int)
    def _on_file_selected(self, row: int):
        """文件选择变化"""
        if not self._file_list or row < 0:
            return

        item = self._file_list.item(row)
        if not item:
            return

        file_path = item.data(0x0100)

        # 保存当前文件的对话历史
        if self._current_file:
            self._conversation_history[self._current_file] = (
                self._chat_widget.get_history()
            )

        # 切换到新文件
        self._current_file = file_path

        # 更新预览
        self._update_preview(file_path)

        # 恢复对话历史
        if file_path in self._conversation_history:
            self._chat_widget.load_history(self._conversation_history[file_path])
        else:
            self._chat_widget.clear_chat()
            # 添加欢迎消息
            self._chat_widget.add_ai_message("您好！请针对这份文档提出您的问题。")

    def _update_preview(self, file_path: str):
        """更新文档预览"""
        if not self._preview_label:
            return

        path = Path(file_path)
        if path.suffix.lower() in [".png", ".jpg", ".jpeg", ".bmp", ".webp"]:
            pixmap = QPixmap(file_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    self._preview_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._preview_label.setPixmap(scaled)
                return

        self._preview_label.setText(f"预览: {path.name}")

    @Slot(str)
    def _on_message_sent(self, query: str):
        """用户发送消息"""
        if not self._current_file:
            self._chat_widget.add_ai_message("请先选择一个文档。")
            return

        if self._worker and self._worker.isRunning():
            self._chat_widget.add_ai_message("正在处理上一个问题，请稍候...")
            return

        # 创建 Worker 处理
        self._chat_widget.set_loading(True)
        self._update_status("处理中...")

        self._worker = DocUnderstandingWorker(
            image_path=self._current_file, query=query, model=self.get_selected_model()
        )
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.error.connect(self._on_worker_error)
        self._worker.start()

    @Slot(str)
    def _on_worker_finished(self, result: str):
        """Worker 完成"""
        self._chat_widget.set_loading(False)
        self._chat_widget.add_ai_message(result)
        self._update_status("就绪")

    @Slot(str)
    def _on_worker_error(self, error_msg: str):
        """Worker 错误"""
        self._chat_widget.set_loading(False)
        self._chat_widget.add_ai_message(f"处理失败: {error_msg}")
        self._update_status("错误")

    def _update_status(self, status: str):
        """更新状态显示"""
        if self._label_status:
            self._label_status.setText(f"状态: {status}")
        self.status_changed.emit(status)

    def get_selected_model(self) -> str:
        """获取选中的模型"""
        if self._combo_model:
            return self._combo_model.currentText()
        return "PP-DocBee2-3B"

    def set_ocr_service(self, service):
        """设置 OCR 服务（兼容接口）"""
        self._ocr_service = service
        self._update_status("已连接")

    def update_model_status(self, configured: bool = False, model_name: str = ""):
        """更新模型状态显示"""
        if self._label_status:
            if configured:
                self._label_status.setText(f"状态: ● 已连接 ({model_name})")
            else:
                self._label_status.setText("状态: ○ 未配置")

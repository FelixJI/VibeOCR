"""批量文件列表组件

显示待处理的文件列表，支持添加、删除、勾选操作。
"""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class BatchFileListWidget(QWidget):
    """批量文件列表组件

    提供：
    - 选择文件按钮
    - 清空列表按钮
    - 文件列表（带状态显示）
    - 已选择文件数量显示
    """

    # 文件列表变更信号
    files_changed = Signal(list)  # List[dict]
    # 选中文件变更信号
    selection_changed = Signal(str)  # file_path

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self._files: list[dict] = []

        self._setup_ui()
        self._connect_signals()

    def _setup_ui(self):
        """设置 UI"""
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(0, 0, 0, 0)

        # 按钮行
        button_layout = QHBoxLayout()

        self._select_btn = QPushButton("选择文件")
        self._clear_btn = QPushButton("清空")

        button_layout.addWidget(self._select_btn)
        button_layout.addWidget(self._clear_btn)
        button_layout.addStretch()

        layout.addLayout(button_layout)

        # 文件列表
        self._table = QTableWidget()
        self._table.setColumnCount(3)
        self._table.setHorizontalHeaderLabels(["状态", "文件名", ""])

        # 设置列宽
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        self._table.setColumnWidth(0, 60)
        self._table.setColumnWidth(2, 40)

        # 选择行为
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        layout.addWidget(self._table)

        # 状态行
        status_layout = QHBoxLayout()
        self._status_label = QLabel("已选择: 0 个文件")
        status_layout.addWidget(self._status_label)
        status_layout.addStretch()

        layout.addLayout(status_layout)

        self.setLayout(layout)

    def _connect_signals(self):
        """连接信号"""
        self._select_btn.clicked.connect(self._on_select_files)
        self._clear_btn.clicked.connect(self._on_clear)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)

    def _on_select_files(self):
        """选择文件"""
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择图片文件",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.tiff *.pdf);;所有文件 (*)",
        )

        if files:
            self.add_files(files)

    def _on_clear(self):
        """清空列表"""
        self._files.clear()
        self._table.setRowCount(0)
        self._update_status()
        self.files_changed.emit([])

    def _on_selection_changed(self):
        """选择变更"""
        selected = self._table.selectedItems()
        if selected:
            row = selected[0].row()
            if row < len(self._files):
                file_path = self._files[row]["path"]
                self.selection_changed.emit(file_path)

    def add_files(self, file_paths: list[str]):
        """添加文件"""
        for path in file_paths:
            # 检查是否已存在
            if any(f["path"] == path for f in self._files):
                continue

            file_info = {
                "path": path,
                "name": Path(path).name,
                "status": "pending",
            }
            self._files.append(file_info)

            # 添加到表格
            row = self._table.rowCount()
            self._table.insertRow(row)

            # 状态
            status_item = QTableWidgetItem("...")
            status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 0, status_item)

            # 文件名
            name_item = QTableWidgetItem(file_info["name"])
            self._table.setItem(row, 1, name_item)

            # 删除按钮占位
            del_item = QTableWidgetItem("")
            self._table.setItem(row, 2, del_item)

        self._update_status()
        self.files_changed.emit(self._files)

    def update_file_status(self, file_path: str, status: str, result=None):
        """更新文件状态

        Args:
            file_path: 文件路径
            status: 状态 (pending, processing, completed, failed)
            result: 识别结果（可选）
        """
        for i, f in enumerate(self._files):
            if f["path"] == file_path:
                f["status"] = status
                f["result"] = result

                # 更新表格
                status_icons = {
                    "pending": "...",
                    "processing": "...",
                    "completed": "[OK]",
                    "failed": "[X]",
                }
                status_item = self._table.item(i, 0)
                if status_item:
                    status_item.setText(status_icons.get(status, "..."))

                # 失败时高亮
                if status == "failed":
                    name_item = self._table.item(i, 1)
                    if name_item:
                        name_item.setForeground(QColor("red"))

                break

        self._update_status()

    def get_selected_files(self) -> list[dict]:
        """获取所有待处理的文件"""
        return [f for f in self._files if f["status"] == "pending"]

    def get_file_count(self) -> int:
        """获取文件总数"""
        return len(self._files)

    def get_pending_count(self) -> int:
        """获取待处理数量"""
        return len([f for f in self._files if f["status"] == "pending"])

    def _update_status(self):
        """更新状态显示"""
        total = len(self._files)
        pending = self.get_pending_count()
        completed = len([f for f in self._files if f["status"] == "completed"])
        failed = len([f for f in self._files if f["status"] == "failed"])

        status_text = (
            f"共: {total} | 待处理: {pending} | 完成: {completed} | 失败: {failed}"
        )
        self._status_label.setText(status_text)

    def clear_results(self):
        """清除所有结果（重置状态）"""
        for i, f in enumerate(self._files):
            f["status"] = "pending"
            f["result"] = None

            status_item = self._table.item(i, 0)
            if status_item:
                status_item.setText("...")

            name_item = self._table.item(i, 1)
            if name_item:
                name_item.setForeground(QColor("black"))

        self._update_status()

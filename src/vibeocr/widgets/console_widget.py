"""控制台输出控件"""

import logging
import re
from typing import List, Optional, Dict
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTableWidget,
    QTableWidgetItem,
    QComboBox,
    QPushButton,
    QLabel,
    QHeaderView,
    QApplication,
)
from PySide6.QtCore import Slot, Qt, Signal
from PySide6.QtGui import QColor, QKeySequence

from vibeocr.services.log_service import LogEntry


class ConsoleWidget(QWidget):
    """控制台输出控件 - 表格形式显示日志"""

    # 低置信度计数变化信号 (数量, [(文本, 置信度), ...])
    low_confidence_count_changed = Signal(int, list)

    # 日志级别颜色
    LEVEL_COLORS = {
        "INFO": QColor("#2196F3"),
        "WARNING": QColor("#FF9800"),
        "ERROR": QColor("#F44336"),
        "DEBUG": QColor("#9E9E9E"),
    }

    # 低置信度阈值和颜色
    LOW_CONFIDENCE_THRESHOLD = 0.80  # 80% 以下标红
    LOW_CONFIDENCE_COLOR = QColor("#F44336")  # 红色

    # 时间格式（表格显示）
    TIME_FORMAT = "%m-%d %H:%M:%S"

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._all_logs: List[LogEntry] = []
        self._current_filter = "ALL"
        self._row_to_log_index: Dict[int, int] = {}  # 表格行 -> 日志索引
        self._low_confidence_count: int = 0  # 低置信度文本块数量
        self._low_confidence_items: List[tuple] = []  # 低置信度文本块详情 [(文本, 置信度), ...]
        self._setup_ui()

    def _setup_ui(self) -> None:
        """初始化 UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # 工具栏
        toolbar = self._create_toolbar()
        layout.addWidget(toolbar)

        # 日志表格
        self._table = self._create_table()
        layout.addWidget(self._table)

    def _create_toolbar(self) -> QWidget:
        """创建工具栏"""
        toolbar = QWidget()
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(8, 4, 8, 4)

        # 标题
        title = QLabel("控制台")
        title.setStyleSheet("font-weight: bold;")
        toolbar_layout.addWidget(title)

        toolbar_layout.addStretch()

        # 级别过滤
        self._level_combo = QComboBox()
        self._level_combo.addItems(["全部", "INFO", "WARNING", "ERROR"])
        self._level_combo.currentTextChanged.connect(self._on_filter_changed)
        toolbar_layout.addWidget(QLabel("级别:"))
        toolbar_layout.addWidget(self._level_combo)

        # 清空按钮
        self._btn_clear = QPushButton("清空")
        self._btn_clear.clicked.connect(self.clear_logs)
        toolbar_layout.addWidget(self._btn_clear)

        return toolbar

    def _create_table(self) -> QTableWidget:
        """创建日志表格"""
        table = QTableWidget()
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["时间", "级别", "消息"])
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)

        # 设置列宽
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        table.setColumnWidth(0, 100)
        table.setColumnWidth(1, 70)

        # 启用复制快捷键
        table.setStyleSheet("QTableWidget { gridline-color: #e0e0e0; }")

        return table

    def append_log(self, entry: LogEntry) -> None:
        """添加日志条目"""
        self._all_logs.append(entry)
        self._refresh_table()

    def clear_logs(self) -> None:
        """清空所有日志"""
        self._all_logs.clear()
        self._table.setRowCount(0)
        self._row_to_log_index.clear()
        self._low_confidence_count = 0
        self._low_confidence_items.clear()
        self.low_confidence_count_changed.emit(0, [])

    @Slot(str)
    def _on_filter_changed(self, text: str) -> None:
        """过滤器变更"""
        self._current_filter = "ALL" if text == "全部" else text
        self._refresh_table()

    def _refresh_table(self) -> None:
        """刷新表格显示"""
        self._row_to_log_index.clear()
        self._low_confidence_count = 0
        self._low_confidence_items.clear()

        # 过滤日志
        if self._current_filter == "ALL":
            filtered_logs = self._all_logs
        else:
            filtered_logs = [log for log in self._all_logs if log.level == self._current_filter]

        # 更新表格
        self._table.setRowCount(len(filtered_logs))
        for row, entry in enumerate(filtered_logs):
            self._row_to_log_index[row] = self._all_logs.index(entry)
            self._add_table_row(row, entry)

        # 发送低置信度计数信号（带详情）
        self.low_confidence_count_changed.emit(self._low_confidence_count, self._low_confidence_items)

        # 滚动到底部
        if filtered_logs:
            self._table.scrollToBottom()

    def _add_table_row(self, row: int, entry: LogEntry) -> None:
        """添加表格行"""
        # 时间
        time_str = entry.timestamp.strftime(self.TIME_FORMAT)
        time_item = QTableWidgetItem(time_str)
        time_item.setData(Qt.ItemDataRole.UserRole, entry)  # 保存完整数据

        # 级别
        level_item = QTableWidgetItem(entry.level)
        level_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        level_color = self.LEVEL_COLORS.get(entry.level, QColor("#000000"))
        level_item.setForeground(level_color)

        # 消息 - 处理多行文本，只显示第一行（完整内容在 tooltip 中）
        msg_lines = entry.message.split('\n')
        msg_text = msg_lines[0] if msg_lines else entry.message
        msg_item = QTableWidgetItem(msg_text)

        # 如果有多行，设置 tooltip 显示完整内容
        if len(msg_lines) > 1:
            msg_item.setToolTip(entry.message)

        # 检测低置信度
        # 匹配 "置信度: XX.XX%" 或 "置信度:XX.XX%" 格式
        confidence_match = re.search(r"置信度:\s*(\d+\.?\d*)\s*%", entry.message)
        if confidence_match:
            try:
                confidence = float(confidence_match.group(1)) / 100
                if confidence < self.LOW_CONFIDENCE_THRESHOLD:
                    # 标红消息列
                    msg_item.setForeground(self.LOW_CONFIDENCE_COLOR)
                    # 计数低置信度并收集详情
                    self._low_confidence_count += 1
                    # 提取文本内容（格式: "  [N] 置信度: XX.XX% | 文本内容"）
                    text_match = re.search(r"\|\s*(.+)$", entry.message)
                    text_content = text_match.group(1).strip() if text_match else "未知"
                    self._low_confidence_items.append((text_content, confidence))
            except (ValueError, ZeroDivisionError):
                pass

        # 添加到表格
        self._table.setItem(row, 0, time_item)
        self._table.setItem(row, 1, level_item)
        self._table.setItem(row, 2, msg_item)

    def keyPressEvent(self, event) -> None:
        """键盘事件处理 - 支持复制"""
        if event.matches(QKeySequence.StandardKey.Copy):
            self._copy_selected()
        else:
            super().keyPressEvent(event)

    def _copy_selected(self) -> None:
        """复制选中的行"""
        selected_rows = self._table.selectedItems()
        if not selected_rows:
            return

        # 获取选中的行号
        rows = sorted(set(item.row() for item in selected_rows))

        # 构建复制文本
        lines = []
        for row in rows:
            time_item = self._table.item(row, 0)
            level_item = self._table.item(row, 1)
            msg_item = self._table.item(row, 2)
            if time_item and level_item and msg_item:
                lines.append(f"[{time_item.text()}] [{level_item.text()}] {msg_item.text()}")

        if lines:
            clipboard = QApplication.clipboard()
            clipboard.setText("\n".join(lines))

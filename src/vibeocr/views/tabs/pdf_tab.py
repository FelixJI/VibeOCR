# src/vibeocr/views/tabs/pdf_tab.py
"""PDF 处理标签页 — 多文件 + 异步加载/OCR。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, cast

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from vibeocr.managers.pdf_session_manager import PdfSessionManager
from vibeocr.services.pdf_service import PdfService
from vibeocr.ui.theme import Colors
from vibeocr.views.pdf_preview_window import PdfPreviewWindow

if TYPE_CHECKING:
    from vibeocr.models.ocr_options import OCROptions
    from vibeocr.models.pdf_ocr_options import PdfGlobalSettings
    from vibeocr.services.ocr_service_base import OCRServiceBase

logger = logging.getLogger(__name__)

_THUMBNAIL_SIZE = 160
_GRID_CELL_SIZE = 40  # 文字层状态网格单格尺寸（正方形）

# 文字层网格 item 数据角色：_LAYER_ROLE 存 page_index，_HAS_LAYER_ROLE 存 has_text_layer
_LAYER_ROLE = Qt.ItemDataRole.UserRole
_HAS_LAYER_ROLE = Qt.ItemDataRole.UserRole + 1


class LayerStatusDelegate(QStyledItemDelegate):
    """文字层网格格子绘制：40×40 圆角方块，居中页码，背景按状态着色。

    有文字层 → 绿（Colors.success）；无文字层 → 灰（Colors.text_subtle）；
    选中态 → 蓝（Colors.accent）覆盖原色。
    """

    def sizeHint(self, option, index):
        return QSize(_GRID_CELL_SIZE, _GRID_CELL_SIZE)

    def paint(self, painter, option, index):
        painter.save()
        page_idx = index.data(_LAYER_ROLE)
        page_num = str(page_idx + 1) if page_idx is not None else ""
        has_layer = index.data(_HAS_LAYER_ROLE)

        if option.state & QStyle.StateFlag.State_Selected:
            bg = QColor(Colors.accent)
        elif has_layer:
            bg = QColor(Colors.success)
        else:
            bg = QColor(Colors.text_subtle)

        # 悬停态用 accent 描边，默认用 border 描边
        is_hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        border_color = QColor(Colors.accent) if is_hover else QColor(Colors.border)
        border_width = 2 if is_hover else 1

        rect = QRectF(option.rect)
        margin = 2
        cell = QRectF(
            rect.x() + margin,
            rect.y() + margin,
            rect.width() - 2 * margin,
            rect.height() - 2 * margin,
        )
        painter.setBrush(bg)
        painter.setPen(QPen(border_color, border_width))
        painter.drawRoundedRect(cell, 6, 6)

        painter.setPen(QPen(QColor("#ffffff")))
        font = painter.font()
        font.setBold(True)
        font.setPointSize(10)
        painter.setFont(font)
        painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, page_num)
        painter.restore()


class PdfTab(QWidget):
    """PDF 处理标签页。"""

    ocr_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session_mgr = PdfSessionManager(self)
        self._preview_window: PdfPreviewWindow | None = None
        # 网格 ↔ 缩略图双向同步的重入保护，避免 itemSelectionChanged 递归
        self._syncing_selection = False
        # splitter 拖动期间 splitterMoved 连续触发，用单次定时器防抖，
        # 停止拖动 300ms 后才落盘，避免每个鼠标移动 tick 都写文件。
        self._splitter_save_timer = QTimer(self)
        self._splitter_save_timer.setSingleShot(True)
        self._splitter_save_timer.timeout.connect(self._persist_splitter_state)
        self._setup_ui()
        self._connect_manager_signals()

    @property
    def session_manager(self) -> PdfSessionManager:
        return self._session_mgr

    def _setup_ui(self) -> None:
        self._main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._main_splitter.setChildrenCollapsible(False)
        self._main_splitter.setObjectName("mainSplitter")

        left_panel = self._create_thumbnail_panel()
        self._main_splitter.addWidget(left_panel)

        right_panel = self._create_operation_panel()
        self._main_splitter.addWidget(right_panel)
        self._main_splitter.setSizes([200, 600])

        # 拖动结束后保存布局（仅主 splitter）
        self._main_splitter.splitterMoved.connect(self._save_splitter_state)

        # 恢复持久化的布局
        self._restore_splitter_state()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._main_splitter)

    def _create_thumbnail_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        self._file_selector = QComboBox()
        self._file_selector.currentIndexChanged.connect(self._on_file_selected)
        layout.addWidget(self._file_selector)

        self._thumbnail_list = QListWidget()
        self._thumbnail_list.setMinimumWidth(120)
        self._thumbnail_list.setIconSize(
            QPixmap(_THUMBNAIL_SIZE, _THUMBNAIL_SIZE).size()
        )
        self._thumbnail_list.setSelectionMode(
            QListWidget.SelectionMode.ExtendedSelection
        )
        self._thumbnail_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self._thumbnail_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._thumbnail_list.customContextMenuRequested.connect(
            self._on_thumbnail_context_menu
        )
        self._thumbnail_list.itemDoubleClicked.connect(
            self._on_thumbnail_double_clicked
        )
        self._thumbnail_list.model().rowsMoved.connect(self._on_pages_reordered)
        # 反向联动：缩略图选中变化 → 状态列表同步当前行
        self._thumbnail_list.itemSelectionChanged.connect(
            self._on_thumbnail_selection_changed
        )

        layout.addWidget(self._thumbnail_list)
        return panel

    def _create_operation_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        file_layout = QHBoxLayout()
        self._btn_open = QPushButton("打开")
        self._btn_open.clicked.connect(self._on_open_file)
        self._btn_add_file = QPushButton("添加文件")
        self._btn_add_file.clicked.connect(self._on_add_file)
        self._btn_save = QPushButton("保存")
        self._btn_save.clicked.connect(self._on_save)
        self._btn_save.setEnabled(False)
        self._btn_save_as = QPushButton("另存为")
        self._btn_save_as.clicked.connect(self._on_save_as)
        self._btn_save_as.setEnabled(False)
        self._btn_export_all = QPushButton("批量导出")
        self._btn_export_all.clicked.connect(self._on_export_all)
        self._btn_export_all.setEnabled(False)
        file_layout.addWidget(self._btn_open)
        file_layout.addWidget(self._btn_add_file)
        file_layout.addWidget(self._btn_save)
        file_layout.addWidget(self._btn_save_as)
        file_layout.addWidget(self._btn_export_all)
        file_layout.addStretch()
        layout.addLayout(file_layout)

        page_group = QGroupBox("页面操作")
        page_layout = QHBoxLayout(page_group)
        self._btn_rotate_cw = QPushButton("顺时针90°")
        self._btn_rotate_cw.clicked.connect(lambda: self._on_rotate(90))
        self._btn_rotate_ccw = QPushButton("逆时针90°")
        self._btn_rotate_ccw.clicked.connect(lambda: self._on_rotate(-90))
        self._btn_rotate_all = QPushButton("旋转全部")
        self._btn_rotate_all.clicked.connect(self._on_rotate_all)
        self._btn_auto_deskew = QPushButton("自动摆正")
        self._btn_auto_deskew.setToolTip(
            "自动检测选中页方向并旋转至文字朝上（仅 90° 倍数）"
        )
        self._btn_auto_deskew.clicked.connect(self._on_auto_deskew)
        self._btn_delete = QPushButton("删除选中页")
        self._btn_delete.clicked.connect(self._on_delete_pages)
        self._btn_insert = QPushButton("在选中页后插入")
        self._btn_insert.clicked.connect(self._on_insert_page)
        page_layout.addWidget(self._btn_rotate_cw)
        page_layout.addWidget(self._btn_rotate_ccw)
        page_layout.addWidget(self._btn_rotate_all)
        page_layout.addWidget(self._btn_auto_deskew)
        page_layout.addWidget(self._btn_delete)
        page_layout.addWidget(self._btn_insert)
        layout.addWidget(page_group)

        text_group = QGroupBox("文字层操作")
        text_layout = QVBoxLayout(text_group)
        text_btn_layout = QHBoxLayout()
        self._btn_add_text_layer = QPushButton("添加文字层")
        self._btn_add_text_layer.clicked.connect(self._on_add_text_layer)
        self._btn_add_text_layer_no_layer = QPushButton("为无文字层页添加文字层")
        self._btn_add_text_layer_no_layer.clicked.connect(
            self._on_add_text_layer_for_pages_without_layer
        )
        self._btn_del_text_layer = QPushButton("删除文字层")
        self._btn_del_text_layer.clicked.connect(self._on_delete_text_layer)
        self._btn_preview_text_layer = QPushButton("预览文字层")
        self._btn_preview_text_layer.clicked.connect(self._on_preview_text_layer)
        text_btn_layout.addWidget(self._btn_add_text_layer)
        text_btn_layout.addWidget(self._btn_add_text_layer_no_layer)
        text_btn_layout.addWidget(self._btn_del_text_layer)
        text_btn_layout.addWidget(self._btn_preview_text_layer)
        text_layout.addLayout(text_btn_layout)

        self._layer_summary_label = QLabel("")
        self._layer_summary_label.setWordWrap(False)
        text_layout.addWidget(self._layer_summary_label)

        self._layer_status_grid = QListWidget()
        self._layer_status_grid.setViewMode(QListWidget.ViewMode.IconMode)
        self._layer_status_grid.setFlow(QListWidget.Flow.LeftToRight)
        self._layer_status_grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._layer_status_grid.setMovement(QListWidget.Movement.Static)
        self._layer_status_grid.setWrapping(True)
        self._layer_status_grid.setIconSize(QSize(_GRID_CELL_SIZE, _GRID_CELL_SIZE))
        # gridSize 略大于 iconSize，给格子间留 3px 间距
        self._layer_status_grid.setGridSize(
            QSize(_GRID_CELL_SIZE + 6, _GRID_CELL_SIZE + 6)
        )
        self._layer_status_grid.setItemDelegate(
            LayerStatusDelegate(self._layer_status_grid)
        )
        self._layer_status_grid.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self._layer_status_grid.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._layer_status_grid.setDragDropMode(QListWidget.DragDropMode.NoDragDrop)
        self._layer_status_grid.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._layer_status_grid.customContextMenuRequested.connect(
            self._on_layer_status_context_menu
        )
        self._layer_status_grid.itemDoubleClicked.connect(
            self._on_grid_item_double_clicked
        )
        self._layer_status_grid.itemSelectionChanged.connect(
            self._on_layer_status_selection_changed
        )
        grid_scroll = QScrollArea()
        grid_scroll.setWidgetResizable(True)
        grid_scroll.setWidget(self._layer_status_grid)
        grid_scroll.setMinimumHeight(120)
        text_layout.addWidget(grid_scroll)
        layout.addWidget(text_group)

        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._btn_cancel = QPushButton("取消")
        self._btn_cancel.setVisible(False)
        self._btn_cancel.clicked.connect(self._on_cancel)
        progress_layout = QHBoxLayout()
        progress_layout.addWidget(self._progress_bar)
        progress_layout.addWidget(self._btn_cancel)
        layout.addLayout(progress_layout)

        self._status_label = QLabel("")
        layout.addWidget(self._status_label)

        layout.addStretch()
        self._set_file_buttons_enabled(False)
        return panel

    # ---- session manager signals ------------------------------------

    def _connect_manager_signals(self) -> None:
        mgr = self._session_mgr
        mgr.session_added.connect(self._on_session_added)
        mgr.session_removed.connect(self._on_session_removed)
        mgr.active_changed.connect(self._on_active_changed)
        mgr.page_loaded.connect(self._on_page_loaded)
        mgr.load_progress.connect(self._on_load_progress)
        mgr.load_done.connect(self._on_load_done)
        mgr.ocr_page_done.connect(self._on_ocr_page_result)
        mgr.ocr_progress.connect(self._on_ocr_progress_update)
        mgr.ocr_done.connect(self._on_ocr_finished)
        mgr.ocr_stats_ready.connect(self._on_ocr_stats_ready)
        mgr.mineru_models_status.connect(self._on_mineru_models_status)
        mgr.mutate_progress.connect(self._on_mutate_progress)
        mgr.mutate_done.connect(self._on_mutate_done)
        mgr.mutate_failed.connect(self._on_mutate_failed)
        mgr.save_done.connect(self._on_save_done)
        mgr.delete_layer_done.connect(self._on_delete_layer_done)
        mgr.render_progress.connect(self._on_render_progress_update)
        mgr.export_progress.connect(self._on_export_progress)
        mgr.export_done.connect(self._on_export_done)
        mgr.deskew_page_done.connect(self._on_deskew_page_done)
        mgr.deskew_done.connect(self._on_deskew_done)
        mgr.deskew_failed.connect(self._on_deskew_failed)

    # ---- splitter layout persistence --------------------------------

    def _restore_splitter_state(self) -> None:
        """从偏好恢复 splitter 布局（仅主 splitter）。"""
        try:
            from vibeocr.utils.ocr_preferences import OCRPreferences

            prefs = OCRPreferences.instance()
        except RuntimeError:
            return
        main_state = prefs.get_pdf_splitter_state()
        if main_state:
            self._main_splitter.restoreState(main_state)

    def _save_splitter_state(self) -> None:
        """拖动时触发：重启防抖定时器，停止拖动 300ms 后才落盘。"""
        self._splitter_save_timer.start(300)

    def _persist_splitter_state(self) -> None:
        """防抖到期后实际落盘（一次写盘只保存主 splitter）。"""
        try:
            from vibeocr.utils.ocr_preferences import OCRPreferences

            prefs = OCRPreferences.instance()
        except RuntimeError:
            return
        # saveState().data() 静态类型含 bytearray|memoryview，但 PySide6 运行时恒为 bytes。
        main_state = cast("bytes", self._main_splitter.saveState().data())
        prefs.set_pdf_splitter_states(main_state, None)

    def _on_session_added(self, file_path: str) -> None:
        name = Path(file_path).name
        self._file_selector.addItem(name, file_path)
        self._file_selector.setCurrentIndex(self._file_selector.count() - 1)
        self._btn_export_all.setEnabled(True)

    def _on_session_removed(self, file_path: str) -> None:
        for i in range(self._file_selector.count()):
            if self._file_selector.itemData(i) == file_path:
                self._file_selector.removeItem(i)
                break
        if self._file_selector.count() == 0:
            self._btn_export_all.setEnabled(False)

    def _on_active_changed(self, file_path: str) -> None:
        # 切换文件：预览窗口的 _page_indices 指向旧文档，关闭它避免翻页到失效索引。
        self._close_preview_window_if_open()
        # 全量重建期间抑制双向选中同步：clear() 会触发 itemSelectionChanged，
        # 此时两侧控件尚处于不一致的中间态，让同步逻辑静默直到重建完成。
        self._syncing_selection = True
        try:
            self._refresh_thumbnails()
            self._update_status()
            self._update_layer_status()
        finally:
            self._syncing_selection = False
        has_doc = self._session_mgr.active_session is not None
        self._set_file_buttons_enabled(has_doc)

    def _on_page_loaded(self, file_path: str, page_index: int) -> None:
        session = self._session_mgr.active_session
        if session is None or session.file_path != file_path:
            return
        if page_index < self._thumbnail_list.count():
            page_info = (
                session.pdf_document.pages[page_index]
                if page_index < len(session.pdf_document.pages)
                else None
            )
            if page_info and page_info.thumbnail:
                scaled = self._scale_thumbnail(page_info.thumbnail)
            else:
                scaled = self._placeholder_pixmap()
            item = self._thumbnail_list.item(page_index)
            if item:
                item.setIcon(QIcon(scaled))
        else:
            self._refresh_thumbnails()

    def _on_load_progress(self, file_path: str, loaded: int, total: int) -> None:
        session = self._session_mgr.active_session
        if session is None or session.file_path != file_path:
            return
        self._status_label.setText(f"{Path(file_path).name} 正在加载 {loaded}/{total} 页…")

    def _on_load_done(self, file_path: str) -> None:
        session = self._session_mgr.active_session
        if session and session.file_path == file_path:
            self._update_layer_status()
            self._status_label.setText(f"{Path(file_path).name} 加载完成")

    def _on_ocr_page_result(self, file_path: str, page_index: int, result) -> None:
        session = self._session_mgr.active_session
        if session is None or session.file_path != file_path:
            return
        # OCR 注入的是隐形文字层，缩略图无视觉变化 → 不重新渲染。
        # 仅逐页更新文字层网格格子（即时变绿）+ 汇总统计。
        self._update_layer_grid_page(page_index)

    def _on_ocr_progress_update(self, file_path: str, current: int, total: int) -> None:
        self._progress_bar.setValue(current)
        self._status_label.setText(f"正在识别第 {current}/{total} 页...")

    def _on_mineru_models_status(self, message: str) -> None:
        """MinerU 模型下载状态提示（首次使用文档解析时）"""
        self._status_label.setText(message)
        # 下载期间显示不确定进度条（无具体百分比）
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setVisible(True)

    def _on_render_progress_update(self, file_path: str, current: int, total: int) -> None:
        """OCR 渲染前置阶段进度（render worker 渲染页面，OCR 未开始）。"""
        session = self._session_mgr.active_session
        if session is None or session.file_path != file_path:
            return
        self._progress_bar.setRange(0, total)
        self._progress_bar.setValue(current)
        self._status_label.setText(f"正在渲染页面 {current}/{total}…")

    def _on_mutate_progress(self, file_path: str, current: int, total: int) -> None:
        session = self._session_mgr.active_session
        if session is None or session.file_path != file_path:
            return
        if total > 0:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(current)
            self._status_label.setText(f"正在处理 {current}/{total}…")

    def _on_mutate_done(self, file_path: str, result) -> None:
        """mutate 逐页/整体完成。逐页 payload 更新 grid 格子。"""
        session = self._session_mgr.active_session
        if session is None or session.file_path != file_path:
            return
        if isinstance(result, dict) and "page" in result:
            self._update_layer_grid_page(result["page"])

    def _on_mutate_failed(self, file_path: str, error: str) -> None:
        session = self._session_mgr.active_session
        if session is None or session.file_path != file_path:
            return
        self._progress_bar.setVisible(False)
        self._set_file_buttons_enabled(True)
        QMessageBox.warning(self, "操作失败", error)

    def _on_delete_layer_done(self, file_path: str, residual_pages: list) -> None:
        session = self._session_mgr.active_session
        if session is None or session.file_path != file_path:
            return
        self._progress_bar.setVisible(False)
        self._set_file_buttons_enabled(True)
        self._update_status()
        if residual_pages:
            QMessageBox.warning(
                self,
                "删除文字层",
                f"第 {', '.join(str(p + 1) for p in residual_pages)} 页经多轮删除"
                f"仍有少量残留文字，\n可能是特殊字体或嵌入图片文字，建议手动检查。",
            )
        else:
            self._status_label.setText("文字层删除完成")

    def _on_save_done(self, file_path: str) -> None:
        session = self._session_mgr.active_session
        if session is None or session.file_path != file_path:
            return
        self._progress_bar.setVisible(False)
        self._set_file_buttons_enabled(True)
        self._btn_open.setEnabled(True)
        self._btn_add_file.setEnabled(True)
        self._update_status()
        self._status_label.setText(f"{Path(file_path).name} 保存完成")

    def _on_export_progress(self, current: int, total: int, file_name: str) -> None:
        self._progress_bar.setRange(0, total)
        self._progress_bar.setValue(current)
        self._status_label.setText(f"正在导出 {file_name} ({current}/{total})…")

    def _on_export_done(self, exported_paths: list) -> None:
        self._progress_bar.setVisible(False)
        self._set_file_buttons_enabled(True)
        QMessageBox.information(
            self,
            "批量导出完成",
            f"成功导出 {len(exported_paths)} 个文件。",
        )

    def _on_ocr_finished(self, file_path: str, success: int, fail: int) -> None:
        self._progress_bar.setVisible(False)
        self._btn_cancel.setVisible(False)
        self._set_file_buttons_enabled(True)
        self._btn_open.setEnabled(True)
        self._btn_add_file.setEnabled(True)
        self._update_status()
        # 不全量重建网格：逐页 _update_layer_grid_page 已把完成的页变绿，
        # 全量 clear()+重建会抹掉用户在 OCR 期间的选中状态（spec 第 6 节）。
        self._refresh_layer_summary()
        msg = f"OCR 完成：成功 {success} 页" + (f"，失败 {fail} 页" if fail else "")
        self._status_label.setText(msg)

    def _on_ocr_stats_ready(self, session_id: str, written: int, skipped: int) -> None:
        """文字层 OCR 完成后：汇总写入结果（成功/跳过）。

        与 _on_ocr_finished（ocr_done 信号）配合：后者负责通用 UI 复位，
        本方法负责文字层特有的"成功/跳过"汇总。网格格子已由逐页
        _update_layer_grid_page 即时更新，此处仅刷新汇总计数。
        """
        if written == 0 and skipped == 0:
            # 没有任何文字块产出（例如全部页面 OCR 失败），不误报“已添加”。
            self._status_label.setText("文字层未添加：未识别到任何文字块")
        elif skipped > 0:
            QMessageBox.information(
                self,
                "文字层已添加",
                f"成功写入 {written} 块，跳过 {skipped} 块（详见日志）。",
            )
        else:
            self._status_label.setText(f"文字层已添加（{written} 块）")
        self._refresh_layer_summary()

    def _on_block_text_edited(
        self, page_index: int, block_index: int, new_text: str
    ) -> None:
        """预览画布双击改字回调：更新内存模型 + 刷新网格 tooltip + 刷新预览弹窗。

        实际写回 PDF 文字层在用户点'保存'时由 rewrite_modified_pages 执行。
        """
        if self._session_mgr.update_page_block_text(page_index, block_index, new_text):
            self._update_layer_grid_page(page_index)
            self._refresh_preview_window_if_current(page_index)

    def _refresh_preview_window_if_current(self, page_index: int) -> None:
        """若预览弹窗正打开且显示该页，重新渲染填充（编辑块文字后刷新）。"""
        win = self._preview_window
        if win is None or not win.isVisible():
            return
        if win.current_page_index() == page_index:
            self._render_preview_page(page_index)

    # ---- UI helpers -------------------------------------------------

    def _set_file_buttons_enabled(self, enabled: bool) -> None:
        for btn in (
            self._btn_save,
            self._btn_save_as,
            self._btn_rotate_cw,
            self._btn_rotate_ccw,
            self._btn_rotate_all,
            self._btn_auto_deskew,
            self._btn_delete,
            self._btn_insert,
            self._btn_add_text_layer,
            self._btn_add_text_layer_no_layer,
            self._btn_del_text_layer,
            self._btn_preview_text_layer,
        ):
            btn.setEnabled(enabled)

    @staticmethod
    def _scale_thumbnail(pixmap: QPixmap) -> QPixmap:
        return pixmap.scaled(
            _THUMBNAIL_SIZE,
            _THUMBNAIL_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    @staticmethod
    def _placeholder_pixmap() -> QPixmap:
        pm = QPixmap(_THUMBNAIL_SIZE, _THUMBNAIL_SIZE)
        pm.fill(Qt.GlobalColor.lightGray)
        return pm

    def _render_single_thumbnail(self, page_index: int) -> QPixmap:
        """主线程渲染单页缩略图（thumbnail_dpi）并写回缓存。用于旋转后增量更新。"""
        session = self._session_mgr.active_session
        if session is None:
            return self._placeholder_pixmap()
        with session.doc_lock:
            pixmap = PdfService.render_page(
                session.doc, page_index, dpi=session.pdf_document.thumbnail_dpi
            )
        scaled = self._scale_thumbnail(pixmap)
        page_info = session.pdf_document.get_page(page_index)
        if page_info is not None:
            page_info.thumbnail = scaled
        return scaled

    def _update_thumbnail_icon(self, page_index: int) -> None:
        """渲染单页并更新对应缩略图 item 的 icon（旋转后调用）。"""
        scaled = self._render_single_thumbnail(page_index)
        item = self._find_thumbnail_item(page_index)
        if item is not None:
            item.setIcon(QIcon(scaled))

    def _find_thumbnail_item(self, page_index: int) -> QListWidgetItem | None:
        for row in range(self._thumbnail_list.count()):
            item = self._thumbnail_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == page_index:
                return item
        return None

    def _reorder_thumbnail_items(self, new_order: list[int]) -> None:
        """拖拽排序后：按 new_order 重排缩略图 item，复用原有 icon（不重新渲染）。

        takeItem 摘出 item（保留 icon/role），再按新顺序重新插入。
        takeItem 会清除选中态，故重排前记录选中的 page_index，重排后恢复。
        """
        # 记录重排前选中的 page_index（takeItem 会丢掉选中态）
        selected_pages = self._get_selected_page_indices()
        old_items: list[QListWidgetItem] = []
        for _ in range(self._thumbnail_list.count()):
            old_items.append(self._thumbnail_list.takeItem(0))
        by_page = {it.data(Qt.ItemDataRole.UserRole): it for it in old_items}
        for page_idx in new_order:
            item = by_page.get(page_idx)
            if item is not None:
                self._thumbnail_list.addItem(item)
        # 恢复选中（page_index 未变，只是行序变了）
        want = set(selected_pages)
        for row in range(self._thumbnail_list.count()):
            item = self._thumbnail_list.item(row)
            item.setSelected(item.data(Qt.ItemDataRole.UserRole) in want)

    def _refresh_thumbnails(self) -> None:
        session = self._session_mgr.active_session
        if session is None:
            self._thumbnail_list.clear()
            return
        doc = session.pdf_document
        self._thumbnail_list.clear()
        for page_info in doc.pages:
            scaled = (
                self._scale_thumbnail(page_info.thumbnail)
                if page_info.thumbnail
                else self._placeholder_pixmap()
            )
            item = QListWidgetItem(QIcon(scaled), f"第 {page_info.page_index + 1} 页")
            item.setData(Qt.ItemDataRole.UserRole, page_info.page_index)
            self._thumbnail_list.addItem(item)

    def _update_status(self) -> None:
        session = self._session_mgr.active_session
        if session is None:
            self._status_label.setText("")
            return
        name = Path(session.file_path).name
        modified = " (未保存)" if session.is_modified else ""
        self._status_label.setText(
            f"{name} | {session.pdf_document.page_count} 页{modified}"
        )
        self._btn_save.setEnabled(session.is_modified)

    def _update_layer_status(self) -> None:
        session = self._session_mgr.active_session
        grid = self._layer_status_grid
        grid.clear()
        if session is None:
            self._update_layer_summary([])
            return
        pages = session.pdf_document.pages
        for p in pages:
            item = QListWidgetItem()
            item.setData(_LAYER_ROLE, p.page_index)
            item.setData(_HAS_LAYER_ROLE, p.has_text_layer)
            item.setToolTip(self._layer_cell_tooltip(p))
            grid.addItem(item)
        self._update_layer_summary(pages)

    @staticmethod
    def _layer_cell_tooltip(page_info) -> str:
        """生成文字层网格格子的 tooltip（block_count 优先用 OCR 原始块）。"""
        if page_info.has_text_layer:
            block_count = (
                len(page_info.ocr_text_blocks)
                if page_info.ocr_text_blocks
                else len(page_info.text_layers)
            )
            return f"第{page_info.page_index + 1}页 · 已添加文字层（{block_count}个文本块）"
        return f"第{page_info.page_index + 1}页 · 无文字层"

    def _update_layer_grid_page(self, page_index: int) -> None:
        """增量更新单页网格格子（不全量重建），用于 OCR/删除文字层即时反馈。

        保留用户当前选中状态（只改单格的颜色/tooltip，不清空网格）。
        """
        session = self._session_mgr.active_session
        if session is None:
            return
        page_info = session.pdf_document.get_page(page_index)
        if page_info is None:
            return
        grid = self._layer_status_grid
        for row in range(grid.count()):
            item = grid.item(row)
            if item.data(_LAYER_ROLE) == page_index:
                item.setData(_HAS_LAYER_ROLE, page_info.has_text_layer)
                item.setToolTip(self._layer_cell_tooltip(page_info))
                break
        # 汇总统计实时刷新
        self._update_layer_summary(session.pdf_document.pages)

    def _update_layer_summary(self, pages) -> None:
        """更新网格上方汇总 Label（共 N 页 / 有文字层 X / 无文字层 Y）。"""
        total = len(pages)
        with_layer = sum(1 for p in pages if p.has_text_layer)
        without = total - with_layer
        self._layer_summary_label.setText(
            f"共 {total} 页 ｜ "
            f"<span style='color:{Colors.success}'>●</span> 有文字层 {with_layer} 页  "
            f"<span style='color:{Colors.text_subtle}'>●</span> 无文字层 {without} 页"
        )

    def _refresh_layer_summary(self) -> None:
        """从活动会话重算汇总 Label 计数（不清空网格，保留选中）。

        用于 OCR/删除文字层完成后：网格格子已逐页更新，仅汇总计数需刷新。
        """
        session = self._session_mgr.active_session
        pages = session.pdf_document.pages if session is not None else []
        self._update_layer_summary(pages)

    def _on_grid_item_double_clicked(self, item: QListWidgetItem) -> None:
        """双击网格格子 → 打开预览窗口到该页。"""
        page_idx = item.data(_LAYER_ROLE)
        if isinstance(page_idx, int):
            self._open_preview(page_idx)

    def _on_layer_status_context_menu(self, pos) -> None:
        """状态网格右键菜单：为选中的无文字层页添加文字层。"""
        session = self._session_mgr.active_session
        if session is None:
            return

        # 收集选中行；无选中则取右键位置所在行
        rows = [i.row() for i in self._layer_status_grid.selectedIndexes()]
        if not rows:
            item = self._layer_status_grid.itemAt(pos)
            if item is None:
                return
            rows = [self._layer_status_grid.row(item)]

        pages = session.pdf_document.pages
        indices = [
            pages[r].page_index
            for r in rows
            if r < len(pages) and not pages[r].has_text_layer
        ]

        menu = QMenu(self)
        if indices:
            act = menu.addAction(f"为 {len(indices)} 个无文字层页添加文字层")
            act.triggered.connect(
                lambda checked=False, idx=indices: self._add_text_layer_for_indices(idx)
            )
        else:
            menu.addAction("选中页面均已有文字层")
        menu.exec(self._layer_status_grid.mapToGlobal(pos))

    def _add_text_layer_for_indices(self, indices: list[int]) -> None:
        """供右键菜单复用：对指定页索引执行添加文字层（overwrite=False）。"""
        session = self._session_mgr.active_session
        if session is None or not indices:
            return
        if not self._session_mgr.is_ocr_ready:
            QMessageBox.warning(
                self,
                "OCR 服务未就绪",
                "OCR 服务尚未初始化，请等待服务启动完成。",
            )
            return

        reply = QMessageBox.question(
            self,
            "添加文字层",
            f"将对 {len(indices)} 个无文字层页面执行 OCR 并添加隐形文字层。\n"
            "建议先另存为备份。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        pdf_settings, ocr_options = self._load_ocr_prefs()
        self._begin_ocr_ui(indices)

        self._session_mgr.start_ocr(
            indices,
            ocr_options=ocr_options,
            pdf_settings=pdf_settings,
            overwrite=False,
        )

    def _on_thumbnail_selection_changed(self) -> None:
        """缩略图选中变化 → 网格同步选中相同 page_index（重入保护防递归）。"""
        if self._syncing_selection:
            return
        indices = self._get_selected_page_indices()
        self._syncing_selection = True
        try:
            self._sync_selection_to(self._layer_status_grid, indices)
        finally:
            self._syncing_selection = False

    def _on_layer_status_selection_changed(self) -> None:
        """网格选中变化 → 缩略图同步选中相同 page_index（重入保护防递归）。"""
        if self._syncing_selection:
            return
        grid = self._layer_status_grid
        indices = [
            item.data(_LAYER_ROLE)
            for item in grid.selectedItems()
            if item.data(_LAYER_ROLE) is not None
        ]
        self._syncing_selection = True
        try:
            self._sync_selection_to(self._thumbnail_list, sorted(set(indices)))
        finally:
            self._syncing_selection = False

    def _sync_selection_to(self, target: QListWidget, page_indices: list[int]) -> None:
        """把给定 page_index 集合同步选中到 target 列表（按 page_index 匹配，清旧选新）。

        两个列表都用 _LAYER_ROLE（== Qt.ItemDataRole.UserRole）存 page_index。
        """
        want = set(page_indices)
        for row in range(target.count()):
            item = target.item(row)
            item.setSelected(item.data(_LAYER_ROLE) in want)

    def _get_selected_page_indices(self) -> list[int]:
        indices = []
        for item in self._thumbnail_list.selectedItems():
            idx = item.data(Qt.ItemDataRole.UserRole)
            if idx is not None:
                indices.append(idx)
        return sorted(set(indices))

    # ---- file operations --------------------------------------------

    def _on_file_selected(self, index: int) -> None:
        file_path = self._file_selector.itemData(index)
        if not file_path:
            return
        session = self._session_mgr.active_session
        if session and session.is_modified and session.file_path != file_path:
            reply = QMessageBox.question(
                self,
                "未保存的修改",
                f"{Path(session.file_path).name} 有未保存的修改，是否保存？",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Save:
                self._on_save()
            elif reply == QMessageBox.StandardButton.Cancel:
                # Revert combo box to current session
                for i in range(self._file_selector.count()):
                    if self._file_selector.itemData(i) == session.file_path:
                        self._file_selector.setCurrentIndex(i)
                        break
                return
        self._session_mgr.switch_session(file_path)

    def _on_open_file(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "打开 PDF 文件", "", "PDF 文件 (*.pdf)"
        )
        if not paths:
            return
        for path in paths:
            try:
                self._session_mgr.open_session(path)
            except (FileNotFoundError, RuntimeError) as e:
                QMessageBox.warning(self, "打开失败", str(e))

    def _on_add_file(self) -> None:
        self._on_open_file()

    def _on_save(self) -> None:
        session = self._session_mgr.active_session
        if session is None:
            return
        self._set_file_buttons_enabled(False)
        self._btn_open.setEnabled(False)
        self._btn_add_file.setEnabled(False)
        self._progress_bar.setRange(0, 0)  # 不确定进度（rewrite+落盘）
        self._progress_bar.setVisible(True)
        self._status_label.setText("正在保存…")
        pdf_settings, _ = self._load_ocr_prefs()
        self._session_mgr.save_async(path=None, pdf_settings=pdf_settings)

    def _on_save_as(self) -> None:
        session = self._session_mgr.active_session
        if session is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "另存为", "", "PDF 文件 (*.pdf)")
        if not path:
            return
        self._set_file_buttons_enabled(False)
        self._btn_open.setEnabled(False)
        self._btn_add_file.setEnabled(False)
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setVisible(True)
        self._status_label.setText("正在保存…")
        pdf_settings, _ = self._load_ocr_prefs()
        self._session_mgr.save_async(path=path, pdf_settings=pdf_settings)

    def _on_export_all(self) -> None:
        mgr = self._session_mgr
        modified_paths = [p for p, _ in mgr.get_modified_sessions()]
        if not modified_paths:
            QMessageBox.information(self, "批量导出", "没有需要导出的修改文件。")
            return

        dir_path = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not dir_path:
            return

        self._set_file_buttons_enabled(False)
        self._progress_bar.setRange(0, len(modified_paths))
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)
        self._status_label.setText("正在批量导出…")
        mgr.export_all_async(dir_path)

    # ---- page operations --------------------------------------------

    def _on_thumbnail_context_menu(self, pos) -> None:
        menu = QMenu(self)
        menu.addAction("顺时针旋转90°", lambda: self._on_rotate(90))
        menu.addAction("逆时针旋转90°", lambda: self._on_rotate(-90))
        menu.addSeparator()
        menu.addAction("删除页面", self._on_delete_pages)
        menu.addAction("在此页后插入", self._on_insert_page)
        menu.addSeparator()
        menu.addAction("预览", lambda: self._open_preview_for_selected())
        menu.exec(self._thumbnail_list.mapToGlobal(pos))

    def _on_thumbnail_double_clicked(self, item: QListWidgetItem) -> None:
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx is not None:
            self._open_preview(idx)

    def _on_pages_reordered(self) -> None:
        new_order: list[int] = []
        for row in range(self._thumbnail_list.count()):
            item = self._thumbnail_list.item(row)
            old_idx = item.data(Qt.ItemDataRole.UserRole)
            if old_idx is not None:
                new_order.append(old_idx)
        self._on_pages_reordered_with_order(new_order)

    def _on_pages_reordered_with_order(self, new_order: list[int]) -> None:
        """用显式 new_order 应用重排：PdfService 重排文档 + 增量移动缩略图 item。

        缩略图 pixmap 内容不变（只是顺序变了），故只移动 item 不重新渲染。
        """
        session = self._session_mgr.active_session
        if session is None or not new_order:
            return
        with session.doc_lock:
            PdfService.reorder_pages(session.doc, session.pdf_document, new_order)
        self._reorder_thumbnail_items(new_order)
        self._update_status()

    def _on_rotate(self, angle: int) -> None:
        session = self._session_mgr.active_session
        if session is None:
            return
        indices = self._get_selected_page_indices()
        if not indices:
            return
        with session.doc_lock:
            PdfService.rotate_pages(session.doc, session.pdf_document, indices, angle)
        # 旋转改变了页面视觉 → 仅增量渲染受影响页（不全量重建）
        for idx in indices:
            self._update_thumbnail_icon(idx)
        self._update_status()

    def _on_rotate_all(self) -> None:
        session = self._session_mgr.active_session
        if session is None:
            return
        reply = QMessageBox.question(
            self,
            "旋转全部页面",
            "确定旋转全部页面 90°？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        indices = list(range(session.pdf_document.page_count))
        with session.doc_lock:
            PdfService.rotate_pages(session.doc, session.pdf_document, indices, 90)
        for idx in indices:
            self._update_thumbnail_icon(idx)
        self._update_status()

    def _on_auto_deskew(self) -> None:
        session = self._session_mgr.active_session
        if session is None:
            return
        indices = self._get_selected_page_indices()
        if not indices:
            QMessageBox.information(self, "自动摆正", "请先选中要摆正的页面。")
            return
        self._btn_auto_deskew.setEnabled(False)
        self._session_mgr.auto_deskew_async(indices)

    def _on_deskew_page_done(
        self, session_id: str, page_index: int, was_corrected: bool
    ) -> None:
        session = self._session_mgr.active_session
        if session is None or session.file_path != session_id:
            return
        # 缩略图刷新（rotate 改变页面视觉）
        self._update_thumbnail_icon(page_index)
        # 格子刷新（Task 5 会让此处同时刷新 _DESKEWED_ROLE）
        self._update_layer_grid_page(page_index)

    def _on_deskew_done(self, session_id: str, summary) -> None:
        self._btn_auto_deskew.setEnabled(True)
        session = self._session_mgr.active_session
        if session is None or session.file_path != session_id:
            return
        corrected = summary.get("corrected", 0)
        skipped = summary.get("skipped", 0)
        pages = summary.get("corrected_pages", [])
        if corrected == 0:
            QMessageBox.information(self, "自动摆正", "选中页本已正向，无需纠正。")
        else:
            page_str = "、".join(str(p + 1) for p in pages)
            QMessageBox.information(
                self,
                "自动摆正",
                f"已摆正 {corrected} 页（第 {page_str} 页）；跳过 {skipped} 页（本已正向）。",
            )
        self._update_status()

    def _on_deskew_failed(self, session_id: str, error: str) -> None:
        self._btn_auto_deskew.setEnabled(True)
        QMessageBox.warning(self, "自动摆正失败", error)
        self._update_status()

    def _on_delete_pages(self) -> None:
        session = self._session_mgr.active_session
        if session is None:
            return
        indices = self._get_selected_page_indices()
        if not indices:
            return
        reply = QMessageBox.question(
            self,
            "删除页面",
            f"确定删除选中的 {len(indices)} 页？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        with session.doc_lock:
            PdfService.delete_pages(session.doc, session.pdf_document, indices)
        session.loaded_pages -= set(indices)
        # 删页改变了 page_index 与位置映射，预览窗口的 _page_indices 会错位 → 关闭。
        self._close_preview_window_if_open()
        self._refresh_thumbnails()
        self._update_status()
        self._update_layer_status()

    def _on_insert_page(self) -> None:
        session = self._session_mgr.active_session
        if session is None:
            return
        indices = self._get_selected_page_indices()
        after_index = indices[0] if indices else 0

        path, _ = QFileDialog.getOpenFileName(
            self, "选择要插入的 PDF", "", "PDF 文件 (*.pdf)"
        )
        if path:
            try:
                with session.doc_lock:
                    PdfService.insert_pages_from(
                        session.doc, session.pdf_document, path, after_index
                    )
            except Exception as e:
                QMessageBox.warning(self, "插入失败", str(e))
                return
        else:
            with session.doc_lock:
                PdfService.insert_blank_page(
                    session.doc, session.pdf_document, after_index
                )
        session.loaded_pages.clear()
        self._refresh_thumbnails()
        self._update_status()
        self._update_layer_status()

    # ---- preview ----------------------------------------------------

    def _open_preview_for_selected(self) -> None:
        indices = self._get_selected_page_indices()
        if indices:
            self._open_preview(indices[0])

    def _open_preview(self, page_index: int) -> None:
        """打开预览窗口，可翻页浏览整个文档（_page_indices = 全部页）。"""
        session = self._session_mgr.active_session
        if session is None:
            return
        all_indices = [p.page_index for p in session.pdf_document.pages]
        if self._preview_window is None:
            self._preview_window = PdfPreviewWindow()
            self._preview_window.block_text_edited.connect(self._on_block_text_edited)
            self._preview_window.page_change_requested.connect(
                self._render_preview_page
            )
        assert self._preview_window is not None
        current = all_indices.index(page_index) if page_index in all_indices else 0
        self._preview_window.set_page_indices(all_indices, current)
        self._render_preview_page(page_index)
        self._preview_window.show()
        self._preview_window.raise_()

    def _render_preview_page(self, page_idx: int) -> None:
        """渲染指定页填充预览窗口（翻页信号回调 / 初始打开共用）。

        优先 OCR 原始块（细粒度，可双击编辑），无则回退 text_layers（粗块仅可视化），
        都没有则显示纯页面图（无高亮）。
        """
        session = self._session_mgr.active_session
        if session is None or self._preview_window is None:
            return
        page_info = session.pdf_document.get_page(page_idx)
        if page_info is None:
            return
        with session.doc_lock:
            pixmap = PdfService.render_page(session.doc, page_idx, dpi=150)
        win = self._preview_window
        assert win is not None
        if page_info.ocr_text_blocks:
            win.set_ocr_blocks(page_idx, page_info.ocr_text_blocks, pixmap)
            win.setWindowTitle(
                f"文字层预览 — 第{page_idx + 1}页 ({len(page_info.ocr_text_blocks)}个文字块)"
            )
        elif page_info.text_layers:
            with session.doc_lock:
                page_rect = session.doc[page_idx].rect
            win.set_highlight(
                pixmap,
                page_info.text_layers,
                render_dpi=150,
                page_rect=page_rect,
                source="pdf",
            )
            win.setWindowTitle(
                f"文字层预览 — 第{page_idx + 1}页 ({len(page_info.text_layers)}个文字块)"
            )
        else:
            win.set_page_pixmap(pixmap)
            win.setWindowTitle(f"文字层预览 — 第{page_idx + 1}页 (无文字层)")

    def _close_preview_window_if_open(self) -> None:
        """关闭预览窗口（若有）。在切换文件/删除页时调用，避免 _page_indices 失效。

        窗口的 _page_indices 存的是打开时的页索引；文档结构变化（换文件/删页）后
        这些索引会失效或错位，翻页会渲染到错误的页。简单稳妥的做法是关闭重开。
        """
        if self._preview_window is not None and self._preview_window.isVisible():
            self._preview_window.close()

    # ---- text layer operations --------------------------------------

    def _load_ocr_prefs(self) -> tuple[PdfGlobalSettings, OCROptions | None]:
        """读取 OCR 偏好；失败时回退默认值。供各添加文字层入口复用。"""
        from vibeocr.utils.ocr_preferences import OCRPreferences

        try:
            prefs = OCRPreferences.instance()
            return prefs.get_pdf_settings(), prefs.get_pdf_pipeline_options()
        except RuntimeError:
            from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

            return PdfGlobalSettings(), None

    def _begin_ocr_ui(self, indices: list[int]) -> None:
        """启动 OCR 前的 UI 复位：进度条 + 禁用文件/操作按钮。"""
        self._progress_bar.setRange(0, len(indices))
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)
        self._btn_cancel.setVisible(True)
        self._set_file_buttons_enabled(False)
        self._btn_open.setEnabled(False)
        self._btn_add_file.setEnabled(False)

    def _on_add_text_layer_for_pages_without_layer(self) -> None:
        """一键为当前文件所有无文字层页面添加 OCR 文字层（不弹防重复框）。"""
        session = self._session_mgr.active_session
        if session is None:
            return

        indices = self._session_mgr.get_pages_without_text_layer(session.file_path)
        if not indices:
            QMessageBox.information(
                self, "添加文字层", "当前文件所有页面均已有文字层。"
            )
            return

        if not self._session_mgr.is_ocr_ready:
            QMessageBox.warning(
                self,
                "OCR 服务未就绪",
                "OCR 服务尚未初始化，请等待服务启动完成。",
            )
            return

        reply = QMessageBox.question(
            self,
            "添加文字层",
            f"将对 {len(indices)} 个无文字层页面执行 OCR 并添加隐形文字层。\n"
            "建议先另存为备份。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        pdf_settings, ocr_options = self._load_ocr_prefs()
        self._begin_ocr_ui(indices)

        # 这些页本就无文字层，overwrite=False（安全默认）
        self._session_mgr.start_ocr(
            indices,
            ocr_options=ocr_options,
            pdf_settings=pdf_settings,
            overwrite=False,
        )

    def _prompt_overwrite_choice(self, has_layer_count: int, total: int) -> int:
        """选中页中部分已有文字层时，询问用户如何处理。

        Returns:
            0 = 跳过已有文字层的页（默认推荐）
            1 = 删除已有文字层后重新添加
            2 = 取消
        """
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setWindowTitle("添加文字层")
        msg.setText(
            f"选中的 {total} 页中有 {has_layer_count} 页已有文字层。\n"
            f"如何处理这些已有文字层的页面？"
        )
        skip_btn = msg.addButton(
            "跳过已有文字层的页（推荐）", QMessageBox.ButtonRole.AcceptRole
        )
        replace_btn = msg.addButton("删除后重新添加", QMessageBox.ButtonRole.AcceptRole)
        cancel_btn = msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(skip_btn)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked is replace_btn:
            return 1
        if clicked is cancel_btn:
            return 2
        return 0

    def _on_add_text_layer(self) -> None:
        session = self._session_mgr.active_session
        if session is None:
            return

        indices = self._get_selected_page_indices()
        if not indices:
            indices = list(range(session.pdf_document.page_count))

        if not self._session_mgr.is_ocr_ready:
            QMessageBox.warning(
                self,
                "OCR 服务未就绪",
                "OCR 服务尚未初始化，请等待服务启动完成。",
            )
            return

        # 未保存编辑检查：OCR 应基于已落盘的状态，避免渲染内存态与
        # 后续保存的文件不一致。遵循同类软件惯例：识别前要求先保存。
        if session.is_modified:
            reply = QMessageBox.question(
                self,
                "未保存的修改",
                f"{Path(session.file_path).name} 有未保存的修改（旋转/删除页面等）。\n"
                "OCR 需基于已保存的状态执行，是否先保存？",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Save:
                return
            self._on_save()
            # 保存失败或被取消时，is_modified 仍为 True → 中止
            if session.is_modified:
                return

        # 软防护：统计选中页中已有文字层的数量，决定是否弹防重复框
        pages = session.pdf_document.pages
        has_layer_count = sum(1 for i in indices if pages[i].has_text_layer)
        overwrite = False
        if has_layer_count > 0:
            choice = self._prompt_overwrite_choice(has_layer_count, len(indices))
            if choice == 2:
                return
            overwrite = choice == 1

        # 从偏好读取 PDF 配置（使用 OCRPreferences 公共 API）
        from vibeocr.utils.ocr_preferences import OCRPreferences

        try:
            prefs = OCRPreferences.instance()
            pdf_settings = prefs.get_pdf_settings()
            ocr_options = prefs.get_pdf_pipeline_options()
        except RuntimeError:
            from vibeocr.models.pdf_ocr_options import PdfGlobalSettings

            pdf_settings = PdfGlobalSettings()
            ocr_options = None

        verb = "删除后重新添加" if overwrite else "跳过已有文字层页"
        reply = QMessageBox.question(
            self,
            "添加文字层",
            f"将对 {len(indices)} 页执行 OCR 并添加隐形文字层（{verb}）。\n"
            "建议先另存为备份。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        pdf_settings, ocr_options = self._load_ocr_prefs()
        self._begin_ocr_ui(indices)

        self._session_mgr.start_ocr(
            indices,
            ocr_options=ocr_options,
            pdf_settings=pdf_settings,
            overwrite=overwrite,
        )

    def _on_delete_text_layer(self) -> None:
        session = self._session_mgr.active_session
        if session is None:
            return
        indices = self._get_selected_page_indices()
        if not indices:
            QMessageBox.information(self, "删除文字层", "请先选择页面。")
            return

        reply = QMessageBox.question(
            self,
            "删除文字层",
            f"将删除选中 {len(indices)} 页的文字层。\n建议先另存为备份。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # 异步：后台逐页词级 redact，主线程不阻塞
        self._progress_bar.setRange(0, len(indices))
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)
        self._set_file_buttons_enabled(False)

        self._session_mgr.delete_text_layers_async(indices)

    def _on_preview_text_layer(self) -> None:
        """打开预览窗口浏览文字层（可翻页，无文字层页显示纯页面图）。"""
        session = self._session_mgr.active_session
        if session is None:
            return
        indices = self._get_selected_page_indices()
        if not indices:
            QMessageBox.information(self, "预览文字层", "请先选择页面。")
            return
        self._open_preview(indices[0])

    # ---- cancel -----------------------------------------------------

    def _on_cancel(self) -> None:
        self._session_mgr.cancel_ocr()

    # ---- public API for MainWindow ----------------------------------

    def set_ocr_service(self, service: OCRServiceBase) -> None:
        """设置 OCR 服务实例（由 MainWindow 调用）。"""
        self._session_mgr.set_ocr_service(service)

    def shutdown(self) -> None:
        """清理资源（由 MainWindow 调用）。"""
        self._session_mgr.shutdown()

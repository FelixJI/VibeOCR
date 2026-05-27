"""PDF 处理标签页"""

import logging
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
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
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from vibeocr.services.pdf_service import PdfService
from vibeocr.views.pdf_preview_window import PdfPreviewWindow

logger = logging.getLogger(__name__)

_THUMBNAIL_SIZE = 160


class PdfTab(QWidget):
    """PDF 处理标签页"""

    ocr_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = PdfService()
        self._preview_window: PdfPreviewWindow | None = None
        self._ocr_service = None
        self._canceled = False
        self._setup_ui()

    def _setup_ui(self) -> None:
        main_splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：缩略图列表
        left_panel = self._create_thumbnail_panel()
        main_splitter.addWidget(left_panel)

        # 右侧：操作面板
        right_panel = self._create_operation_panel()
        main_splitter.addWidget(right_panel)

        main_splitter.setSizes([200, 600])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(main_splitter)

    def _create_thumbnail_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)

        self._thumbnail_list = QListWidget()
        self._thumbnail_list.setFixedWidth(200)
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

        layout.addWidget(self._thumbnail_list)
        return panel

    def _create_operation_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)

        # 文件操作按钮
        file_layout = QHBoxLayout()
        self._btn_open = QPushButton("打开")
        self._btn_open.clicked.connect(self._on_open_file)
        self._btn_save = QPushButton("保存")
        self._btn_save.clicked.connect(self._on_save)
        self._btn_save.setEnabled(False)
        self._btn_save_as = QPushButton("另存为")
        self._btn_save_as.clicked.connect(self._on_save_as)
        self._btn_save_as.setEnabled(False)
        file_layout.addWidget(self._btn_open)
        file_layout.addWidget(self._btn_save)
        file_layout.addWidget(self._btn_save_as)
        file_layout.addStretch()
        layout.addLayout(file_layout)

        # 页面操作组
        page_group = QGroupBox("页面操作")
        page_layout = QHBoxLayout(page_group)
        self._btn_rotate_cw = QPushButton("顺时针90°")
        self._btn_rotate_cw.clicked.connect(lambda: self._on_rotate(90))
        self._btn_rotate_ccw = QPushButton("逆时针90°")
        self._btn_rotate_ccw.clicked.connect(lambda: self._on_rotate(-90))
        self._btn_rotate_all = QPushButton("旋转全部")
        self._btn_rotate_all.clicked.connect(self._on_rotate_all)
        self._btn_delete = QPushButton("删除选中页")
        self._btn_delete.clicked.connect(self._on_delete_pages)
        self._btn_insert = QPushButton("在选中页后插入")
        self._btn_insert.clicked.connect(self._on_insert_page)
        page_layout.addWidget(self._btn_rotate_cw)
        page_layout.addWidget(self._btn_rotate_ccw)
        page_layout.addWidget(self._btn_rotate_all)
        page_layout.addWidget(self._btn_delete)
        page_layout.addWidget(self._btn_insert)
        layout.addWidget(page_group)

        # 文字层操作组
        text_group = QGroupBox("文字层操作")
        text_layout = QVBoxLayout(text_group)
        text_btn_layout = QHBoxLayout()
        self._btn_add_text_layer = QPushButton("添加文字层")
        self._btn_add_text_layer.clicked.connect(self._on_add_text_layer)
        self._btn_del_text_layer = QPushButton("删除文字层")
        self._btn_del_text_layer.clicked.connect(self._on_delete_text_layer)
        self._btn_preview_text_layer = QPushButton("预览文字层")
        self._btn_preview_text_layer.clicked.connect(self._on_preview_text_layer)
        text_btn_layout.addWidget(self._btn_add_text_layer)
        text_btn_layout.addWidget(self._btn_del_text_layer)
        text_btn_layout.addWidget(self._btn_preview_text_layer)
        text_layout.addLayout(text_btn_layout)

        self._layer_status_label = QLabel("未打开文件")
        text_layout.addWidget(self._layer_status_label)
        layout.addWidget(text_group)

        # 进度区域
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._btn_cancel = QPushButton("取消")
        self._btn_cancel.setVisible(False)
        progress_layout = QHBoxLayout()
        progress_layout.addWidget(self._progress_bar)
        progress_layout.addWidget(self._btn_cancel)
        layout.addLayout(progress_layout)

        # 状态标签
        self._status_label = QLabel("")
        layout.addWidget(self._status_label)

        layout.addStretch()

        # 初始状态：禁用所有操作按钮
        self._set_file_buttons_enabled(False)

        return panel

    def _set_file_buttons_enabled(self, enabled: bool) -> None:
        for btn in (
            self._btn_save,
            self._btn_save_as,
            self._btn_rotate_cw,
            self._btn_rotate_ccw,
            self._btn_rotate_all,
            self._btn_delete,
            self._btn_insert,
            self._btn_add_text_layer,
            self._btn_del_text_layer,
            self._btn_preview_text_layer,
        ):
            btn.setEnabled(enabled)

    # --- File operations ---

    def _on_open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "打开 PDF 文件", "", "PDF 文件 (*.pdf)"
        )
        if not path:
            return
        if self._service.is_open():
            self._confirm_close()
        try:
            self._service.open(path)
        except (FileNotFoundError, RuntimeError) as e:
            QMessageBox.warning(self, "打开失败", str(e))
            return
        self._refresh_thumbnails()
        self._set_file_buttons_enabled(True)
        self._update_status()
        self._update_layer_status()

    def _refresh_thumbnails(self) -> None:
        """刷新缩略图列表。"""
        doc = self._service.document
        if doc is None:
            return
        self._thumbnail_list.clear()
        for page_info in doc.pages:
            pixmap = self._service.render_page(
                page_info.page_index, dpi=doc.thumbnail_dpi
            )
            scaled = pixmap.scaled(
                _THUMBNAIL_SIZE,
                _THUMBNAIL_SIZE,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            item = QListWidgetItem(QIcon(scaled), f"第 {page_info.page_index + 1} 页")
            item.setData(Qt.ItemDataRole.UserRole, page_info.page_index)
            self._thumbnail_list.addItem(item)

    def _update_status(self) -> None:
        doc = self._service.document
        if doc is None:
            self._status_label.setText("")
            return
        name = Path(doc.file_path).name if doc.file_path else ""
        modified = " (未保存)" if doc.is_modified else ""
        self._status_label.setText(f"{name} | {doc.page_count} 页{modified}")
        self._btn_save.setEnabled(doc.is_modified)

    def _update_layer_status(self) -> None:
        doc = self._service.document
        if doc is None:
            self._layer_status_label.setText("未打开文件")
            return
        lines = []
        for p in doc.pages:
            if p.has_text_layer:
                lines.append(f"第{p.page_index + 1}页: {len(p.text_layers)}层文字层")
            else:
                status = "扫描件" if p.is_scanned else "无文字层"
                lines.append(f"第{p.page_index + 1}页: {status}")
        self._layer_status_label.setText("\n".join(lines))

    def _get_selected_page_indices(self) -> list[int]:
        indices = []
        for item in self._thumbnail_list.selectedItems():
            idx = item.data(Qt.ItemDataRole.UserRole)
            if idx is not None:
                indices.append(idx)
        return sorted(set(indices))

    def _confirm_close(self) -> bool:
        doc = self._service.document
        if doc and doc.is_modified:
            reply = QMessageBox.question(
                self,
                "未保存的修改",
                "当前文件有未保存的修改，是否保存？",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if reply == QMessageBox.StandardButton.Save:
                self._on_save()
            elif reply == QMessageBox.StandardButton.Cancel:
                return False
        self._service.close()
        return True

    def _on_save(self) -> None:
        try:
            self._service.save()
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))
            return
        self._update_status()

    def _on_save_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "另存为", "", "PDF 文件 (*.pdf)")
        if not path:
            return
        try:
            self._service.save(path)
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))
            return
        self._update_status()

    # --- Page operations ---

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
        if idx is None:
            return
        self._open_preview(idx)

    def _on_pages_reordered(self) -> None:
        """拖拽排序后同步 PdfService。"""
        for new_row in range(self._thumbnail_list.count()):
            item = self._thumbnail_list.item(new_row)
            old_idx = item.data(Qt.ItemDataRole.UserRole)
            if old_idx is not None and old_idx != new_row:
                self._service.move_page(old_idx, new_row)
                break
        self._refresh_thumbnails()

    def _on_rotate(self, angle: int) -> None:
        indices = self._get_selected_page_indices()
        if not indices:
            return
        self._service.rotate_pages(indices, angle)
        self._refresh_thumbnails()
        self._update_status()

    def _on_rotate_all(self) -> None:
        reply = QMessageBox.question(
            self,
            "旋转全部页面",
            "确定旋转全部页面 90°？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._service.rotate_all_pages(90)
        self._refresh_thumbnails()
        self._update_status()

    def _on_delete_pages(self) -> None:
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
        self._service.delete_pages(indices)
        self._refresh_thumbnails()
        self._update_status()
        self._update_layer_status()

    def _on_insert_page(self) -> None:
        indices = self._get_selected_page_indices()
        after_index = indices[0] if indices else 0

        path, _ = QFileDialog.getOpenFileName(
            self, "选择要插入的 PDF", "", "PDF 文件 (*.pdf)"
        )
        if path:
            try:
                self._service.insert_pages_from(path, after_index)
            except Exception as e:
                QMessageBox.warning(self, "插入失败", str(e))
                return
        else:
            self._service.insert_blank_page(after_index)
        self._refresh_thumbnails()
        self._update_status()
        self._update_layer_status()

    # --- Preview ---

    def _open_preview_for_selected(self) -> None:
        indices = self._get_selected_page_indices()
        if indices:
            self._open_preview(indices[0])

    def _open_preview(self, page_index: int) -> None:
        doc = self._service.document
        if doc is None:
            return
        pixmap = self._service.render_page(page_index, dpi=150)
        if self._preview_window is None:
            self._preview_window = PdfPreviewWindow()
        assert self._preview_window is not None
        self._preview_window.set_page_pixmap(pixmap)
        self._preview_window.show()
        self._preview_window.raise_()

    # --- Text layer operations ---

    def _on_add_text_layer(self) -> None:
        doc = self._service.document
        if doc is None:
            return

        indices = self._get_selected_page_indices()
        if not indices:
            indices = list(range(doc.page_count))

        if not hasattr(self, "_ocr_service") or self._ocr_service is None:
            QMessageBox.warning(
                self,
                "OCR 服务未就绪",
                "OCR 服务尚未初始化，请等待服务启动完成。",
            )
            return

        reply = QMessageBox.question(
            self,
            "添加文字层",
            f"将对 {len(indices)} 页执行 OCR 并添加隐形文字层。\n"
            "建议先另存为备份。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._run_add_text_layer(indices)

    def _run_add_text_layer(self, page_indices: list[int]) -> None:
        self._canceled = False
        self._btn_cancel.clicked.connect(self._on_cancel_ocr)
        self._progress_bar.setRange(0, len(page_indices))
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(True)
        self._btn_cancel.setVisible(True)
        self._set_file_buttons_enabled(False)
        self._btn_open.setEnabled(False)

        total = len(page_indices)
        for i, page_idx in enumerate(page_indices):
            if self._canceled:
                break
            self._progress_bar.setValue(i)
            self._status_label.setText(f"正在识别第 {i + 1}/{total} 页...")

            img_array = self._service.render_page_as_array(page_idx)
            if img_array.size == 0:
                continue
            try:
                from vibeocr.models.ocr_options import OCROptions

                ocr = self._ocr_service
                if ocr is None:
                    continue
                result = ocr.recognize(img_array, OCROptions())
                self._service.add_text_layer(page_idx, result)
            except Exception as e:
                logger.error("OCR 失败 (页 %d): %s", page_idx, e)
                continue

            if i % 5 == 0 or i == total - 1:
                self._refresh_thumbnails()

        self._progress_bar.setVisible(False)
        self._btn_cancel.setVisible(False)
        self._set_file_buttons_enabled(True)
        self._btn_open.setEnabled(True)
        self._update_status()
        self._update_layer_status()
        msg = "添加文字层完成" if not self._canceled else "已取消"
        self._status_label.setText(msg)

    def _on_cancel_ocr(self) -> None:
        self._canceled = True

    def set_ocr_service(self, service) -> None:
        """设置 OCR 服务实例（由 MainWindow 调用）。"""
        self._ocr_service = service

    def _on_preview_text_layer(self) -> None:
        indices = self._get_selected_page_indices()
        if not indices:
            QMessageBox.information(self, "预览文字层", "请先选择页面。")
            return
        page_idx = indices[0]
        doc = self._service.document
        if doc is None:
            return
        page_info = doc.get_page(page_idx)
        if page_info is None or not page_info.text_layers:
            QMessageBox.information(self, "预览文字层", "选中页面无文字层。")
            return

        pixmap = self._service.render_page(page_idx, dpi=150)
        if self._preview_window is None:
            self._preview_window = PdfPreviewWindow()
        assert self._preview_window is not None
        self._preview_window.set_page_pixmap(pixmap)
        self._preview_window._canvas.set_highlight_layers(page_info.text_layers)
        self._preview_window.show()
        self._preview_window.raise_()

    def _on_delete_text_layer(self) -> None:
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

        for idx in indices:
            self._service.delete_text_layers(idx)
        self._refresh_thumbnails()
        self._update_status()
        self._update_layer_status()

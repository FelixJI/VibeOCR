# src/vibeocr/views/tabs/pdf_tab.py
"""PDF 处理标签页 — 多文件 + 异步加载/OCR。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, cast

from PySide6.QtCore import (
    QAbstractListModel,
    QItemSelectionModel,
    QModelIndex,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
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
    QListView,
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

from vibeocr.core.constants import Constants
from vibeocr.managers.pdf_session_manager import PdfSessionManager, _wait_thread
from vibeocr.ui.theme import Colors
from vibeocr.utils.thumbnail_lru_cache import ThumbnailLruCache
from vibeocr.views.pdf_preview_window import PdfPreviewWindow

if TYPE_CHECKING:
    from vibeocr.models.ocr_options import OCROptions
    from vibeocr.models.pdf_ocr_options import PdfGlobalSettings
    from vibeocr.models.pdf_session import PdfSession
    from vibeocr.services.ocr_service_base import OCRServiceBase
    from vibeocr.workers.pdf_render_thumb_ipc_worker import ThumbnailIpcWorker

logger = logging.getLogger(__name__)

_THUMBNAIL_SIZE = 160
_GRID_CELL_SIZE = 40  # 文字层状态网格单格尺寸（正方形）

# 缓存占位灰图（缩略图未渲染时显示），避免每次 data() 调用都新建 QPixmap/QIcon。
_PLACEHOLDER_PIXMAP: QPixmap | None = None
_PLACEHOLDER_ICON: QIcon | None = None


def _placeholder_pixmap() -> QPixmap:
    """缩略图占位灰图（懒初始化单例）。供 ThumbnailModel.data() 与 PdfTab 共用。"""
    global _PLACEHOLDER_PIXMAP
    if _PLACEHOLDER_PIXMAP is None:
        pm = QPixmap(_THUMBNAIL_SIZE, _THUMBNAIL_SIZE)
        pm.fill(Qt.GlobalColor.lightGray)
        _PLACEHOLDER_PIXMAP = pm
    return _PLACEHOLDER_PIXMAP


def _placeholder_icon() -> QIcon:
    """占位 QIcon 单例：data() cache miss 时返回，避免每次新建 QIcon 开销。"""
    global _PLACEHOLDER_ICON
    if _PLACEHOLDER_ICON is None:
        _PLACEHOLDER_ICON = QIcon(_placeholder_pixmap())
    return _PLACEHOLDER_ICON

# 文字层网格 item 数据角色：_LAYER_ROLE 存 page_index，_HAS_LAYER_ROLE 存 has_text_layer
_LAYER_ROLE = Qt.ItemDataRole.UserRole
_HAS_LAYER_ROLE = Qt.ItemDataRole.UserRole + 1
_DESKEWED_ROLE = Qt.ItemDataRole.UserRole + 2  # 存 deskewed（本会话是否被自动摆正纠正）


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

        # 已纠偏标记：右上角橙色小圆点（底色保持不变，两维信息并存）
        deskewed = index.data(_DESKEWED_ROLE)
        if deskewed:
            dot_d = 10  # 直径
            dot = QRectF(
                cell.right() - dot_d - 2,
                cell.top() + 2,
                dot_d,
                dot_d,
            )
            painter.setBrush(QColor(Colors.warning))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(dot)

        painter.restore()


class ThumbnailModel(QAbstractListModel):
    """缩略图列表虚拟化数据模型（数据源 = 活动会话的 pages）。

    按需渲染：data(DecorationRole) 查 LRU 缓存，命中返回 QIcon；未命中返回
    None（占位）并投递渲染请求到后台 worker。滚动到可见页时由
    ThumbnailListView 主动请求渲染。渲染完成后 thumbnail_ready 回调
    回填缓存并 dataChanged 通知视图重绘。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._session: PdfSession | None = None
        self._cache = ThumbnailLruCache(capacity=200)
        self._render_worker: ThumbnailIpcWorker | None = None
        self._render_dpi = 96
        # generation 校验:invalidate(row) 自增该行 gen;请求带 gen,响应带 gen,
        # 只在 gen 匹配时入缓存,丢弃失效后仍在途的旧渲染结果(旋转 ABA)。
        self._gen: dict[int, int] = {}

    def set_session(self, session: PdfSession | None) -> None:
        """切换数据源（切文件/导入时）：停旧 worker、清缓存、起新 worker。"""
        self._stop_render_worker()
        self.beginResetModel()
        self._session = session
        self._cache.clear()
        self._gen.clear()
        self.endResetModel()
        if session is not None:
            self._start_render_worker(session)

    def _start_render_worker(self, session: PdfSession) -> None:
        # 进程化:缩略图走 IPC(client.render_thumbnail → PNG → QPixmap)
        from vibeocr.workers.pdf_render_thumb_ipc_worker import ThumbnailIpcWorker

        mgr = self._get_manager()
        if mgr is None:
            return
        self._render_worker = ThumbnailIpcWorker(
            client=mgr.backend_client,
            session_id=session.session_id,
            size=_THUMBNAIL_SIZE,
        )
        self._render_worker.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._render_worker.start()

    def _get_manager(self):
        """从父 PdfTab 拿 manager(渲染需要 backend_client)。延迟绑定避免构造期依赖。"""
        parent = self.parent()
        if parent is None:
            return None
        # parent 通常是 PdfTab(ThumbnailModel 作为 QAbstractListModel,
        # 由 PdfTab 持有,parent() 返回 PdfTab)
        return getattr(parent, "_session_mgr", None) or getattr(parent, "manager", None)

    def _stop_render_worker(self) -> None:
        if self._render_worker is not None:
            self._render_worker.cancel()
            _wait_thread(
                self._render_worker,
                timeout_ms=Constants.Timeout.Ms.PDF_WORKER_TERMINATE_WAIT,
            )
            self._render_worker = None

    def _on_thumbnail_ready(self, page_index: int, pixmap: object, gen: int) -> None:
        """IPC 渲染回调:generation 校验后回填 LRU 缓存。"""
        # 丢弃陈旧结果(gen 不匹配说明该页已被 invalidate,新请求在路上)
        if self._gen.get(page_index, 0) != gen:
            return
        assert isinstance(pixmap, QPixmap)
        self._cache.put(page_index, pixmap)
        if 0 <= page_index < self.rowCount():
            idx = self.index(page_index, 0)
            self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])

    def request_render(self, row: int) -> None:
        """请求渲染指定行（已在缓存则跳过）。滚动监听 / data() miss 时调用。"""
        if not self._ensure_render_worker_alive():
            return
        if row in self._cache:
            return
        assert self._render_worker is not None
        self._render_worker.request(row, self._gen.get(row, 0))

    def request_range(self, first: int, last: int) -> None:
        """请求渲染 [first, last] 行范围（去重由 worker 处理）。"""
        if not self._ensure_render_worker_alive():
            return
        assert self._render_worker is not None
        for row in range(max(0, first), min(self.rowCount(), last + 1)):
            if row not in self._cache:
                self._render_worker.request(row, self._gen.get(row, 0))

    def _ensure_render_worker_alive(self) -> bool:
        """确保 render worker 存活:None 或已结束则重启。"""
        if self._session is None:
            return False
        if self._render_worker is not None and not self._render_worker.isFinished():
            return True
        self._stop_render_worker()
        self._start_render_worker(self._session)
        return True

    def invalidate(self, row: int) -> None:
        """失效单页缓存(旋转后),自增 gen 触发该行重渲。"""
        self._cache.invalidate(row)
        self._gen[row] = self._gen.get(row, 0) + 1
        if 0 <= row < self.rowCount():
            idx = self.index(row, 0)
            self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])
            self.request_render(row)

    def invalidate_all(self) -> None:
        """失效全部缓存(旋转全部后),自增全部 gen,触发可见行重渲。"""
        self._cache.clear()
        for row in range(self.rowCount()):
            self._gen[row] = self._gen.get(row, 0) + 1
        if self.rowCount() > 0:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(self.rowCount() - 1, 0),
                [Qt.ItemDataRole.DecorationRole],
            )

    def session(self) -> PdfSession | None:
        return self._session

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        if parent.isValid() or self._session is None:
            return 0
        return len(self._session.pdf_document.pages)

    def _page_at_row(self, row: int):
        if self._session is None:
            return None
        pages = self._session.pdf_document.pages
        if 0 <= row < len(pages):
            return pages[row]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        page_info = self._page_at_row(index.row())
        if page_info is None:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return f"第 {page_info.page_index + 1} 页"
        if role == Qt.ItemDataRole.DecorationRole:
            pixmap = self._cache.get(index.row())
            if pixmap is not None:
                return QIcon(pixmap)
            # 缓存未命中：返回占位图标。不在 data() 里调 request_render——
            # 滚动时 Qt 对每行多次查 data(DecorationRole)，每次 miss 调
            # request_render 会争 _pending_lock + 放大开销。渲染请求统一由
            # ThumbnailListView 的 visible_range_changed → request_range 驱动
            # （scroll/showEvent 触发，去抖 50ms）。
            return _placeholder_icon()
        if role == Qt.ItemDataRole.UserRole:
            return page_info.page_index
        return None


class ThumbnailListView(QListView):
    """支持拖拽排序 + 按需渲染的缩略图列表视图。

    基类 QListView 的 InternalMove 依赖模型的 insertRows/removeRows，
    但本项目的模型是只读视图（数据源 = session.pdf_document.pages）。
    故在此拦截 dropEvent：根据拖拽前后行的 UserRole(page_index) 计算
    new_order，emit pages_reordered 交由 PdfTab 调用 PdfService.reorder_pages
    统一重排文档与模型数据源，避免数据双写。

    按需渲染：滚动/resize 时计算可见行范围，emit visible_range_changed，
    由 PdfTab 调用 model.request_range 触发后台渲染（去抖 50ms 合并）。
    """

    pages_reordered = Signal(list)  # new_order: list[int]
    visible_range_changed = Signal(int, int)  # (first_visible_row, last_visible_row)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # 滚动去抖：连续滚动合并为一次渲染请求
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.timeout.connect(self._emit_visible_range)

    def scrollContentsBy(self, dx, dy) -> None:  # noqa: N802 — Qt API 命名
        super().scrollContentsBy(dx, dy)
        self._schedule_visible_range()

    def resizeEvent(self, event) -> None:  # noqa: N802 — Qt API 命名
        super().resizeEvent(event)
        self._schedule_visible_range()

    def showEvent(self, event) -> None:  # noqa: N802 — Qt API 命名
        super().showEvent(event)
        self._schedule_visible_range()

    def _schedule_visible_range(self) -> None:
        """防抖：50ms 内合并多次滚动/resize 为一次渲染请求。"""
        self._scroll_timer.start(50)

    def _emit_visible_range(self) -> None:
        model = self.model()
        if model is None or model.rowCount() == 0:
            return
        vp = self.viewport()
        first_idx = self.indexAt(vp.rect().topLeft())
        last_idx = self.indexAt(vp.rect().bottomLeft())
        first = first_idx.row() if first_idx.isValid() else 0
        last = last_idx.row() if last_idx.isValid() else model.rowCount() - 1
        if first < 0:
            first = 0
        if last < 0:
            last = model.rowCount() - 1
        self.visible_range_changed.emit(first, last)

    def dropEvent(self, event) -> None:  # noqa: N802 — Qt API 命名
        if event.source() is not self:
            super().dropEvent(event)
            return
        model = self.model()
        if model is None or model.rowCount() == 0:
            event.ignore()
            return
        n = model.rowCount()
        before = [
            model.data(model.index(r, 0), Qt.ItemDataRole.UserRole) for r in range(n)
        ]
        source_rows = sorted({i.row() for i in self.selectedIndexes()})
        if not source_rows:
            event.ignore()
            return
        # 目标行：用 indexAt 推算鼠标所在行 + dropIndicatorPosition 判定上/下
        target_row = self._target_row_at(event.position().toPoint())
        moved = [before[r] for r in source_rows]
        remaining = [v for r, v in enumerate(before) if r not in source_rows]
        insert_at = min(target_row, len(remaining))
        new_order = remaining[:insert_at] + moved + remaining[insert_at:]
        if new_order == before:
            event.ignore()
            return
        event.accept()
        self.pages_reordered.emit(new_order)

    def _target_row_at(self, pos) -> int:
        """根据鼠标位置推算应插入的行号。"""
        model = self.model()
        if model is None or model.rowCount() == 0:
            return 0
        idx = self.indexAt(pos)
        if not idx.isValid():
            return model.rowCount()  # 视口空白：追加到末尾
        row = idx.row()
        rect = self.rectForIndex(idx)
        # 鼠标在格子的下半部分 → 插到下一行
        if pos.y() > rect.center().y():
            row += 1
        return row


class PdfTab(QWidget):
    """PDF 处理标签页。"""

    ocr_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._session_mgr = PdfSessionManager(self)
        self._preview_window: PdfPreviewWindow | None = None
        # 网格 ↔ 缩略图双向同步的重入保护，避免 itemSelectionChanged 递归
        self._syncing_selection = False
        # 批量异步打开期间的失败项收集（open_done 后统一弹一次提示）
        self._open_errors: list[tuple[str, str]] = []
        # 批量异步打开期间抑制 combo box 自动切换（每个 session_added 否则都会
        # 触发 setCurrentIndex → switch_session → 全量重建，抵消异步优化）。
        self._batch_opening = False
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

        self._thumbnail_model = ThumbnailModel(self)
        self._thumbnail_list = ThumbnailListView()
        self._thumbnail_list.setMinimumWidth(120)
        self._thumbnail_list.setIconSize(
            QPixmap(_THUMBNAIL_SIZE, _THUMBNAIL_SIZE).size()
        )
        self._thumbnail_list.setModel(self._thumbnail_model)
        # IconMode + 固定 gridSize：让 QListView 一次性算出全部内容高度，
        # 滚动条稳定不回弹（此前默认 ListMode + Batched 布局下高度计算
        # 不稳定，向下滚动后滚动条跳回）。UniformItemSizes 配合 gridSize
        # 实现虚拟化，无需 Batched。
        self._thumbnail_list.setViewMode(QListView.ViewMode.IconMode)
        self._thumbnail_list.setFlow(QListView.Flow.TopToBottom)
        self._thumbnail_list.setWrapping(False)
        self._thumbnail_list.setResizeMode(QListView.ResizeMode.Adjust)
        self._thumbnail_list.setMovement(QListView.Movement.Static)
        self._thumbnail_list.setUniformItemSizes(True)
        # gridSize：缩略图 160 + 8px 边距宽；高 +28 给"第 N 页"文字标签留空间
        self._thumbnail_list.setGridSize(
            QSize(_THUMBNAIL_SIZE + 8, _THUMBNAIL_SIZE + 28)
        )
        self._thumbnail_list.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self._thumbnail_list.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove
        )
        self._thumbnail_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._thumbnail_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._thumbnail_list.customContextMenuRequested.connect(
            self._on_thumbnail_context_menu
        )
        self._thumbnail_list.doubleClicked.connect(
            self._on_thumbnail_double_clicked
        )
        self._thumbnail_list.pages_reordered.connect(
            self._on_pages_reordered_with_order
        )
        # 按需渲染：滚动时请求可见行渲染
        self._thumbnail_list.visible_range_changed.connect(
            self._thumbnail_model.request_range
        )
        # 反向联动：缩略图选中变化 → 状态列表同步当前行
        self._thumbnail_list.selectionModel().selectionChanged.connect(
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
        self._btn_remove_file = QPushButton("移除文件")
        self._btn_remove_file.setToolTip("从列表中移除当前文件（不删除源文件）")
        self._btn_remove_file.clicked.connect(self._on_remove_file)
        self._btn_remove_file.setEnabled(False)
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
        file_layout.addWidget(self._btn_remove_file)
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
        # stretch=1：方格子滚动区吃满 text_group 内剩余垂直空间（大文件自动滚动）。
        text_layout.addWidget(grid_scroll, 1)
        # stretch=1：文字层组吃满面板内剩余垂直空间，按钮区/进度条固定在上下。
        layout.addWidget(text_group, 1)

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
        mgr.deskew_progress.connect(self._on_deskew_progress)
        mgr.deskew_done.connect(self._on_deskew_done)
        mgr.deskew_failed.connect(self._on_deskew_failed)
        mgr.open_progress.connect(self._on_open_progress)
        mgr.open_failed.connect(self._on_open_failed)
        mgr.open_done.connect(self._on_open_done)
        mgr.thumbnails_invalidated.connect(self._on_thumbnails_invalidated)

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
        # 批量导入期间不逐个切换（否则每个文件触发 switch_session→全量重建）。
        # 仅在 open_done 后切换到第一个新文件。
        if not self._batch_opening:
            self._file_selector.setCurrentIndex(self._file_selector.count() - 1)
        self._btn_export_all.setEnabled(True)

    def _on_session_removed(self, file_path: str) -> None:
        for i in range(self._file_selector.count()):
            if self._file_selector.itemData(i) == file_path:
                self._file_selector.removeItem(i)
                break
        if self._file_selector.count() == 0:
            self._btn_export_all.setEnabled(False)

    def _on_active_changed(self, file_path: str | None) -> None:
        # 切换文件：预览窗口的 _page_indices 指向旧文档，关闭它避免翻页到失效索引。
        self._close_preview_window_if_open()
        # 全量重建期间抑制双向选中同步：reset 会触发 selectionChanged，
        # 此时两侧控件尚处于不一致的中间态，让同步逻辑静默直到重建完成。
        self._syncing_selection = True
        try:
            self._thumbnail_model.set_session(self._session_mgr.active_session)
            self._update_status()
            self._update_layer_status()
        finally:
            self._syncing_selection = False
        has_doc = self._session_mgr.active_session is not None
        self._set_file_buttons_enabled(has_doc)
        # 模型 reset 后主动触发一次可见范围请求：程序性 reset 不会产生
        # showEvent/scrollContentsBy/resizeEvent，否则首次打开时可见页永不进渲染队列。
        self._thumbnail_list._schedule_visible_range()

    def _on_page_loaded(self, file_path: str, page_index: int) -> None:
        """文字层 worker 逐页完成：更新文字层网格格子（缩略图由按需 worker 渲）。"""
        session = self._session_mgr.active_session
        if session is None or session.file_path != file_path:
            return
        # 缩略图不在此处理（按需渲染由 ThumbnailModel 驱动）；
        # 仅逐页更新文字层网格状态 + 汇总。
        self._update_layer_grid_page(page_index)

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

    def on_ocr_queued(self, message: str) -> None:
        """OCR worker 忙碌（如预热中）时，识别请求已排队。

        WorkerManager 是单 worker 串行队列：预热/预加载独占 worker 时，
        后续 OCR（添加文字层/自动摆正）会排队等待（最长 300s）。
        此前 PDF tab 无提示，用户以为"卡死"。这里明确告知"排队中，会自动执行"，
        并用不确定进度条表示等待态。OCR 真正开始后 _on_render_progress_update
        会切回确定进度。
        """
        self._status_label.setText(f"{message}（完成后自动继续）")
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
        """mutate 逐页/整体完成。

        - {"page": ...}:逐页 payload(删除文字层逐页),更新 grid 格子。
        - {"diff_applied": True}:结构变更(旋转/删页/插页/重排)整体完成,
          model 已由 manager apply,此处刷新缩略图模型 + 文字层网格 + 状态。
        """
        session = self._session_mgr.active_session
        if session is None or session.file_path != file_path:
            return
        if isinstance(result, dict):
            if "page" in result:
                self._update_layer_grid_page(result["page"])
            elif result.get("diff_applied"):
                # 结构变更:model 已刷新,重置缩略图模型数据源 + 文字层网格。
                self._after_structural_change()

    def _after_structural_change(self) -> None:
        """结构变更(删页/插页/重排/旋转全部)后统一刷新 UI。

        model 的 pages 已由 manager apply_diff 刷新,缩略图模型读取同一列表,
        故只需 beginResetModel/endResetModel 通知视图重读,并刷新文字层网格。
        """
        session = self._session_mgr.active_session
        if session is None:
            return
        # 删页:清理 loaded_pages 中已不存在的索引
        pending_del = getattr(self, "_pending_delete_indices", None)
        if pending_del:
            session.loaded_pages -= pending_del
            self._pending_delete_indices = None
        # 插页:loaded_pages 失效(索引移位),清空让 UI 按需重判
        extra = getattr(self, "_pending_insert", False)
        if extra:
            session.loaded_pages.clear()
            self._pending_insert = False
        self._refresh_thumbnails()
        self._update_status()
        self._update_layer_status()

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
            self._btn_remove_file,
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
            Qt.TransformationMode.FastTransformation,
        )

    @staticmethod
    def _placeholder_pixmap() -> QPixmap:
        return _placeholder_pixmap()

    def _row_of_page(self, page_index: int) -> int | None:
        """根据 page_info.page_index 查找它在模型中的当前行号（重排后会变化）。"""
        model = self._thumbnail_model
        for r in range(model.rowCount()):
            if model.data(model.index(r, 0), Qt.ItemDataRole.UserRole) == page_index:
                return r
        return None

    def _reorder_thumbnail_model(self, selected_pages: list[int] | None = None) -> None:
        """PdfService.reorder_pages 已改写 pdf_document.pages 顺序，
        模型数据源随之更新，通知视图整体刷新（保留选中 page_index）。

        selected_pages 须在 reorder_pages 之前捕获（调用方负责传入）。
        调用方应在调用前后用 _syncing_selection 抑制选中同步。
        """
        self._thumbnail_model.set_session(self._session_mgr.active_session)
        # 恢复选中（page_index 未变，只是行序变了）
        if selected_pages:
            want = set(selected_pages)
            sm = self._thumbnail_list.selectionModel()
            model = self._thumbnail_model
            sm.clear()
            for r in range(model.rowCount()):
                if model.data(model.index(r, 0), Qt.ItemDataRole.UserRole) in want:
                    sm.select(
                        model.index(r, 0),
                        QItemSelectionModel.SelectionFlag.Select,
                    )

    def _refresh_thumbnails(self) -> None:
        """重置缩略图模型数据源（删页/插页后页结构变化时调用）。"""
        self._thumbnail_model.set_session(self._session_mgr.active_session)

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
            item.setData(_DESKEWED_ROLE, p.deskewed)
            item.setToolTip(self._layer_cell_tooltip(p))
            grid.addItem(item)
        self._update_layer_summary(pages)

    @staticmethod
    def _layer_cell_tooltip(page_info) -> str:
        """生成文字层网格格子的 tooltip（block_count 优先用 OCR 原始块）。"""
        if page_info.has_text_layer:
            if page_info.ocr_text_blocks:
                block_count = len(page_info.ocr_text_blocks)
                tip = f"第{page_info.page_index + 1}页 · 已添加文字层（{block_count}个文本块）"
            elif page_info.text_layers:
                block_count = len(page_info.text_layers)
                tip = f"第{page_info.page_index + 1}页 · 已添加文字层（{block_count}个文本块）"
            else:
                # text_layers 延迟加载（load worker 只判 has_text_layer 不取详情）
                tip = f"第{page_info.page_index + 1}页 · 已添加文字层"
        else:
            tip = f"第{page_info.page_index + 1}页 · 无文字层"
        if getattr(page_info, "deskewed", False):
            tip += " · 已纠偏"
        return tip

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
                item.setData(_DESKEWED_ROLE, page_info.deskewed)
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

    def _sync_selection_to(self, target, page_indices: list[int]) -> None:
        """把给定 page_index 集合同步选中到 target 列表（按 page_index 匹配，清旧选新）。

        target 可以是 QListWidget（网格）或 QListView（缩略图），两者都有
        selectionModel()。两个列表都用 UserRole 存 page_index。
        """
        want = set(page_indices)
        sm = target.selectionModel()
        model = target.model()
        if sm is None or model is None:
            return
        # 逐行：在 want 中则 Select，不在则 Deselect（不清空整列，保留其余）
        for row in range(model.rowCount()):
            idx = model.index(row, 0)
            in_want = model.data(idx, _LAYER_ROLE) in want
            if in_want:
                sm.select(idx, QItemSelectionModel.SelectionFlag.Select)
            else:
                sm.select(idx, QItemSelectionModel.SelectionFlag.Deselect)

    def _get_selected_page_indices(self) -> list[int]:
        model = self._thumbnail_model
        indices = []
        for idx in self._thumbnail_list.selectedIndexes():
            val = model.data(idx, Qt.ItemDataRole.UserRole)
            if val is not None:
                indices.append(val)
        return sorted(set(indices))

    # ---- file operations --------------------------------------------

    def _on_file_selected(self, index: int) -> None:
        # 批量导入期间 addItem 会改变 combo 当前项，忽略其触发的切换
        # （切换在 open_done 后由 _on_open_done 统一完成一次）。
        if self._batch_opening:
            return
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
        # 统一走后台异步打开（fitz.open 在 PdfOpenWorker 线程执行，
        # 避免大文件在主线程阻塞冻结 UI）。单/多文件路径一致。
        # open_sessions_async 已处理"文件已打开则 switch"的语义。
        self._open_errors = []
        self._batch_opening = True
        self._status_label.setText(f"正在打开 0/{len(paths)} 个文件…")
        self._session_mgr.open_sessions_async(paths)

    def _on_open_progress(self, current: int, total: int) -> None:
        self._status_label.setText(f"正在打开 {current}/{total} 个文件…")

    def _on_open_failed(self, file_path: str, error: str) -> None:
        """批量导入时收集失败项，全部完成后统一弹一次提示。"""
        name = Path(file_path).name if file_path else ""
        self._open_errors.append((name, error))

    def _on_open_done(self) -> None:
        """批量打开流程结束：切换到第一个新文件（一次性，避免逐文件重建）。"""
        self._batch_opening = False
        # 切换到活动会话（manager 已设第一个新文件为 active）
        active = self._session_mgr.active_session
        if active is not None:
            for i in range(self._file_selector.count()):
                if self._file_selector.itemData(i) == active.file_path:
                    self._file_selector.setCurrentIndex(i)
                    break
        if self._open_errors:
            lines = "\n".join(f"• {n}: {e}" for n, e in self._open_errors)
            QMessageBox.warning(self, "部分文件打开失败", lines)
            self._open_errors = []

    def _on_add_file(self) -> None:
        self._on_open_file()

    def _on_remove_file(self) -> None:
        """从列表移除当前活动文件（关闭会话，不删除源文件）。

        有未保存修改时弹确认；确认后调 manager.close_session，
        后者 emit session_removed（_on_session_removed 自动从下拉框移除）
        与 active_changed（自动切到剩余文件）。无剩余文件时 UI 清空。
        """
        session = self._session_mgr.active_session
        if session is None:
            return
        name = Path(session.file_path).name
        if session.is_modified:
            reply = QMessageBox.question(
                self,
                "移除文件",
                f"{name} 有未保存的修改，确定移除吗？移除不会保存修改。",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._session_mgr.close_session(session.file_path)
        self._status_label.setText(f"已移除 {name}")

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

    def _on_thumbnail_double_clicked(self, index: QModelIndex) -> None:
        idx = index.data(Qt.ItemDataRole.UserRole)
        if idx is not None:
            self._open_preview(idx)

    def _on_pages_reordered_with_order(self, new_order: list[int]) -> None:
        """用显式 new_order 应用重排(异步 IPC)。

        重排是结构变更,后端 reorder 完成后 manager 通过 mutate_done +
        thumbnails_invalidated 信号通知 UI 刷新(model 已 apply diff)。
        """
        session = self._session_mgr.active_session
        if session is None or not new_order:
            return
        # 必须在 reorder 之前捕获选中:重排后 UserRole 会与原选中行错位。
        selected_pages = self._get_selected_page_indices()
        self._reorder_pending_selection = selected_pages
        self._session_mgr.reorder_async(new_order)
        # UI 刷新在 _on_mutate_done 回调里做(见 _handle_mutate_done_for_reorder)

    def _on_rotate(self, angle: int) -> None:
        session = self._session_mgr.active_session
        if session is None:
            return
        indices = self._get_selected_page_indices()
        if not indices:
            return
        # 异步 IPC:后端旋转 + apply diff + thumbnails_invalidated 由 manager 处理。
        self._session_mgr.rotate_pages_async(indices, angle)
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
        # 异步 IPC:后端旋转全部 + 缩略图失效由 manager 处理。
        self._session_mgr.rotate_pages_async(indices, 90)
        self._update_status()

    def _on_auto_deskew(self) -> None:
        session = self._session_mgr.active_session
        if session is None:
            return
        # 无 OCR 服务时 auto_deskew_async 会静默 return，导致按钮永久禁用
        # （不发 deskew_done/deskew_failed 信号）。此处前置校验并提示。
        if not self._session_mgr.is_ocr_ready:
            QMessageBox.information(
                self,
                "自动摆正",
                "未配置 OCR 服务，无法检测页面方向。请先在设置中选择 OCR 引擎。",
            )
            return
        indices = self._get_selected_page_indices()
        if not indices:
            QMessageBox.information(self, "自动摆正", "请先选中要摆正的页面。")
            return
        # 进度 UI + 独占锁(禁用所有页操作按钮,仅留取消)
        self._progress_bar.setRange(0, 0)  # 不确定(检测阶段无 tick)
        self._progress_bar.setVisible(True)
        self._btn_cancel.setVisible(True)
        self._set_file_buttons_enabled(False)
        self._status_label.setText("正在摆正…")
        self._session_mgr.auto_deskew_async(indices)

    def _on_deskew_page_done(
        self, session_id: str, page_index: int, was_corrected: bool
    ) -> None:
        session = self._session_mgr.active_session
        if session is None or session.file_path != session_id:
            return
        # 缩略图刷新由 manager 在 all_done 时统一 emit thumbnails_invalidated
        # （_on_thumbnails_invalidated 是唯一缩略图失效入口）。此处仅刷新格子，
        # 让已纠偏角标（_DESKEWED_ROLE）即时更新。
        self._update_layer_grid_page(page_index)

    def _on_deskew_progress(
        self, file_path: str, current: int, total: int
    ) -> None:
        """摆正进度:total > 0 时切确定进度条。"""
        session = self._session_mgr.active_session
        if session is None or session.file_path != file_path:
            return
        if total > 0:
            self._progress_bar.setRange(0, total)
            self._progress_bar.setValue(current)
            phase = "识别方向" if current <= total // 2 else "纠正"
            self._status_label.setText(f"正在{phase} {current}/{total}…")
        else:
            self._progress_bar.setRange(0, 0)
            self._status_label.setText("正在摆正…")

    def _on_deskew_done(self, session_id: str, summary) -> None:
        # 收尾进度 UI + 恢复按钮
        self._progress_bar.setVisible(False)
        self._btn_cancel.setVisible(False)
        self._set_file_buttons_enabled(True)
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
        self._progress_bar.setVisible(False)
        self._btn_cancel.setVisible(False)
        self._set_file_buttons_enabled(True)
        QMessageBox.warning(self, "自动摆正失败", error)
        self._update_status()

    def _on_thumbnails_invalidated(self, page_indices: list[int]) -> None:
        """旋转后缩略图缓存失效：清缓存并触发可见页按需重渲。"""
        if not page_indices:
            return
        # 全部失效用 invalidate_all（旋转全部），否则逐页失效
        session = self._session_mgr.active_session
        if session is None:
            return
        total = session.pdf_document.page_count
        if len(page_indices) >= total:
            self._thumbnail_model.invalidate_all()
        else:
            for idx in page_indices:
                row = self._row_of_page(idx)
                if row is not None:
                    self._thumbnail_model.invalidate(row)

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
        # 异步 IPC:结构变更,manager 通过 mutate_done + thumbnails_invalidated 刷新。
        # 标记待删页索引,供 _on_mutate_done 回调里更新 loaded_pages。
        self._pending_delete_indices = set(indices)
        self._session_mgr.delete_pages_async(indices)
        # 删页改变 page_index 映射,预览窗口的 _page_indices 会错位 → 关闭。
        self._close_preview_window_if_open()

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
            # 异步 IPC 插入(失败由 manager.mutate_failed 信号上报)
            self._pending_insert = True
            self._session_mgr.insert_from_async(path, after_index)
        else:
            # 未选文件 → 插入空白页
            self._pending_insert = True
            self._session_mgr.insert_blank_async(after_index)
        # 结构变更刷新在 _on_mutate_done 回调(loaded_pages 清空 + 重渲)

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

        进程化:预览图走 IPC(client.render_preview → PNG → QPixmap)。
        优先 OCR 原始块（细粒度，可双击编辑），无则回退 text_layers（粗块仅可视化），
        都没有则显示纯页面图（无高亮）。
        """
        session = self._session_mgr.active_session
        if session is None or self._preview_window is None:
            return
        page_info = session.pdf_document.get_page(page_idx)
        if page_info is None:
            return
        # IPC 取预览 PNG(同步,GUI 短暂阻塞;单页 150dpi ~50-200ms 可接受)
        try:
            png = self._session_mgr.backend_client.render_preview(
                session.session_id, page_idx, dpi=150
            )
            pixmap = QPixmap()
            pixmap.loadFromData(png, "PNG")
        except Exception as e:
            logger.error("预览渲染页 %d 失败: %s", page_idx, e)
            return
        if pixmap.isNull():
            return
        win = self._preview_window
        assert win is not None
        if page_info.ocr_text_blocks:
            win.set_ocr_blocks(page_idx, page_info.ocr_text_blocks, pixmap)
            win.setWindowTitle(
                f"文字层预览 — 第{page_idx + 1}页 ({len(page_info.ocr_text_blocks)}个文字块)"
            )
        elif page_info.has_text_layer and not page_info.text_layers:
            # 延迟加载:后端只判 has_text_layer 不取 text_layers 详情,
            # 预览时按需调 IPC detect_text_layers 取 bbox(单页 ~180ms,用户主动触发可接受)。
            page_info.text_layers = self._session_mgr.detect_text_layers(page_idx)
            if page_info.text_layers:
                # page_rect 已由 load worker 缓存到模型，不再直接读 fitz 对象。
                win.set_highlight(
                    pixmap,
                    page_info.text_layers,
                    render_dpi=150,
                    page_rect=page_info.rect,
                    source="pdf",
                )
                win.setWindowTitle(
                    f"文字层预览 — 第{page_idx + 1}页 ({len(page_info.text_layers)}个文字块)"
                )
            else:
                win.set_page_pixmap(pixmap)
                win.setWindowTitle(f"文字层预览 — 第{page_idx + 1}页 (无文字层)")
        elif page_info.text_layers:
            win.set_highlight(
                pixmap,
                page_info.text_layers,
                render_dpi=150,
                page_rect=page_info.rect,
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
        """取消当前操作:按运行状态路由到 deskew / ocr / mutate。"""
        mgr = self._session_mgr
        if mgr.is_deskew_running:
            mgr.cancel_deskew()
        elif mgr.is_ocr_running:
            mgr.cancel_ocr()
        elif mgr.is_mutate_running:
            # 通用 mutate(删除文字层等):后端 cancel_event 协作式
            mgr._cancel_mutate_worker()  # noqa: SLF001

    # ---- public API for MainWindow ----------------------------------

    def set_ocr_service(self, service: OCRServiceBase) -> None:
        """设置 OCR 服务实例（由 MainWindow 调用）。"""
        self._session_mgr.set_ocr_service(service)

    def shutdown(self) -> None:
        """清理资源（由 MainWindow 调用）。"""
        self._session_mgr.shutdown()

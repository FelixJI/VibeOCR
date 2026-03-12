"""撤销/重做命令栈

基于 Qt 的 QUndoStack + QUndoCommand 实现。
"""

from PySide6.QtGui import QUndoCommand, QUndoStack
from PySide6.QtWidgets import QGraphicsItem, QGraphicsScene


class AddAnnotationCommand(QUndoCommand):
    """添加标注项命令"""

    def __init__(
        self,
        scene: QGraphicsScene,
        item: QGraphicsItem,
        description: str = "添加标注",
    ):
        super().__init__(description)
        self._scene = scene
        self._item = item

    def redo(self) -> None:
        self._scene.addItem(self._item)

    def undo(self) -> None:
        self._scene.removeItem(self._item)


class RemoveAnnotationCommand(QUndoCommand):
    """删除标注项命令"""

    def __init__(
        self,
        scene: QGraphicsScene,
        item: QGraphicsItem,
        description: str = "删除标注",
    ):
        super().__init__(description)
        self._scene = scene
        self._item = item

    def redo(self) -> None:
        self._scene.removeItem(self._item)

    def undo(self) -> None:
        self._scene.addItem(self._item)


class MoveAnnotationCommand(QUndoCommand):
    """移动标注项命令"""

    def __init__(
        self,
        item: QGraphicsItem,
        old_pos,
        new_pos,
        description: str = "移动标注",
    ):
        super().__init__(description)
        self._item = item
        self._old_pos = old_pos
        self._new_pos = new_pos

    def redo(self) -> None:
        self._item.setPos(self._new_pos)

    def undo(self) -> None:
        self._item.setPos(self._old_pos)


def create_undo_stack(parent=None) -> QUndoStack:
    """创建配置好的 QUndoStack"""
    stack = QUndoStack(parent)
    stack.setUndoLimit(50)
    return stack

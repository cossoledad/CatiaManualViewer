"""Typed controls layered on the shared native graph canvas."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QToolButton

from catia_manual.core.relations import RELATION_LABELS
from catia_manual.gui.function_map import FunctionMapWidget
from catia_manual.gui.style import icon


class RelationGraphWidget(FunctionMapWidget):
    typesChanged = Signal(list)
    sourceRequested = Signal()

    def __init__(self):
        super().__init__()
        self.up_button.hide()
        self.all_button.hide()
        self.current_button.setToolTip("回到中心页面")
        self.current_button.setAccessibleName("回到中心页面")
        self.view.setAccessibleName("页面关系图画布")
        row = QHBoxLayout()
        row.setContentsMargins(12, 0, 12, 4)
        self.type_buttons = {}
        for kind, title in RELATION_LABELS.items():
            button = QToolButton()
            button.setText("命令" if kind == "documents_command" else title)
            button.setCheckable(True)
            button.setChecked(True)
            button.clicked.connect(self.change_types)
            row.addWidget(button)
            self.type_buttons[kind] = button
        row.addStretch()
        self.layout().insertLayout(1, row)
        self.source_button = QToolButton()
        self.source_button.setIcon(icon("book"))
        self.source_button.setToolTip("查看原文出处")
        self.source_button.setAccessibleName("查看原文出处")
        self.source_button.clicked.connect(self.sourceRequested.emit)
        self.footer.insertWidget(self.footer.count() - 1, self.source_button)
        self.can_open = lambda node: node.get("exists", True)

    def change_types(self):
        self.typesChanged.emit(
            [kind for kind, button in self.type_buttons.items() if button.isChecked()]
        )

    def selection_changed(self):
        super().selection_changed()
        if self.loading or not self.snapshot:
            return
        item = self.items.get(self.selected_id())
        node = item.node if item else None
        self.expand_button.setEnabled(
            bool(node and node["children"] and node["id"] != self.snapshot.root)
        )
        labels = {
            "article": "打开文章",
            "command": "查看出处",
            "image": "预览图片",
            "sample": "打开示例",
        }
        self.open_button.setText(labels.get(node["kind"], "打开") if node else "打开")
        self.open_button.setEnabled(bool(node and self.can_open(node)))
        self.drill_button.setEnabled(bool(node and node["kind"] == "article" and node["exists"]))
        self.drill_button.setToolTip("以此页面为中心")
        self.source_button.setEnabled(
            bool(
                node
                and (
                    node["source"]
                    or any(
                        e["source"] == node["id"] or e["target"] == node["id"]
                        for e in self.snapshot.edges
                    )
                )
            )
        )

    def set_snapshot(self, snapshot, state=None, terms=(), reading=None):
        for kind, button in self.type_buttons.items():
            button.setChecked(kind in snapshot.request["types"])
        super().set_snapshot(snapshot, state, terms, reading)
        self.current_button.setEnabled(True)

    def open_selected(self):
        if self.selected_id():
            self.opened.emit(self.selected_id())

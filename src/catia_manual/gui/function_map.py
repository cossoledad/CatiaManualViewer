"""Native, software-rendered functional map. No document scripts or web dependencies."""

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from catia_manual.gui.style import icon
from catia_manual.gui.widgets import ElidedLabel

CARD_WIDTH, CARD_HEIGHT = 236, 84
KINDS = {
    "library": "功能",
    "category": "功能分类",
    "manual": "手册",
    "placement": "章节",
    "article": "页面",
    "command": "命令",
    "sample": "示例",
    "image": "图片",
}


def valid_position(value):
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(type(n) in (int, float) and math.isfinite(n) and abs(n) < 1000000 for n in value)
    )


class MapNode(QGraphicsObject):
    activated = Signal(str)
    toggled = Signal(str)

    def __init__(self, node, expanded, terms, current=False):
        super().__init__()
        self.node, self.expanded, self.terms, self.current = node, expanded, terms, current
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(
            node["label_en"] + ("\n" + node["document_ref"]["path"] if node["document_ref"] else "")
        )

    def boundingRect(self):
        return QRectF(-3, -3, CARD_WIDTH + 6, CARD_HEIGHT + 6)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = self.isSelected()
        color = (
            "#25898a" if selected else "#d49b50" if not self.node.get("exists", True) else "#dce7ec"
        )
        painter.setPen(QPen(QColor(color), 2 if selected else 1))
        painter.setBrush(QColor("#eef8f5" if selected else "#ffffff"))
        painter.drawRoundedRect(QRectF(0, 0, CARD_WIDTH, CARD_HEIGHT), 10, 10)
        label = self.node["label_zh"] or self.node["label_en"]
        matched = any(
            t and t in (label + " " + self.node["label_en"]).casefold() for t in self.terms
        )
        if matched:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#ffe7a0"))
            painter.drawRoundedRect(QRectF(12, 12, 4, 22), 2, 2)
        font = QFont(QApplication.font())
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor("#155665" if selected else "#294c5c"))
        text = painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, 180)
        painter.drawText(QRectF(22, 10, 184, 25), Qt.AlignmentFlag.AlignVCenter, text)
        font.setWeight(QFont.Weight.Normal)
        font.setPointSizeF(max(8, font.pointSizeF() - 1))
        painter.setFont(font)
        painter.setPen(QColor("#758996"))
        subtitle = (
            self.node["label_en"] if self.node["label_en"] != label else KINDS[self.node["kind"]]
        )
        painter.drawText(
            QRectF(22, 36, 198, 20),
            Qt.AlignmentFlag.AlignVCenter,
            painter.fontMetrics().elidedText(subtitle, Qt.TextElideMode.ElideRight, 198),
        )
        if self.current:
            painter.setPen(QColor("#197b7e"))
            painter.drawText(QRectF(22, 60, 150, 16), self.node.get("current_label", "正在阅读"))
        elif self.node["kind"] in {"article", "command", "sample", "image"}:
            painter.setPen(QColor("#8b7151" if not self.node.get("exists", True) else "#32878a"))
            label = "文件缺失" if not self.node.get("exists", True) else KINDS[self.node["kind"]]
            painter.drawText(QRectF(22, 60, 132, 16), label)
        if self.node["children"]:
            if self.node.get("disclosure", True):
                icon("minus" if self.expanded else "plus", "#32878a").paint(
                    painter, 207, 11, 18, 18
                )
            painter.drawText(
                QRectF(160, 60, 62, 16),
                Qt.AlignmentFlag.AlignRight,
                str(self.node["children"]) + " 项"
                if self.node.get("children_known", True)
                else "可展开",
            )

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if (
            event.button() == Qt.MouseButton.LeftButton
            and event.pos().x() > 204
            and event.pos().y() < 34
        ):
            if self.node["children"] and self.node.get("disclosure", True):
                self.toggled.emit(self.node["id"])

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if event.pos().x() > 204 and event.pos().y() < 34 and self.node["children"]:
                event.accept()
                return
            self.activated.emit(self.node["id"])
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)


class MapCanvas(QGraphicsView):
    viewportChanged = Signal()
    openSelected = Signal()
    toggleSelected = Signal()

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QColor("#f4f8fa"))
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setAccessibleName("功能地图画布")
        self.horizontalScrollBar().valueChanged.connect(lambda _: self.viewportChanged.emit())
        self.verticalScrollBar().valueChanged.connect(lambda _: self.viewportChanged.emit())

    def zoom_by(self, factor):
        previous = self.transform().m11()
        target = max(0.2, min(2.0, previous * factor))
        self.scale(target / previous, target / previous)
        self.viewportChanged.emit()

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.pixelDelta().y()
        self.zoom_by(math.exp(max(-600, min(600, delta)) * 0.0015))
        event.accept()

    def keyPressEvent(self, event):
        key = event.key()
        if key in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            self.openSelected.emit()
        elif key == Qt.Key.Key_Space:
            self.toggleSelected.emit()
        elif key in {Qt.Key.Key_Plus, Qt.Key.Key_Equal}:
            self.zoom_by(1.2)
        elif key == Qt.Key.Key_Minus:
            self.zoom_by(1 / 1.2)
        elif key in {Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down}:
            owner = self.parentWidget()
            owner.move_selection(key)
        else:
            super().keyPressEvent(event)


class FunctionMapWidget(QWidget):
    opened = Signal(str)
    toggled = Signal(str)
    paged = Signal(str, int)
    drilled = Signal(str)
    upRequested = Signal()
    allRequested = Signal()
    currentRequested = Signal()
    stateChanged = Signal()

    def __init__(self):
        super().__init__()
        self.snapshot = None
        self.items = {}
        self.positions = {}
        self.loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(8, 4, 8, 4)
        self.up_button = self.tool(toolbar, "up", "上一级", self.upRequested.emit)
        self.all_button = self.tool(toolbar, "grid", "全部功能", self.allRequested.emit)
        self.title = ElidedLabel("功能地图")
        toolbar.addWidget(self.title, 1)
        self.current_button = self.tool(
            toolbar, "locate", "定位当前阅读页", self.currentRequested.emit
        )
        self.tool(toolbar, "fit", "适应窗口", self.fit_map)
        self.tool(toolbar, "minus", "缩小", lambda: self.view.zoom_by(1 / 1.2))
        self.zoom_label = QLabel("100%")
        self.zoom_label.setFixedWidth(48)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        toolbar.addWidget(self.zoom_label)
        self.tool(toolbar, "plus", "放大", lambda: self.view.zoom_by(1.2))
        layout.addLayout(toolbar)
        self.view = MapCanvas()
        layout.addWidget(self.view, 1)
        self.view.scene().selectionChanged.connect(self.selection_changed)
        self.view.viewportChanged.connect(self.viewport_changed)
        self.view.openSelected.connect(self.open_selected)
        self.view.toggleSelected.connect(self.toggle_selected)
        footer = QHBoxLayout()
        self.footer = footer
        footer.setContentsMargins(12, 8, 12, 10)
        self.selection_title = ElidedLabel()
        preview = QVBoxLayout()
        preview.addWidget(self.selection_title)
        self.selection_summary = ElidedLabel()
        self.selection_summary.setObjectName("muted")
        preview.addWidget(self.selection_summary)
        footer.addLayout(preview, 1)
        self.count = QLabel()
        self.count.setObjectName("muted")
        footer.addWidget(self.count)
        self.prev_button = self.tool(footer, "back", "上一组", lambda: self.page_selected(-1))
        self.next_button = self.tool(footer, "forward", "下一组", lambda: self.page_selected(1))
        self.drill_button = self.tool(footer, "locate", "聚焦分支", self.drill_selected)
        self.expand_button = self.tool(footer, "plus", "展开 / 收起", self.toggle_selected)
        self.open_button = QPushButton("打开文章")
        self.open_button.setObjectName("primary")
        self.open_button.clicked.connect(self.open_selected)
        footer.addWidget(self.open_button)
        layout.addLayout(footer)

    def tool(self, row, name, label, callback):
        button = QToolButton()
        button.setIcon(icon(name))
        button.setToolTip(label)
        button.setAccessibleName(label)
        button.clicked.connect(callback)
        row.addWidget(button)
        return button

    def selected_id(self):
        selected = self.view.scene().selectedItems()
        return selected[0].node["id"] if selected else ""

    def select(self, identity, *, center=False):
        self.view.scene().clearSelection()
        item = self.items.get(identity)
        if item:
            item.setSelected(True)
            if center:
                self.view.centerOn(item)

    def selection_changed(self):
        if self.loading or not self.snapshot:
            return
        item = self.items.get(self.selected_id())
        node = item.node if item else None
        self.selection_title.setText((node["label_zh"] or node["label_en"]) if node else "")
        self.selection_title.setToolTip(node["label_en"] if node else "")
        self.selection_summary.setText(node.get("summary", "") if node else "")
        self.selection_summary.setToolTip(self.selection_summary.text())
        children = node["children"] if node else 0
        self.expand_button.setEnabled(bool(children))
        self.expand_button.setIcon(
            icon("minus" if node and node["id"] in self.snapshot.expanded else "plus")
        )
        self.drill_button.setEnabled(bool(children and node["document_ref"]))
        self.open_button.setEnabled(bool(node and node["document_ref"]))
        self.prev_button.setEnabled(bool(node and node["offset"] > 0))
        self.next_button.setEnabled(
            bool(node and node["page_size"] > 0 and node["offset"] + node["page_size"] < children)
        )
        self.stateChanged.emit()

    def open_selected(self):
        item = self.items.get(self.selected_id())
        if item:
            if item.node["document_ref"]:
                self.opened.emit(item.node["id"])
            elif item.node["children"]:
                self.toggled.emit(item.node["id"])

    def toggle_selected(self):
        if self.selected_id():
            self.toggled.emit(self.selected_id())

    def page_selected(self, direction):
        if self.selected_id():
            self.paged.emit(self.selected_id(), direction)

    def drill_selected(self):
        if self.selected_id():
            self.drilled.emit(self.selected_id())

    def viewport_changed(self):
        self.zoom_label.setText(f"{self.view.transform().m11():.0%}")
        if not self.loading:
            self.stateChanged.emit()

    def fit_map(self):
        if not self.items:
            return
        self.view.fitInView(
            self.view.scene().itemsBoundingRect().adjusted(-24, -24, 24, 24),
            Qt.AspectRatioMode.KeepAspectRatio,
        )
        if self.view.transform().m11() > 1:
            self.view.resetTransform()
        self.viewport_changed()

    def state(self):
        point = self.view.mapToScene(self.view.viewport().rect().center())
        return {
            "selected": self.selected_id(),
            "positions": self.positions,
            "viewport": {"x": point.x(), "y": point.y(), "zoom": self.view.transform().m11()},
        }

    def set_snapshot(self, snapshot, state=None, terms=(), reading=None):
        self.loading = True
        previous = (
            self.positions
            if (
                self.snapshot
                and self.snapshot.root == snapshot.root
                and self.snapshot.graph_revision == snapshot.graph_revision
            )
            else {}
        )
        self.snapshot = snapshot
        self.current_button.setEnabled(bool(reading))
        self.view.scene().clear()
        self.items = {}
        state = state or {}
        saved = {**previous, **state.get("positions", {})}
        self.positions = {}
        used = {}
        parents = {
            edge.get("layout_target", edge["target"]): edge.get("layout_source", edge["source"])
            for edge in snapshot.edges
            if edge.get("layout", True)
        }
        depth = {snapshot.root: 0}
        for node in snapshot.nodes:
            cursor, trail = node["id"], []
            while cursor not in depth and cursor in parents and cursor not in trail:
                trail.append(cursor)
                cursor = parents[cursor]
            for identity in reversed(trail):
                depth[identity] = depth.get(parents[identity], 0) + 1
            position = saved.get(node["id"])
            if valid_position(position):
                used.setdefault(depth.get(node["id"], 0), []).append(position[1])
        pending = list(snapshot.nodes)
        while pending:
            deferred = []
            for node in pending:
                parent = parents.get(node["id"])
                if parent and parent not in self.positions:
                    deferred.append(node)
                    continue
                level = depth.get(parent, -1) + 1 if parent else 0
                depth[node["id"]] = level
                position = saved.get(node["id"])
                valid = valid_position(position)
                y = position[1] if valid else (self.positions[parent][1] if parent else 0)
                occupied = used.setdefault(level, [])
                if not valid:
                    base, step = y, 0
                    while any(abs(y - other) < 106 for other in occupied):
                        step += 1
                        y = base + ((step + 1) // 2) * 110 * (1 if step % 2 else -1)
                occupied.append(y)
                x = position[0] if valid and getattr(self, "free_layout", False) else level * 292
                self.positions[node["id"]] = [x, y]
                item = MapNode(
                    node,
                    node["id"] in snapshot.expanded,
                    terms,
                    current=bool(
                        reading and reading.get("path") == (node["document_ref"] or {}).get("path")
                    ),
                )
                item.setPos(x, y)
                item.activated.connect(self.activate_node)
                item.toggled.connect(self.toggled.emit)
                self.view.scene().addItem(item)
                self.items[node["id"]] = item
            if len(deferred) == len(pending):
                break
            pending = deferred
        for identity, position in saved.items():
            if (
                len(self.positions) < 200
                and identity not in self.positions
                and valid_position(position)
            ):
                self.positions[identity] = position
        for edge in snapshot.edges:
            a, b = self.items.get(edge["source"]), self.items.get(edge["target"])
            if a is None or b is None:
                continue
            direction = 1 if b.x() > a.x() else -1
            start = a.pos() + QPointF(CARD_WIDTH if direction > 0 else 0, CARD_HEIGHT / 2)
            end = b.pos() + QPointF(0 if direction > 0 else CARD_WIDTH, CARD_HEIGHT / 2)
            path = QPainterPath(start)
            path.cubicTo(start + QPointF(28 * direction, 0), end - QPointF(28 * direction, 0), end)
            pen = QPen(QColor("#8aaeb8" if edge.get("label") else "#b6ced5"), 1.4)
            if not edge.get("layout", True):
                pen.setStyle(Qt.PenStyle.DashLine)
            line = self.view.scene().addPath(path, pen)
            line.setZValue(-1)
            if edge.get("label"):
                line.setToolTip(edge["label"])
                triangle = QPolygonF(
                    [end, end + QPointF(-8 * direction, -4), end + QPointF(-8 * direction, 4)]
                )
                self.view.scene().addPolygon(triangle, pen, QColor("#8aaeb8")).setZValue(-1)
                font = QFont(QApplication.font())
                font.setPointSizeF(8)
                label = self.view.scene().addSimpleText(edge["label"], font)
                center = path.pointAtPercent(0.5)
                label.setPos(center.x() - label.boundingRect().width() / 2, center.y() - 18)
                label.setBrush(QColor("#638290"))
                label.setZValue(-1)
        bounds = self.view.scene().itemsBoundingRect()
        self.view.scene().setSceneRect(bounds.adjusted(-300, -300, 300, 300))
        root = self.items.get(snapshot.root)
        self.title.setText((root.node["label_zh"] or root.node["label_en"]) if root else "功能地图")
        self.count.setText(
            snapshot.notice or f"{len(self.items)} 项" + (" · 分页" if snapshot.truncated else "")
        )
        self.up_button.setEnabled(
            bool(
                snapshot.parent
                or snapshot.request.get("scope", {"kind": "all"})["kind"] != "all"
                or snapshot.request.get("branch")
            )
        )
        selected = state.get("selected", snapshot.selected)
        self.select(selected if selected in self.items else snapshot.selected)
        viewport = state.get("viewport")
        if viewport and all(
            type(viewport.get(k)) in (int, float) and math.isfinite(viewport[k])
            for k in ("x", "y", "zoom")
        ):
            self.view.resetTransform()
            factor = max(0.2, min(2, viewport["zoom"]))
            self.view.scale(factor, factor)
            self.view.centerOn(viewport["x"], viewport["y"])
        else:
            self.view.resetTransform()
            self.view.scale(0.9, 0.9)
            item = self.items.get(snapshot.selected) or root
            if item:
                # Keep nearby ancestors readable even when the manual has many siblings.
                x = max(CARD_WIDTH / 2, item.x() - 292 + CARD_WIDTH / 2)
                self.view.centerOn(x, item.y() + CARD_HEIGHT / 2)
        self.loading = False
        self.viewport_changed()
        self.selection_changed()

        # Splitter and stacked-widget sizes settle after the sidebar has been hidden.
        def settled():
            if self.snapshot is not snapshot:
                return
            self.loading = True
            if viewport and all(
                type(viewport.get(k)) in (int, float) and math.isfinite(viewport[k])
                for k in ("x", "y", "zoom")
            ):
                self.view.centerOn(viewport["x"], viewport["y"])
            else:
                item = self.items.get(snapshot.selected) or root
                if item:
                    self.view.centerOn(item.x() - 292 + CARD_WIDTH / 2, item.y() + CARD_HEIGHT / 2)
                    self.view.ensureVisible(item, 24, 24)
            self.loading = False
            self.viewport_changed()

        QTimer.singleShot(0, settled)

    def activate_node(self, identity):
        self.select(identity)
        self.open_selected()

    def move_selection(self, key):
        if not self.snapshot:
            return
        identity = self.selected_id()
        parents = {
            e.get("layout_target", e["target"]): e.get("layout_source", e["source"])
            for e in self.snapshot.edges
            if e.get("layout", True)
        }
        if key == Qt.Key.Key_Left:
            target = parents.get(identity)
        elif key == Qt.Key.Key_Right:
            target = next(
                (e["target"] for e in self.snapshot.edges if e["source"] == identity), None
            )
        else:
            siblings = [n for n in self.items if parents.get(n) == parents.get(identity)]
            siblings.sort(key=lambda n: self.items[n].y())
            index = siblings.index(identity) if identity in siblings else 0
            offset = -1 if key == Qt.Key.Key_Up else 1
            target = siblings[max(0, min(len(siblings) - 1, index + offset))] if siblings else None
        if target:
            self.select(target, center=True)

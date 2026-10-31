"""Native, software-rendered functional map. No document scripts or web dependencies."""

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
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

from catia_manual.gui.graph_edge import GraphEdge
from catia_manual.gui.graph_geometry import EdgeRouter, layout_nodes
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


class MapNode(QGraphicsObject):
    activated = Signal(str)
    toggled = Signal(str)
    moved = Signal(str)
    dragFinished = Signal()
    hovered = Signal(str)

    def __init__(self, node, expanded, terms, current=False):
        super().__init__()
        self.node, self.expanded, self.terms, self.current = node, expanded, terms, current
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.hovering = False
        name, self.accent = {
            "command": ("tool", "#9182b3"),
            "sample": ("cube", "#b48b58"),
            "image": ("image", "#709b7d"),
            "article": ("book", "#5f9da4"),
        }.get(node["kind"], ("layers", "#6689b0"))
        self.kind_icon = icon(name, self.accent)
        self.setToolTip(
            node["label_en"] + ("\n" + node["document_ref"]["path"] if node["document_ref"] else "")
        )

    def boundingRect(self):
        return QRectF(-3, -3, CARD_WIDTH + 6, CARD_HEIGHT + 6)

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        selected = self.isSelected()
        color = (
            "#25898a"
            if selected
            else "#d49b50"
            if not self.node.get("exists", True)
            else "#9fbfc8"
            if self.hovering
            else "#dce7ec"
        )
        painter.setPen(QPen(QColor(color), 2 if selected else 1))
        painter.setBrush(QColor("#eef8f5" if selected else "#ffffff"))
        painter.drawRoundedRect(QRectF(0, 0, CARD_WIDTH, CARD_HEIGHT), 10, 10)
        self.kind_icon.paint(painter, 14, 14, 18, 18)
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
        text = painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, 164)
        painter.drawText(QRectF(40, 10, 164, 25), Qt.AlignmentFlag.AlignVCenter, text)
        if painter.worldTransform().m11() < 0.55:
            return
        font.setWeight(QFont.Weight.Normal)
        font.setPointSizeF(max(8, font.pointSizeF() - 1))
        painter.setFont(font)
        painter.setPen(QColor("#758996"))
        subtitle = self.node["label_en"] if self.node["label_en"] != label else ""
        painter.drawText(
            QRectF(22, 36, 198, 20),
            Qt.AlignmentFlag.AlignVCenter,
            painter.fontMetrics().elidedText(subtitle, Qt.TextElideMode.ElideRight, 198),
        )
        painter.setPen(QColor(self.accent))
        if self.current:
            painter.setPen(QColor("#197b7e"))
            painter.drawText(QRectF(22, 60, 150, 16), self.node.get("current_label", "正在阅读"))
        else:
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
        if (
            event.button() == Qt.MouseButton.LeftButton
            and event.pos().x() > 204
            and event.pos().y() < 34
        ):
            if self.node["children"] and self.node.get("disclosure", True):
                self.scene().clearSelection()
                self.setSelected(True)
                self.toggled.emit(self.node["id"])
                event.accept()
                return
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.press_position = self.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        if self.pos() != getattr(self, "press_position", self.pos()):
            self.dragFinished.emit()

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            return QPointF(
                max(-900000, min(900000, value.x())), max(-900000, min(900000, value.y()))
            )
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.moved.emit(self.node["id"])
        return super().itemChange(change, value)

    def hoverEnterEvent(self, event):
        self.hovering = True
        self.hovered.emit(self.node["id"])
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.hovering = False
        self.hovered.emit("")
        self.update()
        super().hoverLeaveEvent(event)

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
    interactionFinished = Signal()
    pointerChanged = Signal(object)

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QColor("#f4f8fa"))
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setMouseTracking(True)
        self.panning = None
        self.adjusting = False
        self.setAccessibleName("功能地图画布")
        self.horizontalScrollBar().valueChanged.connect(self.scroll_changed)
        self.verticalScrollBar().valueChanged.connect(self.scroll_changed)

    def scroll_changed(self, _):
        if not self.adjusting:
            self.viewportChanged.emit()

    def centerOn(self, *args):
        if len(args) == 2:
            point = QPointF(*args)
        elif isinstance(args[0], QGraphicsItem):
            point = args[0].sceneBoundingRect().center()
        else:
            point = QPointF(args[0])
        # Grow the navigable region around the requested view, rather than the content.
        # Hidden scroll bars remain a Qt implementation detail and never form a pan boundary.
        zoom = max(0.01, self.transform().m11())
        margin_x = max(2000, self.viewport().width() * 3 / zoom)
        margin_y = max(2000, self.viewport().height() * 3 / zoom)
        needed = QRectF(point.x() - margin_x, point.y() - margin_y, margin_x * 2, margin_y * 2)
        self.adjusting = True
        if not self.sceneRect().contains(needed):
            self.setSceneRect(self.sceneRect().united(needed))
        super().centerOn(point)
        self.adjusting = False
        self.viewportChanged.emit()

    def zoom_by(self, factor, anchor=None):
        anchor = anchor or self.viewport().rect().center()
        fixed = self.mapToScene(anchor)
        previous = self.transform().m11()
        target = max(0.2, min(2.0, previous * factor))
        self.scale(target / previous, target / previous)
        offset = QPointF(anchor - self.viewport().rect().center()) / target
        self.centerOn(fixed - offset)
        # QGraphicsView scroll positions are integers. Keep the remaining fraction in
        # the transform so repeated in/out zooms do not drift away from the pointer.
        remainder = self.mapToScene(anchor) - fixed
        self.translate(remainder.x(), remainder.y())
        self.viewportChanged.emit()

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.pixelDelta().y()
        self.zoom_by(math.exp(max(-600, min(600, delta)) * 0.0015), event.position().toPoint())
        event.accept()

    def mousePressEvent(self, event):
        item = self.itemAt(event.pos())
        if event.button() == Qt.MouseButton.MiddleButton or (
            event.button() == Qt.MouseButton.LeftButton and not isinstance(item, MapNode)
        ):
            self.panning = (event.button(), event.position().toPoint())
            self.pointerChanged.emit(None)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.panning:
            button, previous = self.panning
            position = event.position().toPoint()
            delta = QPointF(position - previous) / self.transform().m11()
            self.centerOn(self.mapToScene(self.viewport().rect().center()) - delta)
            self.panning = (button, position)
            self.pointerChanged.emit(None)
            event.accept()
            return
        super().mouseMoveEvent(event)
        self.pointerChanged.emit(self.itemAt(event.pos()))

    def mouseReleaseEvent(self, event):
        if self.panning and event.button() == self.panning[0]:
            self.panning = None
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.interactionFinished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        zoom = self.transform().m11()
        spacing = 24 if zoom >= 0.6 else 96
        painter.setPen(QPen(QColor("#dbe5eb"), 1 / zoom))
        left, top = (
            math.floor(rect.left() / spacing) * spacing,
            math.floor(rect.top() / spacing) * spacing,
        )
        points = [
            QPointF(x, y)
            for x in range(left, math.ceil(rect.right()), spacing)
            for y in range(top, math.ceil(rect.bottom()), spacing)
        ]
        painter.drawPoints(points)

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
        self.layout_generation = 0
        self.edges, self.hover_edge, self.hover_node = [], None, ""
        self.dragged_nodes = set()
        self.route_timer = QTimer(self)
        self.route_timer.setSingleShot(True)
        self.route_timer.timeout.connect(self.preview_routes)
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
        self.arrange_button = self.tool(toolbar, "arrange", "整理布局", self.arrange_map)
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
        self.view.pointerChanged.connect(self.pointer_changed)
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
        self.update_edge_focus()
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
        bounds = self.view.scene().itemsBoundingRect().adjusted(-32, -32, 32, 32)
        factor = min(
            1,
            max(
                0.2,
                min(
                    self.view.viewport().width() / bounds.width(),
                    self.view.viewport().height() / bounds.height(),
                ),
            ),
        )
        self.view.resetTransform()
        self.view.scale(factor, factor)
        self.view.centerOn(bounds.center())
        self.viewport_changed()

    def arrange_map(self):
        if not self.snapshot:
            return
        state = {"selected": self.selected_id()}
        self.positions = {}
        self.set_snapshot(self.snapshot, state, self.terms, self.reading)
        self.fit_map()
        QTimer.singleShot(0, self.fit_map)

    def node_moved(self, identity):
        item = self.items.get(identity)
        if self.loading or item is None:
            return
        self.positions[identity] = [item.x(), item.y()]
        self.dragged_nodes.add(identity)
        if not self.route_timer.isActive():
            self.route_timer.start(16)
        self.stateChanged.emit()

    def preview_routes(self):
        # During a drag update only incident wires; the full obstacle pass runs on release.
        for edge in self.edges:
            a, b = self.items[edge.edge["source"]], self.items[edge.edge["target"]]
            if not self.dragged_nodes.intersection({a.node["id"], b.node["id"]}):
                continue
            direction = 1 if b.x() >= a.x() else -1
            start = (a.x() + (CARD_WIDTH if direction > 0 else 0), a.y() + CARD_HEIGHT / 2)
            end = (b.x() + (0 if direction > 0 else CARD_WIDTH), b.y() + CARD_HEIGHT / 2)
            mid = (start[0] + end[0]) / 2
            edge.set_route([start, (mid, start[1]), (mid, end[1]), end])
            edge.badge = QRectF()
        self.dragged_nodes.clear()

    def finish_node_drag(self):
        if self.loading:
            return
        self.route_timer.stop()
        self.dragged_nodes.clear()
        self.reroute_edges()
        self.stateChanged.emit()

    def reroute_edges(self):
        for item in self.edges:
            self.view.scene().removeItem(item)
            item.deleteLater()
        self.edges, self.hover_edge = [], None
        rectangles = {k: (n.x(), n.y(), CARD_WIDTH, CARD_HEIGHT) for k, n in self.items.items()}
        router = EdgeRouter(rectangles)
        for edge, points in zip(
            self.snapshot.edges, router.routes(self.snapshot.edges), strict=False
        ):
            if len(points) < 2:
                continue
            item = GraphEdge(edge, points)
            item.hovered.connect(self.edge_hovered)
            self.view.scene().addItem(item)
            self.edges.append(item)
        self.update_edge_focus()

    def hover_node_changed(self, identity):
        self.hover_node = identity
        self.update_edge_focus()

    def pointer_changed(self, item):
        node = item.node["id"] if isinstance(item, MapNode) else ""
        edge = item if isinstance(item, GraphEdge) else None
        if node != self.hover_node or edge is not self.hover_edge:
            self.hover_node, self.hover_edge = node, edge
            self.update_edge_focus()

    def edge_hovered(self, edge, entered):
        self.hover_edge = edge if entered else None
        self.update_edge_focus()

    def update_edge_focus(self):
        if not self.snapshot:
            return
        identity = self.hover_node or self.selected_id()
        obstacles = [n.sceneBoundingRect().adjusted(-5, -5, 5, 5) for n in self.items.values()]
        badges = []
        ordered = sorted(self.edges, key=lambda e: e is not self.hover_edge)
        for edge in ordered:
            endpoints = {edge.edge["source"], edge.edge["target"]}
            edge.focused = edge is self.hover_edge or identity in endpoints
            edge.dimmed = bool(identity or self.hover_edge) and not edge.focused
            edge.setZValue(-1 if edge.focused else -2)
            edge.badge = QRectF()
            show = edge is self.hover_edge or (edge.focused and identity != self.snapshot.root)
            if show and edge.edge.get("label"):
                metrics = QFontMetricsF(edge.font)
                width, height = (
                    metrics.horizontalAdvance(edge.edge["label"]) + 14,
                    metrics.height() + 6,
                )
                segments = sorted(
                    zip(edge.points, edge.points[1:], strict=False),
                    key=lambda pair: -math.dist(*pair),
                )
                for a, b in segments:
                    for portion in (0.5, 0.3, 0.7):
                        x, y = a[0] + (b[0] - a[0]) * portion, a[1] + (b[1] - a[1]) * portion
                        rect = QRectF(x - width / 2, y - height / 2, width, height)
                        if not any(rect.intersects(r) for r in obstacles + badges):
                            edge.badge = rect
                            badges.append(rect.adjusted(-5, -4, 5, 4))
                            break
                    if not edge.badge.isEmpty():
                        break
            edge.update()

    def state(self):
        point = self.view.mapToScene(self.view.viewport().rect().center())
        return {
            "selected": self.selected_id(),
            "positions": self.positions,
            "viewport": {"x": point.x(), "y": point.y(), "zoom": self.view.transform().m11()},
        }

    def set_snapshot(self, snapshot, state=None, terms=(), reading=None):
        self.loading = True
        self.layout_generation += 1
        generation = self.layout_generation
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
        self.reading, self.terms = reading, terms
        self.current_button.setEnabled(bool(reading))
        self.route_timer.stop()
        self.dragged_nodes.clear()
        self.edges, self.hover_edge, self.hover_node = [], None, ""
        self.items = {}
        self.view.scene().clear()
        state = state or {}
        saved = {**previous, **state.get("positions", {})}
        self.positions = layout_nodes(snapshot, saved)
        for node in snapshot.nodes:
            position = self.positions.get(node["id"])
            if not position:
                continue
            item = MapNode(
                node,
                node["id"] in snapshot.expanded,
                terms,
                current=bool(
                    reading and reading.get("path") == (node["document_ref"] or {}).get("path")
                ),
            )
            item.setPos(*position)
            item.activated.connect(self.activate_node)
            item.toggled.connect(self.toggled.emit)
            item.moved.connect(self.node_moved)
            item.dragFinished.connect(self.finish_node_drag)
            item.hovered.connect(self.hover_node_changed)
            self.view.scene().addItem(item)
            self.items[node["id"]] = item
        self.reroute_edges()
        bounds = self.view.scene().itemsBoundingRect()
        self.view.setSceneRect(bounds.adjusted(-2000, -2000, 2000, 2000))
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
                x = item.x() + CARD_WIDTH / 2
                if snapshot.view != "page-relations":
                    x -= 176
                self.view.centerOn(x, item.y() + CARD_HEIGHT / 2)
        self.loading = False
        self.viewport_changed()
        self.selection_changed()

        # Splitter and stacked-widget sizes settle after the sidebar has been hidden.
        def settled():
            if self.snapshot is not snapshot or generation != self.layout_generation:
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
                    x = item.x() + CARD_WIDTH / 2
                    if snapshot.view != "page-relations":
                        x -= 176
                    self.view.centerOn(x, item.y() + CARD_HEIGHT / 2)
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
        if self.snapshot.view == "page-relations" and identity in self.items:
            origin = self.items[identity].sceneBoundingRect().center()
            dx, dy = {
                Qt.Key.Key_Left: (-1, 0),
                Qt.Key.Key_Right: (1, 0),
                Qt.Key.Key_Up: (0, -1),
                Qt.Key.Key_Down: (0, 1),
            }[key]
            candidates = []
            for target, item in self.items.items():
                delta = item.sceneBoundingRect().center() - origin
                ahead = delta.x() * dx + delta.y() * dy
                if ahead > 8:
                    side = abs(delta.x() * dy - delta.y() * dx)
                    candidates.append((ahead + side * 2, target))
            if candidates:
                self.select(min(candidates)[1], center=True)
            return
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

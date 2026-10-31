"""Lightweight native edges with rounded routes and focus-only relationship badges."""

import math

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import QApplication, QGraphicsObject

from catia_manual.gui.graph_geometry import compact

COLORS = {
    "references": "#6689b0",
    "links_to": "#5f9da4",
    "documents_command": "#9182b3",
    "has_sample": "#b48b58",
    "has_image": "#709b7d",
}


def rounded_path(points):
    path = QPainterPath(QPointF(*points[0]))
    for a, b, c in zip(points, points[1:], points[2:], strict=False):
        incoming, outgoing = math.dist(a, b), math.dist(b, c)
        radius = min(9, incoming / 2, outgoing / 2)
        before = QPointF(
            b[0] + (a[0] - b[0]) * radius / incoming, b[1] + (a[1] - b[1]) * radius / incoming
        )
        after = QPointF(
            b[0] + (c[0] - b[0]) * radius / outgoing, b[1] + (c[1] - b[1]) * radius / outgoing
        )
        path.lineTo(before)
        path.quadTo(QPointF(*b), after)
    path.lineTo(QPointF(*points[-1]))
    return path


class GraphEdge(QGraphicsObject):
    hovered = Signal(object, bool)

    def __init__(self, edge, points):
        super().__init__()
        self.edge, self.points = edge, []
        self.path, self.hit_path = QPainterPath(), QPainterPath()
        self.focused = self.dimmed = False
        self.badge = QRectF()
        self.setZValue(-2)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        self.setAcceptHoverEvents(bool(edge.get("label")))
        self.setToolTip(edge.get("label", ""))
        self.font = QFont(QApplication.font())
        self.font.setPointSizeF(8)
        self.set_route(points)

    def set_route(self, points):
        self.prepareGeometryChange()
        points = compact(points)
        if len(points) < 2:
            points = points + [(points[0][0] + 0.01, points[0][1])]
        self.points = points
        self.path = rounded_path(points)
        stroker = QPainterPathStroker()
        stroker.setWidth(12)
        self.hit_path = stroker.createStroke(self.path)
        self.update()

    def boundingRect(self):
        return self.path.boundingRect().adjusted(-90, -22, 90, 22)

    def shape(self):
        if not self.badge.isEmpty():
            badge = QPainterPath()
            badge.addRoundedRect(self.badge, 5, 5)
            return self.hit_path.united(badge)
        return self.hit_path

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(COLORS.get(self.edge.get("kind"), "#8baeb9"))
        color.setAlpha(65 if self.dimmed else 230 if self.focused else 140)
        width = 1.8 if self.focused else 1.2
        # A background halo keeps unavoidable crossings legible without merging the lines.
        painter.setPen(
            QPen(QColor("#f4f8fa"), width + 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        )
        painter.drawPath(self.path)
        pen = QPen(
            color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin
        )
        if not self.edge.get("layout", True):
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawPath(self.path)
        if self.edge.get("label"):
            a, b = self.points[-2:]
            length = math.dist(a, b)
            ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
            end = QPointF(*b)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawPolygon(
                QPolygonF(
                    [
                        end,
                        QPointF(b[0] - ux * 7 - uy * 3.5, b[1] - uy * 7 + ux * 3.5),
                        QPointF(b[0] - ux * 7 + uy * 3.5, b[1] - uy * 7 - ux * 3.5),
                    ]
                )
            )
        if not self.badge.isEmpty() and painter.worldTransform().m11() >= 0.55:
            painter.setPen(QPen(QColor("#dce7ec"), 0.7))
            painter.setBrush(QColor("#ffffff"))
            painter.drawRoundedRect(self.badge, 5, 5)
            painter.setFont(self.font)
            painter.setPen(QColor(COLORS.get(self.edge.get("kind"), "#557787")))
            painter.drawText(self.badge, Qt.AlignmentFlag.AlignCenter, self.edge["label"])

    def hoverEnterEvent(self, event):
        self.hovered.emit(self, True)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.hovered.emit(self, False)
        super().hoverLeaveEvent(event)

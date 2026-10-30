"""Non-modal image preview. It never navigates or alters the reader."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QImageReader, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)


class ImageView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.fit_mode = True

    def fit(self):
        self.resetTransform()
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.fit_mode = True

    def original(self):
        self.resetTransform()
        ratio = 1 / self.devicePixelRatioF()
        self.scale(ratio, ratio)
        self.fit_mode = False

    def wheelEvent(self, event):
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        zoom = self.transform().m11() * factor
        if 0.03 <= zoom <= 32:
            self.fit_mode = False
            self.scale(factor, factor)
        event.accept()

    def mouseDoubleClickEvent(self, event):
        self.original() if self.fit_mode else self.fit()
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit_mode and self.scene() and not self.sceneRect().isEmpty():
            self.fit()


class ImagePreview(QDialog):
    def __init__(self, resources, paths, selected, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resources, self.paths = resources, list(dict.fromkeys(paths))
        if selected not in self.paths:
            self.paths.append(selected)
        self.position = self.paths.index(selected)
        self.resize(900, 680)
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.previous = QPushButton("上一张")
        self.next = QPushButton("下一张")
        self.previous.clicked.connect(lambda: self.move(-1))
        self.next.clicked.connect(lambda: self.move(1))
        self.view = ImageView()
        self.scene = QGraphicsScene(self)
        self.view.setScene(self.scene)
        for widget in (self.previous, self.next):
            controls.addWidget(widget)
        for label, callback in (("适应窗口", self.view.fit), ("100%", self.view.original)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            controls.addWidget(button)
        controls.addStretch()
        layout.addLayout(controls)
        layout.addWidget(self.view, 1)
        self.info = QLabel()
        self.info.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.info)
        for shortcut, delta in (("Left", -1), ("Right", 1)):
            action = QAction(self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(lambda checked=False, step=delta: self.move(step))
            self.addAction(action)
        self.load()

    def move(self, delta):
        self.position = max(0, min(len(self.paths) - 1, self.position + delta))
        try:
            self.load()
        except (ValueError, OSError) as exc:
            self.info.setText(str(exc))
            self.previous.setEnabled(self.position > 0)
            self.next.setEnabled(self.position + 1 < len(self.paths))

    def load(self):
        path = self.resources.image(self.paths[self.position])
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        image = reader.read()
        if image.isNull():
            raise ValueError("图片无法解码：" + reader.errorString())
        self.scene.clear()
        item = QGraphicsPixmapItem(QPixmap.fromImage(image))
        self.scene.addItem(item)
        self.scene.setSceneRect(item.boundingRect())
        self.view.fit()
        self.previous.setEnabled(self.position > 0)
        self.next.setEnabled(self.position + 1 < len(self.paths))
        self.info.setText(
            f"{self.position + 1}/{len(self.paths)} · {image.width()} × {image.height()}"
            " · 滚轮缩放，拖动平移"
        )
        self.setWindowTitle(path.name + " · 图片预览")

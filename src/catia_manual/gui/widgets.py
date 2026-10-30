"""Small shared widgets; dimensions use Qt logical pixels and respect display scaling."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QLabel


class ElidedLabel(QLabel):
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().windowText().color())
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignVCenter, text)

    def minimumSizeHint(self):
        hint = super().minimumSizeHint()
        hint.setWidth(0)
        return hint


class ReaderView(QWebEngineView):
    """Keep the last rendered frame visible until the new page's mode/cache are applied."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cover = QLabel(self)
        self.cover.setStyleSheet("background:white;color:#7b8a99;padding:0;")
        self.cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover.hide()

    def begin_navigation(self):
        if not self.cover.isVisible():
            self.cover.setPixmap(self.grab())
        self.cover.setGeometry(self.rect())
        self.cover.show()
        self.cover.raise_()

    def reveal(self):
        self.cover.hide()
        self.cover.clear()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.cover.setGeometry(self.rect())

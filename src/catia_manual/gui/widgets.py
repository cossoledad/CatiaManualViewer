"""Small shared widgets; dimensions use Qt logical pixels and respect display scaling."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QLabel, QLineEdit, QToolButton

from catia_manual.gui.style import icon


class SearchField(QLineEdit):
    """Reserve an explicit icon gutter, independent of native line-edit action offsets."""

    def __init__(self):
        super().__init__()
        self.setObjectName("search")
        self.setMinimumHeight(38)
        self.setTextMargins(30, 0, 26, 0)
        self.search_icon = QLabel(self)
        self.search_icon.setPixmap(icon("search").pixmap(QSize(18, 18)))
        self.search_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.clear_button = QToolButton(self)
        self.clear_button.setIcon(icon("close"))
        self.clear_button.setIconSize(QSize(14, 14))
        self.clear_button.setToolTip("清除搜索")
        self.clear_button.setAccessibleName("清除搜索")
        self.clear_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.clear_button.setStyleSheet(
            "QToolButton{padding:0;border:0;min-width:0;min-height:0;}"
        )
        self.clear_button.clicked.connect(self.clear)
        self.clear_button.hide()
        self.textChanged.connect(lambda text: self.clear_button.setVisible(bool(text)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.search_icon.setGeometry(12, (self.height() - 18) // 2, 18, 18)
        self.clear_button.setGeometry(self.width() - 30, (self.height() - 22) // 2, 22, 22)


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
        self.cover.setScaledContents(True)
        self.cover.hide()

    def begin_navigation(self):
        if not self.cover.isVisible():
            if self.isVisible() and self.url().isValid():
                self.cover.setPixmap(self.grab())
            else:
                # Grabbing a hidden WebEngine widget can force an early native surface resize.
                self.cover.clear()
        self.cover.setGeometry(self.rect())
        self.cover.show()
        self.cover.raise_()

    def reveal(self):
        self.cover.hide()
        self.cover.clear()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.cover.setGeometry(self.rect())

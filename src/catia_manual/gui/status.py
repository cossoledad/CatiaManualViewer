"""Source-scoped status messages and an inspectable activity panel."""

import re
import time
from collections import deque
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QListWidget, QStatusBar, QToolButton, QVBoxLayout

from catia_manual.gui.widgets import ElidedLabel


def safe_message(text):
    return re.sub(
        r"(?i)((?:api[_-]?key|token|authorization|password)[\s=:]+)([^\s,;]+)",
        r"\1***",
        str(text),
    )


@dataclass
class Message:
    text: str
    priority: int
    expires: float
    created: float


class StatusCenter(QStatusBar):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.messages = {}
        self.entries = deque(maxlen=100)
        self.label = ElidedLabel()
        self.label.setMinimumWidth(0)
        self.addWidget(self.label, 1)
        self.activity = QToolButton()
        self.activity.setText("活动")
        self.activity.setToolTip("后台任务与消息记录")
        self.activity.clicked.connect(self.open_activity)
        self.addPermanentWidget(self.activity)
        self.panel = None
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(250)

    def publish(self, source, text, *, priority=20, timeout=4000, record=False):
        text = safe_message(text)
        now = time.monotonic()
        self.messages[source] = Message(text, priority, now + timeout / 1000 if timeout else 0, now)
        if record:
            entry = f"{time.strftime('%H:%M:%S')}  {text}"
            if not self.entries or self.entries[-1][1] != text:
                self.entries.append((entry, text))
        self.refresh()

    def clear_source(self, source):
        self.messages.pop(source, None)
        self.refresh()

    def showMessage(self, text, timeout=4000):  # Qt-compatible entry for incidental notices.
        self.publish("notice", text, timeout=timeout, record=True)

    def clearMessage(self):
        self.clear_source("notice")

    def currentMessage(self):
        return self.label.text()

    def refresh(self):
        now = time.monotonic()
        self.messages = {
            key: value
            for key, value in self.messages.items()
            if not value.expires or value.expires > now
        }
        message = max(self.messages.values(), key=lambda m: (m.priority, m.created), default=None)
        text = message.text if message else "就绪"
        self.label.setText(text)
        self.label.setToolTip(text)
        if self.panel is not None and self.panel.isVisible():
            self.fill_activity()

    def fill_activity(self):
        rows = [
            "进行中 · " + message.text for message in self.messages.values() if not message.expires
        ]
        rows += [entry for entry, _ in reversed(self.entries)]
        if rows != getattr(self, "panel_rows", None):
            self.panel_rows = rows
            self.log.clear()
            self.log.addItems(rows or ["暂无任务与消息"])

    def open_activity(self):
        if self.panel is None:
            self.panel = QDialog(self.window())
            self.panel.setWindowTitle("活动 · Catia Manual Viewer")
            self.panel.resize(650, 360)
            self.panel.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
            layout = QVBoxLayout(self.panel)
            self.log = QListWidget()
            self.log.setWordWrap(True)
            layout.addWidget(self.log)
        self.fill_activity()
        self.panel.show()
        self.panel.raise_()

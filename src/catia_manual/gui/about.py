"""A small application page, available without a document library or CATIA."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)

from catia_manual import __version__
from catia_manual.gui.style import APP_NAME, app_icon


class AboutDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("关于 · " + APP_NAME)
        self.setWindowIcon(app_icon())
        self.setMinimumWidth(460)
        self.setMaximumWidth(540)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 24)
        layout.setSpacing(18)
        header = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(72, 72))
        header.addWidget(logo)
        titles = QVBoxLayout()
        name = QLabel(APP_NAME)
        name.setObjectName("categoryTitle")
        titles.addWidget(name)
        version = QLabel("版本 " + __version__)
        version.setObjectName("muted")
        titles.addWidget(version)
        header.addLayout(titles)
        header.addStretch()
        layout.addLayout(header)
        description = QLabel("让 CATIA V5 手册更易查找与阅读。\n功能导航 · 中英文检索 · 双语阅读")
        description.setWordWrap(True)
        layout.addWidget(description)
        link = QLabel(
            '<a href="https://github.com/cossoledad/CatiaManualViewer">项目主页与版本更新 ↗</a>'
        )
        link.setOpenExternalLinks(True)
        layout.addWidget(link)
        label = QLabel("当前文档库")
        label.setObjectName("sectionTitle")
        layout.addWidget(label)
        library = QLabel(str(parent.settings.root))
        library.setWordWrap(True)
        library.setTextFormat(Qt.TextFormat.PlainText)
        library.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(library)
        credit = QLabel(
            "基于 Python、Qt / PySide6、SQLite 和 lxml。\n"
            "独立开发的文档阅读工具。CATIA 及原始手册属于其各自权利人。"
        )
        credit.setObjectName("muted")
        credit.setWordWrap(True)
        layout.addWidget(credit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

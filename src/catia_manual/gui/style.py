from pathlib import Path

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ASSETS = Path(__file__).resolve().parents[1] / "assets"
APP_NAME = "Catia Manual Viewer"


def app_icon() -> QIcon:
    return QIcon(str(ASSETS / "app.ico" if (ASSETS / "app.ico").exists() else ASSETS / "app.svg"))


def icon(name: str, color="#60758a") -> QIcon:
    paths = {
        "home": "M3 10l9-7 9 7M5 9v12h5v-7h4v7h5V9",
        "back": "M14 5l-7 7 7 7M7 12h13",
        "forward": "M10 5l7 7-7 7M4 12h13",
        "search": "M20 20l-5-5M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0",
        "settings": "M4 6h16M4 12h16M4 18h16M8 3v6M16 9v6M10 15v6",
        "refresh": "M20 7v5h-5M4 17v-5h5M5 8a8 8 0 0 1 14-2l1 6M4 12l1 6a8 8 0 0 0 14-2",
        "book": "M3 4q5-2 9 1 4-3 9-1v15q-5-2-9 1-4-3-9-1zM12 5v15",
        "grid": "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z",
        "tools": "M3 4h18v16H3zM15 4v16",
        "layers": "M12 3l9 5-9 5-9-5zM3 12l9 5 9-5M3 16l9 5 9-5",
        "cube": "M12 2l9 5v10l-9 5-9-5V7zM3 7l9 5 9-5M12 12v10",
        "surface": "M3 6c5-7 12 7 18 0M3 12c5-7 12 7 18 0M3 18c5-7 12 7 18 0",
        "chart": "M3 3v18h18M7 16v-5M12 16V6M17 16v-8",
        "system": "M3 3h6v6H3zM15 15h6v6h-6zM6 9v9h9M15 3h6v6h-6zM18 9v6",
        "tool": "M8 3h8v7l-4 5-4-5zM12 15v5M3 21h18M6 5h2M16 5h2",
        "network": "M4 4h5v5H4zM15 15h5v5h-5zM15 3h5v5h-5zM9 6h6M6 9v9h9M17 8v7",
    }
    source = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
        f'<path d="{paths.get(name, paths["book"])}" fill="none" stroke="{color}" '
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>'
    )
    result = QIcon()
    renderer = QSvgRenderer(QByteArray(source.encode()))
    for size in (18, 24, 36, 48):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        result.addPixmap(pixmap)
    return result


STYLESHEET = """
QMainWindow,QDialog {background:#f3f6fa;color:#23394d;}
QWidget {color:#23394d;}
QFrame#topbar {background:#ffffff;border-bottom:1px solid #e1e8ef;}
QLabel#brand {font-size:17px;font-weight:600;color:#183e54;}
QLabel#muted {color:#7b8a99;}
QLabel#hero {font-size:30px;font-weight:600;color:#163b51;}
QLabel#categoryTitle {font-size:17px;font-weight:600;color:#25475c;}
QLabel#eyebrow {font-size:11px;color:#738a98;}
QFrame#catalogCard {background:white;border:1px solid #e0e8ef;border-radius:12px;}
QFrame#catalogCard:hover {background:#f6fcfc;border:1px solid #87bdbc;}
QLineEdit,QComboBox,QSpinBox {background:#ffffff;border:1px solid #dce5ed;border-radius:7px;
padding:7px 9px;selection-background-color:#c1e2df;}
QLineEdit#search {background:#f5f8fb;padding:9px 12px;}
QLineEdit:focus,QComboBox:focus,QSpinBox:focus {border:1px solid #579b9b;}
QComboBox::drop-down {border:0;width:24px;}
QPushButton {background:white;border:1px solid #dce5ed;border-radius:7px;padding:7px 12px;}
QPushButton:hover {background:#edf5f6;border-color:#b9d6d8;}
QPushButton#primary {background:#1b777a;border-color:#1b777a;color:white;font-weight:600;}
QPushButton#primary:hover {background:#14666a;}
QToolButton {background:transparent;border:0;border-radius:6px;padding:7px;}
QToolButton:hover {background:#e6eef4;}
QToolButton:disabled {color:#b2bdc6;}
QTreeWidget,QListWidget,QTextBrowser {background:white;border:0;outline:0;}
QTreeWidget::item {padding:7px 4px;border-radius:5px;}
QTreeWidget::item:hover {background:#f1f6f9;}
QTreeWidget::item:selected {background:#dfefed;color:#1b666b;}
QListWidget::item {border-bottom:1px solid #eef2f6;padding:10px 8px;}
QListWidget::item:selected {background:#e9f4f2;color:#204f57;}
QTabWidget::pane {border:0;background:white;}
QTabBar::tab {background:transparent;border:0;color:#80909d;padding:11px 14px;}
QTabBar::tab:selected {color:#176e73;border-bottom:2px solid #2a8586;}
QScrollArea {border:0;background:transparent;}
QScrollArea>QWidget>QWidget {background:transparent;}
QSplitter::handle {background:#edf2f6;width:8px;}
QStatusBar {background:#f3f6fa;color:#7b8b99;font-size:11px;}
QMenu {background:white;border:1px solid #dce5ed;padding:5px;}
QMenu::item {padding:8px 24px;border-radius:4px;}
QMenu::item:selected {background:#e8f3f2;}
QCheckBox {spacing:7px;}
QCheckBox::indicator {width:15px;height:15px;}
QScrollBar:vertical {background:transparent;width:10px;margin:3px;}
QScrollBar::handle:vertical {background:#c6d2dc;min-height:28px;border-radius:3px;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {background:transparent;}
"""

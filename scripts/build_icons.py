"""Render the repository-native SVG icon to PNG/ICO for Windows launchers."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

app = QGuiApplication([])
assets = Path(__file__).resolve().parents[1] / "src/catia_manual/assets"
renderer = QSvgRenderer(str(assets / "app.svg"))
canvas = QImage(256, 256, QImage.Format.Format_ARGB32)
canvas.fill(Qt.GlobalColor.transparent)
painter = QPainter(canvas)
renderer.render(painter)
painter.end()
assert canvas.save(str(assets / "app.png"))
assert canvas.save(str(assets / "app.ico"), "ICO")
print("Created app.png and app.ico")

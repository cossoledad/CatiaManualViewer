"""Render the actual Qt reader and exercise async search, without opening CATIA."""

import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from catia_manual.config import load_settings  # noqa: E402
from catia_manual.gui.app import MainWindow  # noqa: E402

app = QApplication([])
# The Windows offscreen platform does not enumerate native system fonts.
for filename in ("segoeui.ttf", "msyh.ttc"):
    font = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / filename
    if font.is_file():
        QFontDatabase.addApplicationFont(str(font))
window = MainWindow(load_settings())
window.resize(1480, 940)
window.show()
failures = []
loaded = False
page_text = ""


def capture_text(text):
    global page_text
    page_text = text


def check_page(ok):
    global loaded
    loaded = ok
    if not ok:
        failures.append("WebEngine load failed")
    window.reader_page.toPlainText(capture_text)
    window.query.setText("creating pads")
    window.search()


def finish():
    if window.jobs:
        QTimer.singleShot(250, finish)
        return
    if not loaded:
        failures.append("No loaded page")
    if "Creating Pads" not in page_text:
        failures.append("Expected article text missing")
    if window.results.count() == 0:
        failures.append("No search results")
    if window.samples.count() == 0:
        failures.append("No sample links")
    output = Path(__file__).resolve().parents[1] / "data" / "gui-smoke.png"
    output.parent.mkdir(exist_ok=True)
    window.grab().save(str(output))
    print(
        json.dumps(
            {
                "loaded": loaded,
                "results": window.results.count(),
                "samples": window.samples.count(),
                "screenshot": str(output),
                "failures": failures,
            },
            ensure_ascii=False,
        )
    )
    window.close()
    app.exit(1 if failures else 0)


window.web.loadFinished.connect(check_page)
QTimer.singleShot(8000, finish)
QTimer.singleShot(30000, lambda: app.exit(2))
raise SystemExit(app.exec())

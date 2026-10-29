"""Translate a real installed CATIA article with the offline model, without remote calls."""

import json
import os
import time
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtCore import QThreadPool, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from catia_manual.config import load_settings  # noqa: E402
from catia_manual.gui.app import MainWindow  # noqa: E402

project = Path(__file__).resolve().parents[1]
settings = load_settings()
settings = replace(
    settings,
    config_path=project / ".cache/real-page.toml",
    translation={**settings.translation, "provider": "local"},
    appearance={**settings.appearance, "remember_window": False},
)
app = QApplication([])
window = MainWindow(settings, "online/prtug_C2/prtugbt0501.htm")
window.show()
started = False
deadline = time.monotonic() + 180
report = {"passed": False}


def poll():
    global started
    if time.monotonic() > deadline:
        report["error"] = "timeout"
        finish()
    elif window.page_ready and not started:
        started = True
        window.set_reading_mode("bilingual")
        QTimer.singleShot(200, poll)
    elif started and window.page_task.errors:
        report["error"] = window.page_task.errors
        finish()
    elif (
        started
        and not window.page_worker_running
        and len(window.page_task.results) == len(window.page_task.page.units)
    ):
        window.application_script("window.scrollTo(0,0)")
        QTimer.singleShot(200, capture)
    else:
        QTimer.singleShot(200, poll)


def capture():
    window.grab().save(str(project / "data/stage5-real-bilingual.png"))
    report["units"] = len(window.page_task.results)
    report["cached"] = sum(value.cached for value in window.page_task.results.values())
    report["passed"] = all(
        any("\u4e00" <= c <= "\u9fff" for c in value.text)
        for value in window.page_task.results.values()
    )
    window.set_reading_mode("chinese")
    QTimer.singleShot(200, capture_chinese)


def capture_chinese():
    window.grab().save(str(project / "data/stage5-real-chinese.png"))
    finish()


def finish():
    window.page_pump.stop()
    if window.page_task:
        window.page_task.paused = True
    (project / "data/stage5-real-page.json").write_text(json.dumps(report, indent=2), "utf-8")
    print(json.dumps(report))
    QThreadPool.globalInstance().waitForDone()
    window.close()
    app.exit(0 if report["passed"] else 1)


QTimer.singleShot(300, poll)
raise SystemExit(app.exec())

"""Local native Qt acceptance: PDF contents/snapshots and hidden-window MCP lifetime."""

import json
import os
import runpy
import socket
import stat
import sys
import time
from pathlib import Path

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

import httpx  # noqa: E402
import tomlkit  # noqa: E402
from PySide6.QtCore import QModelIndex, QSize, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtPdf import QPdfDocument, QPdfLinkModel  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QSystemTrayIcon  # noqa: E402

from catia_manual.config import load_settings, save_settings  # noqa: E402
from catia_manual.core.index import ManualIndex  # noqa: E402
from catia_manual.core.translation import TranslationResult  # noqa: E402
from catia_manual.gui.app import MainWindow  # noqa: E402

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project))
folder = project / ".cache/pdf-tray-gui" / str(time.time_ns())
folder.mkdir(parents=True)
root = runpy.run_path(str(project / "tests/test_structure.py"))["manual_library"].__wrapped__(
    folder / "manual"
)
source = root / "online/shared_v17/coincide.htm"
picture = QImage(240, 80, QImage.Format.Format_RGB32)
picture.fill(QColor("#d62828"))
picture.save(str(source.with_name("diagram.png")))
source.write_text(
    '<h1 id="top">PDF ORIGINAL HEADING</h1><p>SOURCEONLY paragraph.</p>'
    '<img src="diagram.png"><p><a href="https://example.org/reference">Reference link</a>'
    '<a href="coincide.htm#top">Top</a></p>'
    + "".join(
        f"<p>Step {i}. Select two supporting faces and confirm the constraint.</p>"
        for i in range(70)
    ),
    "utf-8",
)
database = folder / "data/index.sqlite3"
with ManualIndex(database, root) as index:
    index.build()
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
config = folder / "config.toml"
config.write_text(
    tomlkit.dumps(
        {
            "documentation": {"root": str(root), "database": str(database)},
            "translation": {"provider": "glossary", "automatic": False},
            "appearance": {"remember_window": False},
            "agent": {"port": port},
        }
    ),
    "utf-8",
)
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
w = MainWindow(load_settings(config), "online/shared_v17/coincide.htm")
w.show()
report = {"checks": [], "failures": [], "pdfs": []}
results = []
w.pdf_exporter.finished.connect(lambda path, ok: results.append((path, ok)))


def wait(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(30)
    raise TimeoutError("Native PDF/tray verification timed out")


def check(value, label):
    report["checks"].append({"name": label, "passed": bool(value)})
    print(("PASS " if value else "FAIL ") + label, flush=True)
    if not value:
        raise AssertionError(label)


def read_pdf(path):
    pdf = QPdfDocument()
    check(pdf.load(str(path)) == QPdfDocument.Error.None_, "generated PDF opens: " + path.name)
    text = "\n".join(pdf.getAllText(i).text() for i in range(pdf.pageCount()))
    return pdf, text.replace(" ", "").replace("\n", "")


try:
    wait(lambda: w.page_ready and not w.page_loading and not w.jobs)
    check(w.pdf_action.isEnabled(), "article enables the toolbar PDF action")
    w.restore_scroll(0, 180)
    QTest.qWait(150)
    for mode in ("original", "bilingual", "chinese"):
        task = w.page_task
        task.mode = mode
        task.paused = True
        task.results = {
            u.id: TranslationResult("中文译文 TRANSLATED", "fixture", True) for u in task.page.units
        }
        w.apply_page_update()
        QTest.qWait(250)
        path = folder / (mode + ".pdf")
        scroll = w.reader_page.scrollPosition().y()
        w.pdf_exporter.start(path)
        check(not w.pdf_action.isEnabled(), "PDF action prevents duplicate exports")
        wait(lambda: not w.pdf_exporter.busy)
        check(results[-1] == (str(path), True), "PDF generation completes: " + mode)
        pdf, text = read_pdf(path)
        check(
            ("SOURCEONLY" in text) == (mode != "chinese")
            and ("TRANSLATED" in text) == (mode != "original"),
            "PDF respects reading mode: " + mode,
        )
        if mode == "original":
            check(pdf.pageCount() > 1, "printing includes the entire article, beyond the viewport")
            links = QPdfLinkModel()
            links.setDocument(pdf)
            links.setPage(0)
            check(links.rowCount(QModelIndex()) > 0, "PDF retains clickable document links")
            image = pdf.render(0, QSize(700, 990))
            image.save(str(folder / "pdf-preview.png"))
            red = sum(
                image.pixelColor(x, y).red() > 150 and image.pixelColor(x, y).green() < 100
                for x in range(0, image.width(), 3)
                for y in range(0, image.height(), 3)
            )
            check(red > 100, "PDF includes the original image")
        report["pdfs"].append({"file": str(path), "pages": pdf.pageCount(), "mode": mode})
        pdf.close()
        check(
            abs(w.reader_page.scrollPosition().y() - scroll) < 2,
            "export preserves reading position: " + mode,
        )
    path = folder / "snapshot.pdf"
    w.pdf_exporter.start(path)
    wait(lambda: w.pdf_exporter.page is not None)
    snapshot = w.pdf_exporter.snapshot.read_text("utf-8")
    check('href="#top"' in snapshot, "snapshot preserves current-article anchors")
    w.open_document("online/shared_v17/offset.htm")
    wait(lambda: not w.pdf_exporter.busy and w.page_ready and not w.page_loading and not w.jobs)
    pdf, text = read_pdf(path)
    check(
        "TRANSLATED" in text and "ManagingOffset" not in text,
        "navigation while printing cannot substitute another article",
    )
    pdf.close()
    existing = folder / "read-only.pdf"
    existing.write_bytes(b"existing file must survive failed export")
    existing.chmod(stat.S_IREAD)
    try:
        w.pdf_exporter.start(existing)
        wait(lambda: not w.pdf_exporter.busy)
        check(
            not results[-1][1]
            and existing.read_bytes() == b"existing file must survive failed export",
            "failed save preserves the existing file and reports failure",
        )
    finally:
        existing.chmod(stat.S_IWRITE)
    w.show_home()
    check(not w.pdf_action.isEnabled(), "home view cannot print a stale article")
    w.agent_controller.start()
    wait(lambda: w.agent_controller.state == "running")
    pid = w.agent_controller.process.processId()
    check(w.tray_controller.available(), "native system tray is available")
    w.showMinimized()
    QTest.qWait(200)
    check(w.isVisible() and w.isMinimized(), "minimize retains the ordinary taskbar window")
    w.tray_controller.restore()
    w.showMaximized()
    # Exercise the actual close chooser, including cancel and persisted selection.
    from PySide6.QtWidgets import QMessageBox

    def choose_close(text, remember=False):
        dialog = app.activeModalWidget()
        check(isinstance(dialog, QMessageBox), "close opens the native choice dialog")
        if remember:
            dialog.checkBox().setChecked(True)
        button = next(button for button in dialog.buttons() if button.text() == text)
        button.click()

    def cancel_close():
        dialog = app.activeModalWidget()
        if isinstance(dialog, QMessageBox):
            dialog.button(QMessageBox.StandardButton.Cancel).click()

    QTimer.singleShot(50, cancel_close)
    w.close()
    check(w.isVisible() and not w.tray_controller.quitting, "cancel close preserves the viewer")
    QTimer.singleShot(80, lambda: choose_close("留在系统托盘", True))
    w.close()
    wait(lambda: not w.isVisible())
    check(load_settings(config).appearance["close_behavior"] == "tray", "close preference persists")
    with httpx.Client(trust_env=False, timeout=3) as client:
        check(
            client.get(f"http://127.0.0.1:{port}/health").status_code == 200
            and w.agent_controller.process.processId() == pid,
            "closing to tray keeps the same MCP process alive",
        )
    w.tray_controller.tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
    wait(lambda: w.isVisible() and not w.isMinimized())
    w.showMaximized()
    w.showMinimized()
    w.tray_controller.hide_window()
    w.activate_request({})
    check(
        w.isVisible() and w.isMaximized(), "secondary-instance activation restores maximized state"
    )
    w.settings = save_settings(w.settings, {"appearance": {"close_behavior": "quit"}})
    w.showMinimized()
    QTest.qWait(150)
    check(
        w.isVisible()
        and w.isMinimized()
        and load_settings(config).appearance["close_behavior"] == "quit",
        "ordinary minimization preference persists",
    )
    w.tray_controller.restore()
    original_available = w.tray_controller.available
    w.tray_controller.available = lambda: False
    w.settings = save_settings(w.settings, {"appearance": {"close_behavior": "tray"}})
    w.showMinimized()
    QTest.qWait(150)
    check(w.isVisible(), "missing system tray never makes the window inaccessible")
    check(w.tray_controller.request_close(), "missing system tray falls back to application exit")
    w.tray_controller.available = original_available
    w.tray_controller.restore()
    w.open_document("online/shared_v17/coincide.htm")
    wait(lambda: w.page_ready and not w.page_loading and not w.jobs)
    closing_pdf = folder / "saved-before-exit.pdf"
    w.pdf_exporter.start(closing_pdf)
    w.tray_controller.hide_window()
    QTimer.singleShot(0, w.tray_controller.quit)
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(lambda: app.exit(2))
    watchdog.start(10000)
    code = app.exec()
    check(
        code == 0 and not w.agent_controller.active and not w.tray_controller.tray.isVisible(),
        "tray Exit stops MCP and ends the application even when its window was hidden",
    )
    check(
        closing_pdf.is_file() and results[-1] == (str(closing_pdf), True),
        "exit waits for the pending PDF to finish before releasing WebEngine",
    )
except Exception as exc:
    report["failures"].append(f"{type(exc).__name__}: {exc}")
    print(report["failures"][-1], flush=True)
finally:
    if w.agent_controller.active:
        w.agent_controller.stop()
        wait(lambda: not w.agent_controller.active)
    if w.isVisible():
        w.tray_controller.quit()
        wait(lambda: not w.jobs and not w.pdf_exporter.busy)
    report["passed"] = not report["failures"]
    (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print("Report: " + str(folder / "report.json"), flush=True)
raise SystemExit(0 if report["passed"] else 1)

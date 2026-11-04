"""Native share dialog, clipboard, article position and secondary-instance acceptance."""

import json
import os
import runpy
import sys
import time
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtCore import QProcess, QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from catia_manual.config import load_settings  # noqa: E402
from catia_manual.core.index import ManualIndex  # noqa: E402
from catia_manual.core.sharing import PageLink  # noqa: E402
from catia_manual.gui.app import MainWindow  # noqa: E402
from catia_manual.gui.sharing import ShareDialog, register_protocol  # noqa: E402
from catia_manual.gui.single_instance import InstanceBroker  # noqa: E402

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project))
folder = project / ".cache/share-gui" / str(time.time_ns())
folder.mkdir(parents=True)
root = runpy.run_path(str(project / "tests/test_structure.py"))["manual_library"].__wrapped__(
    folder / "manual"
)
database = folder / "data/index.sqlite3"
with ManualIndex(database, root) as index:
    index.build()
config = folder / "config.toml"
config.write_text(
    "[documentation]\nroot='"
    + str(root)
    + "'\ndatabase='"
    + str(database)
    + "'\n[appearance]\nremember_window=false\nclose_behavior='quit'\n"
    + "[translation]\nprovider='glossary'\nautomatic=false\n",
    "utf-8",
)
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
settings = load_settings(config)
broker = InstanceBroker(settings, app)
assert broker.take_or_forward({})
w = MainWindow(settings, "online/shared_v17/coincide.htm")
broker.request.connect(w.activate_request)
w.show()
report = {"checks": [], "failures": []}
shared = []


def check(value, label):
    report["checks"].append({"name": label, "passed": bool(value)})
    print(("PASS " if value else "FAIL ") + label, flush=True)
    if not value:
        raise AssertionError(label)


def wait(predicate, seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(25)
    raise TimeoutError("Share UI acceptance timed out")


def close_share():
    dialog = app.activeModalWidget()
    if not isinstance(dialog, ShareDialog):
        return
    uri = dialog.uri_field.text()
    shared.append(uri)
    check(PageLink.parse(uri).block_id, "share captures the visible source paragraph")
    check(not dialog.web_field.text(), "stopped service does not offer an unusable browser URL")
    copy = next(
        b for b in dialog.uri_field.parentWidget().findChildren(QPushButton) if b.text() == "复制"
    )
    copy.click()
    check(app.clipboard().text() == uri, "copy application link updates the clipboard")
    dialog.grab().save(str(folder / "share-dialog.png"))
    timer.stop()
    dialog.accept()


timer = QTimer()
timer.setInterval(50)
timer.timeout.connect(close_share)
try:
    wait(lambda: w.page_ready and not w.page_loading and not w.jobs)
    check(w.share_action.isEnabled(), "article enables sharing")
    timer.start()
    w.share_action.trigger()
    wait(lambda: bool(shared))
    uri = shared[0]
    w.open_document("online/shared_v17/offset.htm")
    wait(lambda: w.page_ready and not w.page_loading and not w.jobs)
    w.tray_controller.hide_window()
    process = QProcess()
    process.start(
        sys.executable, ["-m", "catia_manual.entry", "--config", str(config), "--open", uri]
    )
    wait(lambda: process.state() == QProcess.ProcessState.NotRunning)
    check(process.exitCode() == 0, "secondary process forwards the share to the existing viewer")
    wait(lambda: w.page_ready and not w.page_loading and not w.jobs)
    check(
        w.isVisible() and w.current_document.path == PageLink.parse(uri).path,
        "forwarded share restores the window and opens the correct article",
    )
    check(
        w.history.current.source["block_id"] == PageLink.parse(uri).block_id,
        "opened share retains the exact source paragraph",
    )
    with patch("winreg.CreateKey") as key, patch("winreg.SetValueEx") as write:
        register_protocol()
        commands = [c.args[4] for c in write.call_args_list if "--share-uri" in str(c)]
        check(
            commands and '--share-uri "%1"' in commands[0],
            "URL handler uses a quoted, dedicated share entry",
        )
        check(
            all(c.args[0] == __import__("winreg").HKEY_CURRENT_USER for c in key.call_args_list),
            "URL association is per-user and needs no administrator",
        )
    w.show_home()
    check(not w.share_action.isEnabled(), "home cannot share a stale article")
except Exception as exc:
    report["failures"].append(f"{type(exc).__name__}: {exc}")
finally:
    timer.stop()
    broker.close()
    w.tray_controller.quit()
    wait(lambda: not w.jobs and not w.isVisible())
    report["passed"] = not report["failures"]
    (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    print("Report: " + str(folder / "report.json"), flush=True)
raise SystemExit(0 if report["passed"] else 1)

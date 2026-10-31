"""Native migration acceptance using a small local library; no CATIA or online services."""

import json
import os
import runpy
import time
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from catia_manual.config import load_settings  # noqa: E402
from catia_manual.core.index import ManualIndex  # noqa: E402
from catia_manual.core.library import connect_readonly  # noqa: E402
from catia_manual.core.transfer import inspect_bundle  # noqa: E402
from catia_manual.gui.app import MainWindow  # noqa: E402
from catia_manual.gui.transfer import TransferDialog  # noqa: E402
from catia_manual.integrations.catia import CatiaLauncher  # noqa: E402

project = Path(__file__).resolve().parents[1]
folder = project / ".cache/transfer-gui" / str(time.time_ns())
folder.mkdir(parents=True)
fixture = runpy.run_path(str(project / "tests/test_transfer.py"))["library"].__wrapped__(folder)
settings, index = next(fixture)
identity = index.library_id
fixture.close()
settings = replace(
    settings,
    translation={**settings.translation, "automatic": False},
    search={**settings.search, "mode": "keyword"},
)
CatiaLauncher.session_available = staticmethod(lambda: False)
app = QApplication([])
app.setFont(QFont("Microsoft YaHei UI", 10))
window = MainWindow(settings, "online/prtug_C2/pad.htm")
window.show()
report = {"checks": [], "failures": []}
output = project / "data"
output.mkdir(exist_ok=True)
dialog = None


def wait(predicate, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            QTest.qWait(60)
            return
        QTest.qWait(20)
    raise TimeoutError("migration acceptance timed out")


def check(value, name):
    report["checks"].append({"name": name, "passed": bool(value)})
    if not value:
        report["failures"].append(name)
    print(("PASS " if value else "FAIL ") + name, flush=True)


try:
    wait(lambda: window.page_ready and not window.page_loading and not window.jobs)
    check(
        window.transfer_action.text() == "数据迁移…", "migration is available from the global menu"
    )
    dialog = TransferDialog(window)
    dialog.show()
    archive = folder / "export.zip"
    dialog.export_to(archive)
    wait(lambda: not dialog.busy and not window.jobs)
    manifest = inspect_bundle(archive)
    check(
        manifest["documents"] and manifest["models"] and manifest["library_id"] == identity,
        "GUI exports a complete library with optional documents and models",
    )
    dialog.inspect(archive)
    wait(lambda: not dialog.busy and not window.jobs)
    check(
        dialog.import_button.isEnabled() and not dialog.root.isEnabled(),
        "bundled documents require no manual path selection",
    )
    dialog.tabs.setCurrentIndex(1)
    old_database, old_root = window.settings.database, window.settings.root
    old_launcher = window.settings.launcher
    dialog.start_import()
    wait(lambda: not dialog.busy and not window.jobs and not window.page_loading)
    updated = load_settings(settings.config_path)
    check(
        updated.database != old_database
        and updated.root != old_root
        and updated.database.is_file(),
        "successful import activates a new directory and persists it",
    )
    with ManualIndex(updated.database, updated.root) as imported:
        check(
            imported.library_id == identity and bool(imported.search("Creating Pads")),
            "imported GUI library is immediately searchable without rebuilding",
        )
        check(
            imported.connection.execute("SELECT value FROM user_payload").fetchone()[0]
            == b"translations, vectors and views",
            "complete application data survives the GUI roundtrip",
        )
    with connect_readonly(old_database) as original:
        check(
            original.execute("SELECT value FROM metadata WHERE key='root'").fetchone()[0]
            == str(old_root),
            "original database remains mounted to its original documents",
        )
    check(
        updated.launcher == old_launcher and "api_key_override" not in updated.translation,
        "target CATIA configuration is kept and transfer credentials are omitted",
    )
    check(
        (updated.database.parents[1] / "previous-config.toml").is_file(),
        "activation keeps a local copy of the previous settings",
    )
    dialog.grab().save(str(output / "transfer.png"))
    cancelled = folder / "cancelled.zip"
    dialog.export_to(cancelled)
    dialog.reject()
    wait(lambda: not dialog.busy and not window.jobs)
    check(
        not cancelled.exists() and "取消" in dialog.status.text(),
        "cancelled export leaves no partial bundle",
    )
except Exception as exc:
    report["failures"].append(repr(exc))
finally:
    if dialog:
        dialog.close()
    window.close()
    wait(lambda: not window.isVisible() and not window.jobs)
    report["passed"] = not report["failures"]
    (output / "transfer-gui.json").write_text(json.dumps(report, indent=2), "utf-8")
raise SystemExit(0 if report["passed"] else 1)

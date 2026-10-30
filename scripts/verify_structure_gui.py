"""Structured search/navigation acceptance without CATIA, network or model inference."""

import json
import os
import runpy
import sys
import time
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from catia_manual.config import load_settings  # noqa: E402
from catia_manual.core.index import ManualIndex  # noqa: E402
from catia_manual.core.structure import StructureIndex  # noqa: E402
from catia_manual.gui.app import MainWindow  # noqa: E402
from catia_manual.integrations.catia import CatiaLauncher  # noqa: E402

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project))
folder = project / ".cache/structure-gui" / str(time.time_ns())
folder.mkdir(parents=True)
root = runpy.run_path(str(project / "tests/test_structure.py"))["manual_library"].__wrapped__(
    folder
)
database = folder / "data/index.sqlite3"
with ManualIndex(database, root) as index:
    index.build()
    positions = StructureIndex(index).placements("online/shared_v17/coincide.htm", ["wd1ug_C2"])
    weld = positions[0]
CatiaLauncher.session_available = staticmethod(lambda: False)
settings = replace(
    load_settings(),
    root=root,
    database=database,
    config_path=folder / "config.toml",
    launcher=folder / "missing.exe",
    environment="",
    environment_dir=folder / "none",
    search={"mode": "hybrid", "model_dir": str(folder / "no-model")},
    translation={"provider": "glossary", "automatic": False},
    appearance={"reading_mode": "original", "remember_window": False, "show_tools": True},
)
app = QApplication([])
app.setFont(QFont("Microsoft YaHei UI", 10))
window = MainWindow(settings)
window.show()
report = {"checks": [], "failures": []}


def check(condition, label):
    report["checks"].append({"name": label, "passed": bool(condition)})
    if not condition:
        report["failures"].append(label)


def wait(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(20)
    raise TimeoutError("structured GUI acceptance timed out")


def ready():
    wait(
        lambda: (
            window.page_ready
            and not window.page_loading
            and not window.jobs
            and window.center_stack.currentWidget() == window.web
            and not window.web.cover.isVisible()
        )
    )


def selected():
    item = window.tree.currentItem()
    return item.data(0, Qt.ItemDataRole.UserRole) if item else {}


try:
    wait(lambda: not window.jobs)
    window.set_scope("manual", "asmug_C2")
    window.query.setText("相合约束")
    window.search()
    wait(lambda: not window.jobs)
    hit = window.results.item(0)
    check(
        "Managing Coincidence Constraint" in hit.text(), "Chinese command selects shared tutorial"
    )
    check(
        "操作教程" in hit.text() and "装配设计" in hit.text(),
        "result displays functional ownership and role",
    )
    window.results.setCurrentRow(0)
    count = window.results.count()
    window.activate_result(hit)
    ready()
    check(
        window.tabs.currentWidget() == window.tree,
        "opening a result activates functional navigation",
    )
    check(
        selected().get("path") == "online/shared_v17/coincide.htm",
        "tree selects the actual shared page",
    )
    check(selected().get("manual") == "asmug_C2", "tree keeps the search result's manual context")
    check("管理约束" in window.breadcrumb.text(), "breadcrumb includes the section hierarchy")
    check(window.module.currentData()["id"] == "asmug_C2", "reading does not rewrite search scope")
    check(
        len(window.history.entries) == 2,
        "programmatic tree synchronization does not duplicate history",
    )
    window.return_results.click()
    check(
        window.tabs.currentWidget() == window.results
        and window.results.count() == count
        and window.results.currentItem() is hit
        and window.query.text() == "相合约束",
        "return to search preserves query results and selection",
    )
    window.open_document("online/shared_v17/coincide.htm", placement=weld)
    ready()
    check(selected().get("manual") == "wd1ug_C2", "one document can select its weld placement")
    window.go_back()
    ready()
    check(selected().get("manual") == "asmug_C2", "back restores the former manual placement")
    check(window.reference_reading.isVisible(), "task exposes its authored reference link")
    reference = window.current_navigation["related"][0]
    window.open_source(reference["source"], placement={"manual": "asmug_C2"})
    ready()
    check(
        window.current_document.path.endswith("reference.htm")
        and selected().get("path", "").endswith("reference.htm"),
        "reference reading also synchronizes its own directory position",
    )
    window.go_back()
    ready()
    window.breadcrumb_clicked("2")
    ready()
    check(window.current_document.path.endswith("overview.htm"), "breadcrumb opens parent overview")
    window.set_scope("all")
    window.query.setText("偏移约束")
    window.search()
    wait(lambda: not window.jobs)
    window.activate_result(window.results.item(0))
    ready()
    check(
        window.module.currentData()["kind"] == "all", "global search remains global after reading"
    )
    check(
        not any(name.startswith(("onnxruntime", "tokenizers")) for name in sys.modules),
        "known commands load no inference runtime even in hybrid mode",
    )
    output = project / "data"
    output.mkdir(exist_ok=True)
    window.grab().save(str(output / "structured-navigation.png"))
except Exception as exc:
    report["failures"].append(str(exc))
finally:
    window.page_pump.stop()
    window.position_timer.stop()
    window.flush_reading_locations()
    wait(lambda: not window.jobs)
    window.close()
    app.processEvents()
    report["passed"] = not report["failures"]
    (project / "data").mkdir(exist_ok=True)
    (project / "data/structured-gui.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
raise SystemExit(0 if report["passed"] else 1)

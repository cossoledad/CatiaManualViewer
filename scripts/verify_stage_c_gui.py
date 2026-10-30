"""Local C-round acceptance with real CPU embeddings and synthetic manuals. No remote APIs."""

import json
import os
import time
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from catia_manual.config import load_settings  # noqa: E402
from catia_manual.core.embedding import FINGERPRINT  # noqa: E402
from catia_manual.core.index import ManualIndex  # noqa: E402
from catia_manual.core.semantic import PASSAGE_VERSION, SemanticIndex  # noqa: E402
from catia_manual.gui.app import MainWindow  # noqa: E402
from catia_manual.gui.semantic import SemanticDialog  # noqa: E402
from catia_manual.gui.settings import GlobalSettings  # noqa: E402

project = Path(__file__).resolve().parents[1]
folder = project / ".cache/stage-c-gui" / str(time.time_ns())
root = folder / "manual"
module = root / "online/prtug_C2"
module.mkdir(parents=True)
(root / "online/CATIA_P3_TOC.xml").write_text(
    '<Root><ITEM name="CATIA"><ITEM name="Mechanical Design">'
    '<DITEM dir="prtug_C2" name="Part Design"/></ITEM></ITEM></Root>',
    "utf-8",
)
topics = [
    ("pad", "Creating Pads", "Extrude a sketch profile to create a solid pad. Specify a length."),
    ("shaft", "Creating Shafts", "Revolve a sketch around an axis to create a solid shaft."),
    (
        "pocket",
        "Creating Pockets",
        "Remove material from a solid by extruding a profile into a pocket.",
    ),
    ("chamfer", "Creating Chamfers", "Cut the sharp edges of a solid using a bevel chamfer."),
]
for name, title, text in topics:
    (module / f"{name}.htm").write_text(
        f"<h1>{title}</h1>"
        + "".join(
            f'<h2 id="step{i}">Step {i}</h2><p>' + (text + " ") * 8 + "</p>" for i in range(16)
        ),
        "utf-8",
    )
database = folder / "data/index.sqlite3"
with ManualIndex(database, root) as index:
    index.build()
settings = replace(
    load_settings(),
    root=root,
    database=database,
    config_path=folder / "config.toml",
    search={
        "mode": "keyword",
        "model_dir": str(project / "data/search-models/e5-small"),
        "threads": 2,
    },
    translation={"provider": "glossary", "automatic": False},
    appearance={"remember_window": False, "reading_mode": "original", "show_tools": True},
)
app = QApplication([])
app.setFont(QFont("Microsoft YaHei UI", 10))
window = MainWindow(settings, "online/prtug_C2/pad.htm")
window.show()
report = {"checks": [], "failures": []}
dialog = None


def check(condition, name):
    report["checks"].append({"name": name, "passed": bool(condition)})
    if not condition:
        report["failures"].append(name)


def wait(predicate, seconds=40):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(15)
    raise TimeoutError("C round GUI acceptance timed out")


def status():
    with ManualIndex(database, root) as index:
        return SemanticIndex(index).status(FINGERPRINT + ":" + PASSAGE_VERSION)


try:
    wait(lambda: window.page_ready and not window.jobs)
    dialog = SemanticDialog(window)
    dialog.show()
    wait(lambda: not dialog.busy)
    check(
        dialog.build.isEnabled() and not dialog.activate.isEnabled(),
        "model capability is independent of vector index readiness",
    )
    dialog.index()
    wait(lambda: dialog.progress.value() > 0)
    dialog.pause.click()
    wait(lambda: not dialog.busy)
    paused = status()
    check(paused["state"] == "paused" and paused["valid"] > 0, "pause retains committed vectors")
    dialog.index()
    wait(lambda: not dialog.busy)
    current = status()
    check(
        current["state"] == "ready" and current["valid"] > paused["valid"],
        "resume completes remaining passages",
    )
    check(dialog.activate.isEnabled(), "hybrid activation becomes available after indexing")
    dialog.enable()
    check(
        load_settings(settings.config_path).search["mode"] == "hybrid",
        "hybrid mode persists in portable settings",
    )
    (project / "data").mkdir(exist_ok=True)
    dialog.grab().save(str(project / "data/stage-c-model.png"))
    dialog.reject()
    window.query.setText("沿着轴把草图旋转成一个实体")
    window.search()
    wait(lambda: not window.jobs)
    hit = window.results.item(0)
    check(
        hit is not None and "Shafts" in hit.text(),
        "Chinese description retrieves the English shaft feature",
    )
    check(
        hit.data(Qt.ItemDataRole.UserRole + 1).get("block_id"),
        "semantic result retains a source block",
    )
    window.activate_result(hit)
    wait(
        lambda: (
            window.page_ready
            and not window.page_loading
            and not window.jobs
            and not window.web.cover.isVisible()
        )
    )
    check(
        window.current_document.path.endswith("shaft.htm"),
        "semantic result opens its actual source page",
    )
    heading = []
    window.application_script("document.querySelector('h1').innerText.trim()", heading.append)
    wait(lambda: bool(heading))
    check(
        heading[0] == "Creating Shafts",
        "visible DOM matches the source after transition cover clears",
    )
    window.grab().save(str(project / "data/stage-c-search.png"))
    global_settings = GlobalSettings(window.settings, window)
    check(
        global_settings.search_mode.currentData() == "hybrid",
        "global search settings reflect active mode",
    )
    global_settings.reject()
    global_settings.deleteLater()
    window.query.setText("Creating Pockets")
    window.search()
    window.query.setText("Creating Shafts")
    window.search()
    wait(lambda: not window.jobs)
    check(
        "Shafts" in window.results.item(0).text(), "a newer query supersedes pending semantic work"
    )
    window.settings = replace(
        window.settings,
        search={**window.settings.search, "model_dir": str(folder / "missing-model")},
    )
    window.query.setText("Creating Pockets")
    window.search()
    wait(lambda: not window.jobs)
    check(
        "Pockets" in window.results.item(0).text(),
        "missing model falls back to exact keyword result",
    )
    check(
        "语义检索未就绪" in window.statusBar().currentMessage(),
        "fallback reason is visible in status bar",
    )
except Exception as exc:
    report["failures"].append(str(exc))
finally:
    if dialog and dialog.busy:
        dialog.cancel.set()
        wait(lambda: not dialog.busy)
    if dialog:
        dialog.reject()
    window.page_pump.stop()
    window.position_timer.stop()
    window.flush_reading_locations()
    wait(lambda: not window.jobs)
    window.close()
    app.processEvents()
    report["passed"] = not report["failures"]
    (project / "data/stage-c-gui.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
raise SystemExit(0 if report["passed"] else 1)

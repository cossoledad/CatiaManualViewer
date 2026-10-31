"""Native functional-map acceptance; synthetic docs/provider, no network or CATIA."""

import json
import os
import runpy
import statistics
import sys
import time
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import catia_manual.gui.page_reader as reader  # noqa: E402
from catia_manual.config import load_settings  # noqa: E402
from catia_manual.core.index import ManualIndex  # noqa: E402
from catia_manual.gui.app import MainWindow  # noqa: E402
from catia_manual.integrations.catia import CatiaLauncher  # noqa: E402

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project))
folder = project / ".cache/function-map-gui" / str(time.time_ns())
folder.mkdir(parents=True)
root = runpy.run_path(str(project / "tests/test_structure.py"))["manual_library"].__wrapped__(
    folder
)
page = root / "online/shared_v17/coincide.htm"
page.write_text(
    page.read_text("utf-8")
    + "".join(
        f'<p id="long{i}">Select a profile and create a constraint. '
        + "Confirm the selection. " * 20
        + "</p>"
        for i in range(12)
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
    launcher=folder / "missing.exe",
    environment="",
    environment_dir=folder / "none",
    search={"mode": "keyword"},
    translation={
        "provider": "libretranslate",
        "endpoint": "http://fixture.invalid",
        "automatic": False,
    },
    appearance={"remember_window": False, "reading_mode": "original", "show_tools": True},
)


class Provider:
    name = "map-gui-fixture"

    def translate(self, text, target):
        return "创建约束。 " + text


reader.make_provider = lambda _: Provider()
CatiaLauncher.session_available = staticmethod(lambda: False)
app = QApplication([])
app.setFont(QFont("Microsoft YaHei UI", 10))
window = MainWindow(settings)
window.show()
report = {"checks": [], "failures": []}
output = project / "data"
output.mkdir(exist_ok=True)


def check(condition, name):
    report["checks"].append({"name": name, "passed": bool(condition)})
    print(("PASS " if condition else "FAIL ") + name, flush=True)
    if not condition:
        report["failures"].append(name)


def wait(predicate, seconds=25):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(20)
    raise TimeoutError(
        "map acceptance timed out after "
        + str(report["checks"][-1:])
        + "; status="
        + window.statusBar().currentMessage()
    )


def ready_map():
    wait(lambda: window.map_active() and not window.page_loading and not window.jobs)
    QTest.qWait(70)


def ready_reader():
    wait(
        lambda: (
            window.page_ready
            and not window.page_loading
            and not window.jobs
            and window.center_stack.currentWidget() == window.web
            and not window.web.cover.isVisible()
        )
    )


def node(title):
    return next(n for n in window.function_map.snapshot.nodes if n["label_en"] == title)


def js(script):
    result = []
    window.application_script(script, lambda value: result.append(value))
    wait(lambda: bool(result))
    return result[0]


try:
    wait(lambda: not window.jobs and window.history.current)
    window.query.setText("相合约束")
    window.search()
    wait(lambda: not window.jobs and window.results.count())
    hit = window.results.item(0)
    window.activate_result(hit)
    ready_reader()
    js("window.scrollTo(0,350)")
    QTest.qWait(100)
    window.set_reading_mode("bilingual")
    window.map_button.click()
    ready_map()
    canvas = window.function_map
    check(
        window.history.current.path == "@map" and window.map_button.isChecked(),
        "article toolbar opens a map history entry",
    )
    check(
        node("Managing Coincidence Constraint")["id"] == canvas.selected_id(),
        "map focuses the current tutorial",
    )
    check(
        node("Managing Offset Constraint") and node("Managing Constraints"),
        "map exposes authored siblings and parent section",
    )
    check(window.module.currentData()["kind"] == "all", "map does not rewrite global search scope")
    check(
        not window.details_pane.isVisible()
        and not window.reading_controls.isVisible()
        and window.settings.appearance["show_tools"],
        "map hides reader tools without changing preferences",
    )
    QTest.qWait(200)
    check(
        window.map_active() and window.page_task is None,
        "in-flight page translations cannot switch the map back to the reader",
    )
    count = len(window.history.entries)
    canvas.view.zoom_by(1.2)
    canvas.view.horizontalScrollBar().setValue(canvas.view.horizontalScrollBar().value() + 75)
    check(len(window.history.entries) == count, "pan and zoom do not create browsing history")
    group = node("Managing Constraints")["id"]
    positions = deepcopy(canvas.positions)
    canvas.select(group)
    canvas.expand_button.click()
    ready_map()
    check(
        not any(n["label_en"] == "Managing Coincidence Constraint" for n in canvas.snapshot.nodes),
        "collapse hides the descendants",
    )
    canvas.expand_button.click()
    ready_map()
    check(
        node("Managing Coincidence Constraint") and canvas.positions[group] == positions[group],
        "expand restores the branch without moving its parent",
    )
    window.map_drill_node(group)
    ready_map()
    check(
        canvas.snapshot.root == group and len(canvas.snapshot.nodes) == 3,
        "focus branch limits the graph to its local hierarchy",
    )
    window.map_up()
    ready_map()
    check(canvas.snapshot.parent is not None, "up navigates to the authored parent")
    window.map_current()
    ready_map()
    offset = node("Managing Offset Constraint")["id"]
    canvas.select(offset)
    canvas.view.zoom_by(1.15)
    state = canvas.state()
    window.grab().save(str(output / "function-map.png"))
    QTest.keyClick(canvas.view, Qt.Key.Key_Return)
    ready_reader()
    check(
        window.current_document.path.endswith("offset.htm"),
        "Enter opens the selected source article",
    )
    check(
        window.history.current.placement["manual"] == "asmug_C2"
        and window.module.currentData()["kind"] == "all",
        "article retains map context and search scope",
    )
    check(
        window.reading_state.mode == "bilingual" and window.details_pane.isVisible(),
        "reading mode and sidebar return with the article",
    )
    window.go_back()
    ready_map()
    restored = canvas.state()
    check(
        restored["selected"] == offset
        and abs(restored["viewport"]["zoom"] - state["viewport"]["zoom"]) < 0.001
        and abs(restored["viewport"]["x"] - state["viewport"]["x"]) < 3,
        "back restores graph selection, zoom and pan",
    )
    window.go_back()
    ready_reader()
    check(
        window.current_document.path.endswith("coincide.htm") and js("scrollY") > 250,
        "back to the origin restores the reader's position",
    )
    window.go_forward()
    ready_map()
    check(canvas.selected_id() == offset, "forward restores the map workspace")
    window.show_function_map(
        request={
            "scope": {"kind": "manual", "id": "wd1ug_C2"},
            "focus": {"path": "online/shared_v17/coincide.htm", "manual": "wd1ug_C2"},
        }
    )
    ready_map()
    selected = canvas.items[canvas.selected_id()].node
    check(
        selected["placement_context"]["manual"] == "wd1ug_C2",
        "shared article can focus its other manual placement",
    )
    canvas.open_selected()
    ready_reader()
    check(
        window.history.current.placement["manual"] == "wd1ug_C2",
        "map opening preserves the weld placement",
    )
    window.go_back()
    ready_map()
    window.result_on_map(hit)
    ready_map()
    check(
        canvas.items[canvas.selected_id()].node["document_ref"]["path"].endswith("coincide.htm"),
        "search result maps directly to its matching page",
    )
    window.refresh_function_map({"scope": {"kind": "all"}}, reveal=True, keep_view=False)
    ready_map()
    check(
        any(n["kind"] == "category" for n in canvas.snapshot.nodes),
        "all-functions map uses the catalog categories",
    )
    canvas.view.zoom_by(0.8)
    window.save_map_location()
    window.flush_map_state()
    wait(lambda: not window.jobs)
    expected = canvas.state()["viewport"]["zoom"]
    window.close()
    wait(lambda: not window.isVisible() and not window.jobs)
    window = MainWindow(settings)
    window.show()
    wait(lambda: not window.jobs and window.history.current)
    window.map_button.click()
    ready_map()
    canvas = window.function_map
    check(
        abs(canvas.view.transform().m11() - expected) < 0.001,
        "a new window restores the saved library map viewport",
    )
    # Exercise 200 native cards without depending on a browser/GPU renderer.
    snapshot = deepcopy(canvas.snapshot)
    root_node = snapshot.nodes[0]
    snapshot.nodes, snapshot.edges = [root_node], []
    root_node["children"] = 199
    for i in range(199):
        item = dict(
            root_node,
            id="performance-" + str(i),
            label_en="Tutorial " + str(i),
            label_zh="功能 " + str(i),
            children=0,
        )
        snapshot.nodes.append(item)
        snapshot.edges.append({"source": snapshot.root, "target": item["id"]})
    started = time.perf_counter()
    canvas.set_snapshot(snapshot)
    first_ms = (time.perf_counter() - started) * 1000
    samples = []
    for _ in range(40):
        started = time.perf_counter()
        canvas.view.horizontalScrollBar().setValue(canvas.view.horizontalScrollBar().value() + 2)
        canvas.view.viewport().repaint()
        samples.append((time.perf_counter() - started) * 1000)
    report["native_200_nodes"] = {
        "build_ms": round(first_ms, 2),
        "paint_median_ms": round(statistics.median(samples), 2),
        "paint_max_ms": round(max(samples), 2),
    }
    check(len(canvas.items) == 200, "software canvas renders the 200-node limit")
    check(
        not any(name.startswith(("onnxruntime", "tokenizers")) for name in sys.modules),
        "functional maps load no inference runtime",
    )
except Exception as exc:
    report["failures"].append(str(exc))
finally:
    window.save_map_location()
    window.flush_map_state()
    wait(lambda: not window.jobs)
    window.close()
    wait(lambda: not window.isVisible() and not window.jobs)
    app.processEvents()
    report["passed"] = not report["failures"]
    (output / "function-map-gui.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
raise SystemExit(0 if report["passed"] else 1)

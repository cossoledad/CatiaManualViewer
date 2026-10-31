"""Native page relationships, provenance, resource actions and history acceptance."""

import json
import os
import runpy
import sys
import time
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

from catia_manual.config import load_settings  # noqa: E402
from catia_manual.core.index import ManualIndex  # noqa: E402
from catia_manual.core.relations import RELATION_LABELS  # noqa: E402
from catia_manual.core.resources import ResourceService  # noqa: E402
from catia_manual.gui.app import MainWindow  # noqa: E402
from catia_manual.integrations.catia import CatiaLauncher  # noqa: E402

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project))
folder = project / ".cache/relations-gui" / str(time.time_ns())
folder.mkdir(parents=True)
fixture = runpy.run_path(str(project / "tests/test_relations.py"))["relation_library"].__wrapped__(
    folder
)
index = next(fixture)
root, database = index.root, index.database
fixture.close()
path = root / "online/asmug_C2/main.htm"
long_text = "".join(
    f"<p>Paragraph {i}. " + "Create a constraint and confirm. " * 30 + "</p>" for i in range(12)
)
path.write_text(
    path.read_text("utf-8").replace(
        '<p><img src="diagram.png"', long_text + '<p><img src="diagram.png"'
    ),
    "utf-8",
)
with ManualIndex(database, root) as index:
    index.build()
settings = replace(
    load_settings(),
    root=root,
    database=database,
    config_path=folder / "config.toml",
    launcher=folder / "none.exe",
    environment="",
    environment_dir=folder / "none",
    translation={"provider": "glossary", "automatic": False},
    search={"mode": "keyword"},
    appearance={"reading_mode": "original", "remember_window": False, "show_tools": True},
)
CatiaLauncher.session_available = staticmethod(lambda: False)
launched, revealed = [], []
CatiaLauncher.open = lambda self, relative: launched.append(relative) or {"method": "fixture"}
ResourceService.reveal = lambda self, relative: revealed.append(relative)
app = QApplication([])
app.setFont(QFont("Microsoft YaHei UI", 10))
window = MainWindow(settings, "online/asmug_C2/main.htm")
window.show()
report = {"checks": [], "failures": []}
output = project / "data"
output.mkdir(exist_ok=True)


def check(condition, name):
    report["checks"].append({"name": name, "passed": bool(condition)})
    print(("PASS " if condition else "FAIL ") + name, flush=True)
    if not condition:
        report["failures"].append(name)


def wait(predicate, timeout=30):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        app.processEvents()
        if predicate():
            QTest.qWait(60)
            return
        QTest.qWait(20)
    raise TimeoutError("relationships acceptance timed out: " + window.statusBar().currentMessage())


def ready_graph():
    wait(lambda: window.relation_active() and not window.page_loading and not window.jobs)


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


def node(kind, exists=True):
    return next(
        n
        for n in window.relation_graph.snapshot.nodes
        if n["kind"] == kind and n["exists"] == exists
    )


def js(script):
    result = []
    window.application_script(script, lambda value: result.append(value))
    wait(lambda: bool(result))
    return result[0]


try:
    ready_reader()
    window.relation_button.click()
    ready_graph()
    canvas = window.relation_graph
    check(
        window.history.current.path == "@relations"
        and window.relation_button.isChecked()
        and not window.map_button.isChecked(),
        "article toolbar opens the distinct relationship workspace",
    )
    check(
        set(RELATION_LABELS) == {e["kind"] for e in canvas.snapshot.edges},
        "five explicit relation types appear",
    )
    check(
        window.module.currentData()["kind"] == "all" and not window.details_pane.isVisible(),
        "relationship graph preserves search scope and reading preferences",
    )
    reference = next(n for n in canvas.snapshot.nodes if n["role"] == "reference")
    canvas.select(reference["id"])
    canvas.view.zoom_by(1.15)
    canvas.view.horizontalScrollBar().setValue(canvas.view.horizontalScrollBar().value() + 40)
    state = canvas.state()
    before = len(window.history.entries)
    QTest.keyClick(canvas.view, Qt.Key.Key_Return)
    ready_reader()
    check(
        window.current_document.path.endswith("reference.htm")
        and window.history.current.fragment == "details",
        "opening a reference retains its explicit target anchor",
    )
    window.go_back()
    ready_graph()
    check(
        canvas.selected_id() == reference["id"]
        and abs(canvas.view.transform().m11() - state["viewport"]["zoom"]) < 0.001,
        "back restores relationship selection and zoom",
    )
    check(
        abs(canvas.state()["viewport"]["x"] - state["viewport"]["x"]) < 3,
        "back restores relationship pan",
    )
    window.relation_toggle_node(reference["id"])
    ready_graph()
    check(
        any(
            e["source"] == reference["id"]
            and e["target"] == canvas.snapshot.root
            and not e["layout"]
            for e in canvas.snapshot.edges
        ),
        "expansion shows cyclic references with their actual direction",
    )
    command = node("command")
    window.relation_toggle_node(command["id"])
    ready_graph()
    check(
        any(
            e["source"] != canvas.snapshot.root and e["kind"] == "documents_command"
            for e in canvas.snapshot.edges
        ),
        "command expansion shows other pages with verified command anchors",
    )
    check(
        len(window.history.entries) == before + 1,
        "graph expansion and panning do not add history entries",
    )
    image = node("image")
    canvas.select(image["id"])
    canvas.open_selected()
    wait(lambda: bool(window.image_windows))
    preview = next(iter(window.image_windows))
    check(
        window.relation_active() and preview.isVisible(),
        "image opens in an independent preview without leaving the graph",
    )
    preview.close()
    sample = node("sample")
    canvas.select(sample["id"])
    check(
        not canvas.open_button.isEnabled() and canvas.source_button.isEnabled(),
        "document-only mode disables sample launch while keeping provenance",
    )
    captured = []

    def close_menu():
        menu = QApplication.activePopupWidget()
        if isinstance(menu, QMenu):
            captured.extend(a.text() for a in menu.actions())
            next(a for a in menu.actions() if a.text() == "在文件资源管理器中选中").trigger()
            menu.close()

    point = canvas.view.mapFromScene(canvas.items[sample["id"]].scenePos())
    QTimer.singleShot(100, close_menu)
    window.relation_context_menu(point)
    check(
        revealed and "原文出处…" in captured and "在 CATIA 中打开" in captured,
        "sample context menu reuses Explorer and CATIA capability policies",
    )
    launcher = folder / "CATSTART.exe"
    launcher.touch()
    environment = folder / "CATEnv"
    environment.mkdir()
    window.apply_settings(
        replace(
            window.settings, launcher=launcher, environment_dir=environment, environment="fixture"
        )
    )
    canvas.select(sample["id"])
    canvas.open_selected()
    wait(lambda: not window.jobs)
    check(
        len(launched) == 1 and window.relation_active(),
        "explicit sample opening submits one CATIA request",
    )
    missing = node("image", exists=False)
    canvas.select(missing["id"])
    check(
        not canvas.open_button.isEnabled() and canvas.source_button.isEnabled(),
        "missing images keep their source action",
    )
    canvas.select(reference["id"])
    proof = next(
        p
        for e, p in window.relation_proofs(reference["id"])
        if p["source"]["fragment"] == "ref-source"
    )
    window.open_relation_proof(proof)
    ready_reader()
    check(
        window.current_document.path.endswith("main.htm")
        and window.history.current.source["block_id"] == proof["source"]["block_id"],
        "reference provenance jumps to the original source block",
    )
    window.go_back()
    ready_graph()
    canvas.select(image["id"])
    proof = next(
        p
        for e, p in window.relation_proofs(image["id"])
        if p.get("element", {}).get("occurrence") == 1
    )
    window.open_relation_proof(proof)
    ready_reader()
    check(
        js(
            "(()=>{const r=document.querySelectorAll('img[data-cmv-image]')[1]"
            ".getBoundingClientRect();return r.top>=0&&r.top<innerHeight;})()"
        ),
        "image provenance locates the exact repeated image occurrence",
    )
    window.go_back()
    ready_graph()
    check(
        canvas.selected_id() == image["id"],
        "back from image provenance restores the graph selection",
    )
    window.relation_types(["has_image"])
    ready_graph()
    check(
        len(canvas.snapshot.nodes) == 3
        and {e["kind"] for e in canvas.snapshot.edges} == {"has_image"},
        "relation filters update the bounded neighborhood",
    )
    window.relation_types(list(RELATION_LABELS))
    ready_graph()
    canvas.select(reference["id"])
    window.relation_recenter(reference["id"])
    ready_graph()
    check(
        canvas.snapshot.request["path"].endswith("reference.htm"),
        "an article can become the new graph center",
    )
    window.go_back()
    ready_graph()
    check(
        canvas.snapshot.request["path"].endswith("main.htm"),
        "back restores the previous graph center and filters",
    )
    canvas.fit_map()
    window.grab().save(str(output / "relations.png"))
    canvas.view.zoom_by(1.2)
    saved_zoom = canvas.view.transform().m11()
    window.save_relation_location()
    window.flush_relation_state()
    wait(lambda: not window.jobs)
    window.close()
    wait(lambda: not window.isVisible() and not window.jobs)
    window = MainWindow(settings, "online/asmug_C2/main.htm")
    window.show()
    ready_reader()
    window.relation_button.click()
    ready_graph()
    check(
        abs(window.relation_graph.view.transform().m11() - saved_zoom) < 0.001,
        "reopening restores the page-specific graph viewport",
    )
    check(
        not any(n.startswith(("onnxruntime", "tokenizers")) for n in sys.modules),
        "relationship graphs load no model runtime",
    )
except Exception as exc:
    report["failures"].append(str(exc))
finally:
    for preview in list(window.image_windows):
        preview.close()
    window.save_map_location()
    window.flush_map_state()
    wait(lambda: not window.jobs)
    window.close()
    wait(lambda: not window.isVisible() and not window.jobs)
    report["passed"] = not report["failures"]
    (output / "relations-gui.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
raise SystemExit(0 if report["passed"] else 1)

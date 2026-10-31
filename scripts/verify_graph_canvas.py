"""Native canvas gestures, routing and readability; no WebEngine, CATIA or services."""

import json
import statistics
import time
from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QCursor, QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from catia_manual.core.function_map import GraphSnapshot
from catia_manual.core.relations import RELATION_LABELS
from catia_manual.gui.function_map import FunctionMapWidget
from catia_manual.gui.relations import RelationGraphWidget
from catia_manual.gui.style import STYLESHEET

project = Path(__file__).resolve().parents[1]
output = project / "data"
output.mkdir(exist_ok=True)
app = QApplication([])
app.setFont(QFont("Microsoft YaHei UI", 10))
app.setStyleSheet(STYLESHEET)
report = {"checks": [], "failures": []}


def check(condition, name):
    report["checks"].append({"name": name, "passed": bool(condition)})
    if not condition:
        report["failures"].append(name)
    print(("PASS " if condition else "FAIL ") + name, flush=True)


def node(key, title, kind="article", children=0):
    return {
        "id": key,
        "kind": kind,
        "label_en": title,
        "label_zh": "",
        "document_ref": {"library_id": "fixture", "path": key + ".htm"}
        if kind == "article"
        else None,
        "summary": "",
        "children": children,
        "visible_children": children,
        "offset": 0,
        "page_size": children,
        "has_more": False,
        "role": "",
        "source": None,
        "placement_context": None,
        "exists": True,
    }


nodes = [
    node("root", "About Settings", children=5),
    node("locking", "Locking Settings", children=1),
    node("options", "Customizing Settings", children=1),
    node("command", "Settings Command", "command"),
    node("sample", "Settings.CATSettings", "sample"),
    node("image", "Settings Overview", "image"),
]
nodes[0].update(current_label="中心页面", disclosure=False)
edges = []
for target, kind in (
    ("locking", "links_to"),
    ("options", "references"),
    ("command", "documents_command"),
    ("sample", "has_sample"),
    ("image", "has_image"),
):
    edges.append(
        {
            "source": "root",
            "target": target,
            "kind": kind,
            "label": RELATION_LABELS[kind],
            "layout": True,
            "layout_source": "root",
            "layout_target": target,
        }
    )
edges.extend(
    [
        {
            "source": "locking",
            "target": "root",
            "kind": "links_to",
            "label": "引用页面",
            "layout": False,
        },
        {
            "source": "options",
            "target": "locking",
            "kind": "references",
            "label": "参考说明",
            "layout": False,
        },
    ]
)
graph = GraphSnapshot(
    "fixture",
    "revision",
    "root",
    "root",
    nodes,
    edges,
    {"path": "root.htm", "types": list(RELATION_LABELS)},
    ["root", "locking", "options"],
    False,
    view="page-relations",
)
canvas = RelationGraphWidget()
canvas.setWindowTitle("Catia Manual Viewer · 页面关系图")
canvas.resize(1280, 820)
canvas.show()
canvas.set_snapshot(graph, reading={"path": "root.htm"})
QTest.qWait(100)
opened, toggled = [], []
canvas.opened.connect(opened.append)
canvas.toggled.connect(toggled.append)


def drag(start, end, button=Qt.MouseButton.LeftButton):
    QTest.mousePress(canvas.view.viewport(), button, Qt.KeyboardModifier.NoModifier, start)
    for step in range(1, 9):
        QTest.mouseMove(canvas.view.viewport(), start + (end - start) * step / 8, 8)
    QTest.mouseRelease(canvas.view.viewport(), button, Qt.KeyboardModifier.NoModifier, end)
    QTest.qWait(40)


try:
    check(
        not canvas.view.horizontalScrollBar().isVisible()
        and not canvas.view.verticalScrollBar().isVisible(),
        "canvas has no visible scroll bars",
    )
    original = deepcopy(canvas.positions)
    # Far beyond the old content + 300 scene rectangle in every direction.
    for target in (QPointF(-18000, -14000), QPointF(22000, 16000), QPointF(-25000, 25000)):
        canvas.view.centerOn(target)
        before = canvas.view.mapToScene(canvas.view.viewport().rect().center())
        drag(QPoint(420, 300), QPoint(720, 450))
        after = canvas.view.mapToScene(canvas.view.viewport().rect().center())
        check(
            after.x() < before.x() - 250 and after.y() < before.y() - 120,
            "background pan grows beyond prior scene boundaries",
        )
    canvas.view.centerOn(canvas.items["root"])
    point = QPoint(620, 320)
    before = canvas.view.mapToScene(point)
    for _ in range(5):
        canvas.view.zoom_by(1.2, point)
        canvas.view.zoom_by(1 / 1.2, point)
    report["zoom_anchor_drift"] = round(
        (canvas.view.mapToScene(point) - before).manhattanLength(), 4
    )
    check(
        report["zoom_anchor_drift"] < 1,
        "zoom keeps the pointer's scene position stable",
    )
    canvas.view.centerOn(canvas.items["locking"])
    start = canvas.view.mapFromScene(canvas.items["locking"].pos() + QPointF(70, 46))
    drag(start, start + QPoint(95, 75))
    check(
        canvas.positions["locking"][0] > original["locking"][0] + 80
        and canvas.positions["locking"][1] > original["locking"][1] + 60,
        "left drag moves a card freely on both axes",
    )
    check(
        all(canvas.positions[k] == v for k, v in original.items() if k != "locking"),
        "dragging one card keeps other cards stable",
    )
    check(not opened, "card dragging does not activate its source")
    old_position = deepcopy(canvas.positions["locking"])
    canvas.view.centerOn(canvas.items["locking"])
    start = canvas.view.mapFromScene(canvas.items["locking"].pos() + QPointF(70, 46))
    before = canvas.state()["viewport"]
    drag(start, start + QPoint(120, 60), Qt.MouseButton.MiddleButton)
    check(
        canvas.positions["locking"] == old_position
        and canvas.state()["viewport"]["x"] < before["x"] - 100,
        "middle drag pans even when starting over a card",
    )
    state = deepcopy(canvas.state())
    canvas.set_snapshot(graph, state, reading={"path": "root.htm"})
    QTest.qWait(60)
    check(
        canvas.positions == state["positions"]
        and abs(canvas.state()["viewport"]["x"] - state["viewport"]["x"]) < 3,
        "saved free positions and pan restore together",
    )
    canvas.arrange_button.click()
    QTest.qWait(100)
    check(canvas.positions == original, "arrange restores a tidy layout without changing the graph")
    canvas.select("root")
    QTest.keyClick(canvas.view, Qt.Key.Key_Left)
    check(
        canvas.items[canvas.selected_id()].x() < 0,
        "arrow keys navigate the visual left neighborhood",
    )
    canvas.select("locking", center=True)
    point = canvas.view.mapFromScene(canvas.items["locking"].pos() + QPointF(216, 20))
    QTest.mouseClick(
        canvas.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point
    )
    check(
        toggled == ["locking"] and canvas.selected_id() == "locking",
        "disclosure click selects and expands once without dragging",
    )
    point = canvas.view.mapFromScene(canvas.items["locking"].pos() + QPointF(70, 46))
    QTest.mouseDClick(
        canvas.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point
    )
    QTest.mouseRelease(
        canvas.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point
    )
    check(opened == ["locking"], "native double click opens the selected source once")
    canvas.select("locking")
    canvas.fit_map()
    # Wait for native hover delivery after the graph moved under the pointer.
    for _ in range(20):
        QTest.mouseMove(canvas.view.viewport(), QPoint(8, 8))
        QTest.qWait(30)
        if not canvas.hover_node and canvas.hover_edge is None:
            break
    badges = [edge.badge for edge in canvas.edges if not edge.badge.isEmpty()]
    report["label_focus"] = {
        "selected": canvas.selected_id(),
        "hover_node": canvas.hover_node,
        "hover_edge": canvas.hover_edge.edge if canvas.hover_edge else None,
        "cursor": str(canvas.view.viewport().mapFromGlobal(QCursor.pos())),
        "badges": [str(b) for b in badges],
        "buttons": str(app.mouseButtons()),
    }
    cards = [n.sceneBoundingRect() for n in canvas.items.values()]
    check(
        len(badges) >= 2
        and not any(a.intersects(b) for i, a in enumerate(badges) for b in badges[i + 1 :])
        and not any(a.intersects(b) for a in badges for b in cards),
        "reciprocal labels never overlap each other or cards",
    )
    reciprocal = [
        edge
        for edge in canvas.edges
        if {edge.edge["source"], edge.edge["target"]} == {"root", "locking"}
    ]
    check(
        reciprocal[0].points != reciprocal[1].points[::-1],
        "reciprocal relations use separate directed ports",
    )
    canvas.grab().save(str(output / "graph-canvas.png"))
    canvas.select("root")
    check(
        not any(not edge.badge.isEmpty() for edge in canvas.edges),
        "center overview suppresses repeated relationship labels",
    )
    canvas.edge_hovered(canvas.edges[0], True)
    check(not canvas.edges[0].badge.isEmpty(), "hover reveals the individual relationship")
    functional = FunctionMapWidget()
    functional.resize(1280, 820)
    functional.show()
    large = GraphSnapshot(
        "fixture",
        "large",
        "root",
        "root",
        [node("root", "Functions", children=199)]
        + [node(str(i), "Tutorial " + str(i)) for i in range(199)],
        [{"source": "root", "target": str(i)} for i in range(199)],
        {"scope": {"kind": "all"}},
        ["root"],
        False,
    )
    started = time.perf_counter()
    functional.set_snapshot(large)
    report["build_200_ms"] = round((time.perf_counter() - started) * 1000, 2)
    QTest.qWait(80)
    samples = []
    for _ in range(30):
        started = time.perf_counter()
        functional.view.viewport().repaint()
        samples.append((time.perf_counter() - started) * 1000)
    report["paint_200_median_ms"] = round(statistics.median(samples), 2)
    check(
        len(functional.items) == 200 and len(functional.edges) == 199,
        "native canvas retains the 200-card capacity",
    )
    functional.close()
except Exception as exc:
    report["failures"].append(repr(exc))
    raise
finally:
    canvas.close()
    report["passed"] = not report["failures"]
    (output / "graph-canvas.json").write_text(json.dumps(report, indent=2), "utf-8")
raise SystemExit(0 if report["passed"] else 1)

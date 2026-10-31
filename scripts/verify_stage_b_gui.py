"""Local end-to-end acceptance for source search and verified library migration."""

import json
import os
import shutil
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
from catia_manual.core.library_contract import SourceLocation  # noqa: E402
from catia_manual.core.parser import ManualParser  # noqa: E402
from catia_manual.core.source import source_for_block  # noqa: E402
from catia_manual.gui.app import MainWindow  # noqa: E402
from catia_manual.gui.library import LibraryDialog, TermsDialog  # noqa: E402
from catia_manual.gui.settings import GlobalSettings  # noqa: E402

project = Path(__file__).resolve().parents[1]
folder = project / ".cache/stage-b" / str(time.time_ns())
root = folder / "manual"
module = root / "online/prtug_C2"
module.mkdir(parents=True)
(root / "online/CATIA_P3_TOC.xml").write_text(
    '<Root><ITEM name="CATIA"><ITEM name="Mechanical Design">'
    '<DITEM dir="prtug_C2" name="Part Design"/></ITEM></ITEM></Root>',
    "utf-8",
)
(module / "prtugpr01.htm").write_text("<h1>Part Design</h1><p>General introduction.</p>", "utf-8")
intro = "".join(
    f'<p id="p{i}">Step {i}: Select a profile. ' + "Specify a length and confirm. " * 12 + "</p>"
    for i in range(12)
)
(module / "page.htm").write_text(
    "<h1>Creating Pads</h1>" + intro + '<h2 id="limit">Surface limits</h2>'
    '<p id="target">Specify a limiting surface and thickness for the pad.</p>'
    + intro.replace('id="p', 'id="q'),
    "utf-8",
)
(module / "other.htm").write_text("<h1>Pocket</h1><p>Cut a pocket.</p>", "utf-8")
database = folder / "data/index.sqlite3"
with ManualIndex(database, root) as index:
    index.build()
    identity = index.library_id
output = project / "data"
output.mkdir(exist_ok=True)
settings = replace(
    load_settings(),
    root=root,
    database=database,
    config_path=folder / "config.toml",
    translation={"provider": "glossary", "automatic": False},
    search={"mode": "keyword"},
    appearance={"remember_window": False, "reading_mode": "original", "show_tools": True},
)
app = QApplication([])
app.setFont(QFont("Microsoft YaHei UI", 10))
window = MainWindow(settings, "online/prtug_C2/page.htm")
window.show()
dialogs = []
report = {"checks": [], "failures": []}


def check(condition, name):
    report["checks"].append({"name": name, "passed": bool(condition)})
    if not condition:
        report["failures"].append(name)


def wait(predicate, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(20)
    raise TimeoutError("B round GUI acceptance timed out")


def ready():
    wait(
        lambda: (
            window.page_ready
            and not window.page_loading
            and window.center_stack.currentWidget() == window.web
            and not window.web.cover.isVisible()
        )
    )
    QTest.qWait(80)


def js(code):
    value = []
    window.application_script(code, lambda result: value.append(result))
    wait(lambda: bool(value))
    return value[0]


try:
    ready()
    check(window.library_id == identity, "reader and index share one persistent library identity")
    window.set_scope("manual", "prtug_C2")
    window.query.setText("终止面")
    window.search()
    wait(lambda: not window.jobs)
    item = window.results.item(0)
    source = SourceLocation.from_dict(item.data(Qt.ItemDataRole.UserRole + 1))
    check(
        source.fragment in {"limit", "target"} and source.heading_path[-1] == "Surface limits",
        "Chinese search returns a precise chapter source",
    )
    window.activate_result(item)
    ready()
    top = js(
        "document.querySelector('[data-cmv-source-block=\""
        + source.block_id
        + "\"]').closest('.cmv-unit').getBoundingClientRect().top"
    )
    check(abs(top) < 8, "activating a search result scrolls to the matching block")
    js("window.scrollBy(0,110)")
    QTest.qWait(250)
    window.capture_reading_position()
    QTest.qWait(100)
    window.flush_reading_locations()
    wait(lambda: not window.jobs)
    with ManualIndex(database, root) as index:
        saved = index.load_location("online/prtug_C2/page.htm")
    check(
        saved is not None and saved.document.library_id == identity,
        "reading position is saved as a versioned source reference",
    )

    target = folder / "copied-manual"
    shutil.copytree(root, target)
    dialog = LibraryDialog(window)
    dialogs.append(dialog)
    dialog.show()
    wait(lambda: not dialog.busy)
    check(
        identity in dialog.summary.toolTip(), "library management retains the registered identity"
    )
    dialog.root.setText(str(target))
    dialog.verify_index(database, target)
    wait(lambda: not dialog.busy)
    check(
        dialog.verification["matched"] and dialog.attach_button.isEnabled(),
        "GUI accepts a complete matching copy",
    )
    dialog.grab().save(str(output / "stage-b-library.png"))
    dialog.attach()
    wait(lambda: not dialog.busy and window.settings.root == target)
    moved_database = window.settings.database
    check(
        moved_database != database and moved_database.is_file(),
        "GUI attaches a new snapshot instead of rebinding the original",
    )
    with ManualIndex(database, root) as index:
        check(index.meta("root") == str(root), "original library keeps its original mount")
    dialog.reject()
    window.open_document("online/prtug_C2/page.htm")
    ready()
    position = json.loads(js("JSON.stringify(window.cmvReader.capture())"))
    check(window.library_id == identity, "attached library retains its UUID")
    check(
        position["source_block"] == saved.block_id
        and abs(position["offset"] - saved.offset_css_px) < 8,
        "attached copy restores the source-relative reading position",
    )
    settings_dialog = GlobalSettings(window.settings, window)
    check(
        Path(settings_dialog.changes()["documentation"]["database"]) == moved_database,
        "unrelated settings preserve a custom attached index filename",
    )
    settings_dialog.reject()
    settings_dialog.deleteLater()

    terms = TermsDialog(window)
    dialogs.append(terms)
    terms.show()
    wait(lambda: not terms.busy)
    terms.alias.setText("小凸起")
    terms.english.setText("pad")
    terms.scope.setCurrentIndex(terms.scope.findData("prtug_C2"))
    terms.store()
    wait(lambda: not terms.busy)
    with ManualIndex(window.settings.database, window.settings.root) as index:
        check(
            index.aliases(["prtug_C2"]).get("小凸起") == ("pad",)
            and "小凸起" not in index.aliases(),
            "term editor persists the explicit manual scope",
        )
    terms.reject()
    window.set_scope("manual", "prtug_C2")
    window.query.setText("小凸起")
    window.search()
    wait(lambda: not window.jobs)
    check(
        window.results.count() > 0 and "Creating Pads" in window.results.item(0).text(),
        "new scoped alias searches immediately without rebuilding",
    )
    window.grab().save(str(output / "stage-b-reader.png"))

    parser = ManualParser(target)
    relative = "online/prtug_C2/repeated.htm"
    path = target / relative
    path.write_text("<h1>Repeated</h1><p>Repeated selection.</p>", "utf-8")
    document = parser.parse(relative)
    ambiguous = source_for_block(identity, document, document.blocks[-1])
    ambiguous = replace(ambiguous, heading_path=(), text_prefix="", text_suffix="")
    path.write_text(
        "<h1>Repeated</h1><p>Repeated selection.</p><p>Repeated selection.</p>", "utf-8"
    )
    history_count = len(window.history.entries)
    old_document = window.current_document.path
    window.open_source(ambiguous.to_dict())
    ready()
    check(
        window.current_document.path == old_document
        and len(window.history.entries) == history_count,
        "ambiguous revised source does not navigate to an arbitrary occurrence",
    )
    check(
        "无法唯一定位" in window.statusBar().currentMessage(),
        "ambiguous source reports a clear recoverable message",
    )
except Exception as exc:
    report["failures"].append(str(exc))
finally:
    for dialog in dialogs:
        dialog.reject()
    window.page_pump.stop()
    window.position_timer.stop()
    if window.page_task:
        window.page_task.paused = True
    window.flush_reading_locations()
    wait(lambda: not window.jobs)
    window.close()
    app.processEvents()
    report["passed"] = not report["failures"]
    (output / "stage-b-gui.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))

raise SystemExit(0 if report["passed"] else 1)

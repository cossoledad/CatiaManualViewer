"""Native UI acceptance: focus, geometry, sidebar recovery and original-file actions."""

import faulthandler
import json
import os
import runpy
import sys
import time
import tomllib
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
faulthandler.dump_traceback_later(45)

from PySide6.QtCore import QEvent, QObject, QPoint, QTimer  # noqa: E402
from PySide6.QtGui import QFont, QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu, QStyleOptionViewItem  # noqa: E402

import catia_manual.gui.page_reader as reader  # noqa: E402
import catia_manual.gui.resource_actions as resources  # noqa: E402
from catia_manual import __version__  # noqa: E402
from catia_manual.config import load_settings  # noqa: E402
from catia_manual.core.index import ManualIndex  # noqa: E402
from catia_manual.gui.about import AboutDialog  # noqa: E402
from catia_manual.gui.app import MainWindow  # noqa: E402
from catia_manual.gui.search_focus import TERMS_ROLE  # noqa: E402
from catia_manual.integrations.catia import CatiaLauncher  # noqa: E402

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project))
folder = project / ".cache/ui-polish" / str(time.time_ns())
folder.mkdir(parents=True)
root = runpy.run_path(str(project / "tests/test_structure.py"))["manual_library"].__wrapped__(
    folder / "manual 中文"
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
    appearance={"remember_window": False, "show_tools": False, "sidebar_sizes": [275, 925, 0]},
)


class Provider:
    name = "ui-fixture"

    def translate(self, text, target):
        return "相合约束 " + text


reader.make_provider = lambda _: Provider()
CatiaLauncher.session_available = staticmethod(lambda: False)
opened = []
resources.QDesktopServices.openUrl = staticmethod(lambda url: opened.append(url) or True)
app = QApplication([])
app.setFont(QFont("Microsoft YaHei UI", 10))
window = MainWindow(settings)
window.show()
report = {"checks": [], "failures": []}
output = project / "data"
output.mkdir(exist_ok=True)


def check(condition, name):
    print(("PASS " if condition else "FAIL ") + name, flush=True)
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
    raise TimeoutError("UI acceptance timed out after " + str(report["checks"][-1:]))


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


def js(script):
    result = []
    window.application_script(script, lambda value: result.append(value))
    wait(lambda: bool(result))
    return result[0]


class GeometryEvents(QObject):
    def __init__(self):
        super().__init__()
        self.sizes = []

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            self.sizes.append((event.size().width(), event.size().height()))
        return False


try:
    wait(lambda: not window.jobs and window.history.current)
    check(not window.details_pane.isVisible(), "saved hidden sidebar is hidden from startup")
    window.tools_action.trigger()
    QTest.qWait(100)
    check(
        window.details_pane.isVisible() and window.split.sizes()[2] >= 235,
        "show restores sidebar even when saved width was zero",
    )
    window.toggle_tools(False)
    window.tools_button.click()
    QTest.qWait(50)
    check(
        window.tools_action.isChecked() and window.details_pane.width() >= 235,
        "toolbar and menu sidebar state remain synchronized",
    )
    window.showMaximized()
    QTest.qWait(150)
    geometry = GeometryEvents()
    window.installEventFilter(geometry)
    initial_size = (window.width(), window.height())
    window.open_manual("asmug_C2")
    ready()
    check(
        window.isMaximized() and all(size == initial_size for size in geometry.sizes),
        "first manual entry preserves top-level size and maximized state",
    )
    window.activate_request({})
    check(window.isMaximized(), "activation preserves an already maximized window")
    window.query.setText("相合约束")
    window.search()
    wait(lambda: not window.jobs and window.results.count())
    hit = window.results.item(0)
    terms = hit.data(TERMS_ROLE)
    check(
        "相合约束" in terms and "coincidence" in terms,
        "Chinese search carries both original and expanded terms",
    )
    delegate = window.results.itemDelegate()
    option = QStyleOptionViewItem()
    option.font = window.results.font()
    doc = delegate.document(option, window.results.indexFromItem(hit))
    check("#ffe7a0" in doc.toHtml(), "result title and snippet paint matching text")
    window.grab().save(str(output / "ui-polish-results.png"))
    window.activate_result(hit)
    ready()
    before = js("document.body.innerText")
    check(
        js("CSS.highlights.get('cmv-search').size") > 0,
        "reader paints English matches from a Chinese query",
    )
    source_ids = js(
        "JSON.stringify([...document.querySelectorAll('[data-cmv-source-block]')]"
        ".map(n=>n.dataset.cmvSourceBlock))"
    )
    window.focus_action.trigger()
    wait(lambda: js("!CSS.highlights.has('cmv-search')"))
    check(before == js("document.body.innerText"), "focus toggle does not mutate source text")
    window.focus_action.trigger()
    wait(lambda: js("CSS.highlights.has('cmv-search')"))
    window.set_reading_mode("bilingual")
    wait(lambda: bool(window.page_task.results) and not window.page_worker_running)
    check(
        source_ids
        == js(
            "JSON.stringify([...document.querySelectorAll('[data-cmv-source-block]')]"
            ".map(n=>n.dataset.cmvSourceBlock))"
        ),
        "progressive translation and emphasis preserve source markers",
    )
    check(
        js("CSS.highlights.get('cmv-search').size") > 0,
        "matches remain highlighted after bilingual updates",
    )
    window.open_document("online/shared_v17/offset.htm")
    ready()
    check(js("!CSS.highlights.has('cmv-search')"), "ordinary navigation clears old query focus")
    window.go_back()
    ready()
    check(
        js("CSS.highlights.get('cmv-search').size") > 0,
        "back restores search emphasis with its reading location",
    )
    menu = QMenu(window)
    action = window.add_original_action(
        menu, "online/shared_v17/coincide.htm", "CATAssembly.CreateCoincid"
    )
    action.trigger()
    check(
        opened
        and "manual 中文" in opened[-1].toLocalFile()
        and opened[-1].fragment() == "CATAssembly.CreateCoincid"
        and "rendered" not in opened[-1].toLocalFile(),
        "original browser action uses source file with Unicode, spaces and anchor",
    )
    check(
        not window.add_original_action(menu, "online/missing.htm").isEnabled(),
        "missing original file action is disabled",
    )
    captured = []

    def capture_menu():
        popup = QApplication.activePopupWidget()
        if isinstance(popup, QMenu):
            captured.append([a.text() for a in popup.actions()])
            popup.close()

    QTimer.singleShot(80, capture_menu)
    window.context_menu(QPoint(10, 10))
    QTimer.singleShot(80, capture_menu)
    window.result_context_menu(window.results.visualItemRect(hit).center())
    QTimer.singleShot(80, capture_menu)
    window.tree_context_menu(window.tree.visualItemRect(window.tree.currentItem()).center())
    check(
        len(captured) == 3 and all("在浏览器中打开原始页面" in texts for texts in captured),
        "reader, search results and navigation expose original-browser actions",
    )
    window.breadcrumb.setText('<a href="0">' + "Very long functional breadcrumb " * 40 + "</a>")
    window.showNormal()
    window.resize(1060, 680)
    QTest.qWait(150)
    check(
        window.width() == 1060
        and window.details_pane.width() >= 235
        and window.split.widget(1).width() >= 320,
        "long breadcrumb fits minimum window without covering the sidebar",
    )
    icon_rect = window.query.search_icon.geometry()
    check(
        abs(icon_rect.center().y() - window.query.rect().center().y()) <= 1
        and icon_rect.right() < window.query.textMargins().left() + 12
        and abs(
            window.query.clear_button.geometry().center().y() - window.query.rect().center().y()
        )
        <= 1,
        "search icon uses a centered, reserved gutter",
    )
    dialog = AboutDialog(window)
    dialog.show()
    QTest.qWait(100)
    version = tomllib.loads((project / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    check(__version__ == version and dialog.isVisible(), "About page shows the release version")
    dialog.grab().save(str(output / "ui-polish-about.png"))
    dialog.close()
    image = QImage(str(project / "src/catia_manual/assets/app.png"))
    check(
        image.pixelColor(0, 0).alpha() == 0,
        "application icon has a transparent background",
    )
    window.grab().save(str(output / "ui-polish-compact.png"))
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
    faulthandler.cancel_dump_traceback_later()
    (output / "ui-polish.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
raise SystemExit(0 if report["passed"] else 1)

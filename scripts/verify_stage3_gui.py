"""Live regression for functional catalog, portable settings and complete mouse side clicks."""

import json
import os
import time
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtCore import Qt, QThreadPool, QTimer  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from catia_manual.config import load_settings, save_settings  # noqa: E402
from catia_manual.gui.app import MainWindow  # noqa: E402
from catia_manual.gui.settings import GlobalSettings  # noqa: E402
from catia_manual.gui.style import APP_NAME  # noqa: E402
from catia_manual.gui.workspace import CatalogCard  # noqa: E402

project = Path(__file__).resolve().parents[1]
qa = project / ".cache/stage3"
qa.mkdir(parents=True, exist_ok=True)
original = replace(load_settings(), config_path=qa / "config.local.toml")
original = save_settings(original, {})
app = QApplication([])
app.setFont(QFont("Microsoft YaHei UI", 10))
window = MainWindow(original)
window.show()
report = {"checks": [], "failures": []}
PAD = "online/prtug_C2/prtugbt0501.htm"
POCKET = "online/prtug_C2/prtugbt0502.htm"
back_url = ""
forward_url = ""


def check(condition, name):
    report["checks"].append({"name": name, "passed": bool(condition)})
    if not condition:
        report["failures"].append(name)


def wait_for(predicate, then, timeout=15):
    deadline = time.monotonic() + timeout

    def poll():
        try:
            if predicate():
                QTimer.singleShot(180, then)
            elif time.monotonic() > deadline:
                report["failures"].append("Timeout: " + then.__name__)
                finish()
            else:
                QTimer.singleShot(100, poll)
        except Exception as exc:
            report["failures"].append(str(exc))
            finish()

    poll()


def page(path):
    return (
        window.current_document and window.current_document.path == path and not window.page_loading
    )


def home():
    check(
        window.windowTitle() == APP_NAME and not window.windowIcon().isNull(),
        "application name and icon",
    )
    check(
        len(window.catalog.categories) == 7 and len(window.catalog.manuals) == 130,
        "official functional catalog",
    )
    check(window.tree.topLevelItem(1).text(0) == "机械设计", "human-readable category navigation")
    window.grab().save(str(project / "data/stage3-home.png"))
    cards = window.home_content.findChildren(CatalogCard)
    QTest.mouseClick(cards[1], Qt.MouseButton.LeftButton)
    wait_for(lambda: window.history.current.path == "@category:mechanical-design", search_category)


def search_category():
    check(
        "prtug_C2" in window.scope_modules() and "estug_C2" not in window.scope_modules(),
        "category selection sets search scope",
    )
    window.grab().save(str(project / "data/stage3-category.png"))
    window.query.setText("圆角")
    window.search()
    wait_for(lambda: not window.jobs and window.results.count() > 0, scoped_results)


def scoped_results():
    allowed = set(window.catalog.category("mechanical-design").modules)
    modules = {
        window.results.item(index).data(Qt.ItemDataRole.UserRole).split("/")[1]
        for index in range(window.results.count())
    }
    check(bool(modules) and modules <= allowed, "every result belongs to selected category")
    window.open_manual("prtug_C2")
    wait_for(lambda: page(window.catalog.manuals["prtug_C2"].entry), manual_opened)


def manual_opened():
    check(
        window.module.currentText().strip() == "零件设计",
        "named manual opens from functional catalog",
    )
    window.open_document(PAD)
    wait_for(lambda: page(PAD), open_next)


def open_next():
    window.open_document(POCKET)
    wait_for(lambda: page(POCKET), press_back)


def press_back():
    window.activateWindow()
    window.web.setFocus()
    QTest.mousePress(window.web.focusProxy(), Qt.MouseButton.BackButton)
    wait_for(lambda: page(PAD), release_back)


def release_back():
    global back_url
    back_url = window.web.url().toString()
    QTest.mouseRelease(window.web.focusProxy(), Qt.MouseButton.BackButton)
    QTimer.singleShot(400, back_stable)


def back_stable():
    check(
        page(PAD) and window.web.url().toString() == back_url,
        "back release cannot bounce to current page",
    )
    QTest.mousePress(window.web.focusProxy(), Qt.MouseButton.ForwardButton)
    wait_for(lambda: page(POCKET), release_forward)


def release_forward():
    global forward_url
    forward_url = window.web.url().toString()
    QTest.mouseRelease(window.web.focusProxy(), Qt.MouseButton.ForwardButton)
    QTimer.singleShot(400, forward_stable)


def forward_stable():
    check(
        page(POCKET) and window.web.url().toString() == forward_url,
        "forward release cannot trigger a second navigation",
    )
    window.grab().save(str(project / "data/stage3-reader.png"))
    dialog = GlobalSettings(window.settings, window)
    dialog.tabs.setCurrentIndex(1)
    editor = dialog.translation
    for provider in ("mymemory", "deepl", "libretranslate"):
        editor.provider.setCurrentIndex(editor.provider.findData(provider))
        check(
            editor.endpoint.text().startswith("http") and not editor.model.isVisible(),
            provider + " default preset",
        )
    editor.provider.setCurrentIndex(editor.provider.findData("chat-completions"))
    editor.endpoint.setText("https://example.invalid/v1")
    editor.model.setText("private-model")
    editor.provider.setCurrentIndex(editor.provider.findData("mymemory"))
    editor.provider.setCurrentIndex(editor.provider.findData("chat-completions"))
    check(
        editor.endpoint.text() == "https://example.invalid/v1"
        and editor.model.text() == "private-model",
        "provider profiles retain independent configuration",
    )
    editor.provider.setCurrentIndex(editor.provider.findData("local"))
    dialog.font_size.setValue(11)
    dialog.zoom.setValue(110)
    updated = save_settings(window.settings, dialog.changes())
    window.apply_settings(updated)
    check(abs(window.web.zoomFactor() - 1.1) < 0.01, "appearance applies without restart")
    dialog.tabs.setCurrentIndex(0)
    dialog.show()
    window._qa_dialog = dialog
    QTimer.singleShot(300, migrate)


def migrate():
    window._qa_dialog.grab().save(str(project / "data/stage3-global-settings.png"))
    window._qa_dialog.reject()
    root = qa / "copied-docs" / str(time.monotonic_ns())
    folder = root / "online/prtug_C2"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "prtugpr01.htm").write_text(
        "<h1>Copied Library</h1><p>Copied manual tutorial.</p>", encoding="utf-8"
    )
    (root / "online/CATIA_P3_TOC.xml").write_text(
        '<Root><ITEM name="CATIA"><ITEM name="Mechanical Design">'
        '<DITEM dir="prtug_C2" name="Part Design"/></ITEM></ITEM></Root>'
    )
    moved = save_settings(window.settings, {"documentation": {"root": str(root)}})
    check(
        moved.database != original.database,
        "a different documentation root selects an independent index",
    )
    window.apply_settings(moved)
    check(
        len(window.catalog.manuals) == 1 and window.history.current.path == "@home",
        "library switch resets catalog and history",
    )
    window.open_manual("prtug_C2")
    wait_for(lambda: page("online/prtug_C2/prtugpr01.htm"), copied_read)


def copied_read():
    check(
        window.current_document.title == "Copied Library",
        "copied documentation is readable without CATIA",
    )
    window.query.setText("copied")
    window.search()
    wait_for(lambda: not window.jobs and window.results.count() > 0, create_index)


def create_index():
    if window.results.item(0).data(Qt.ItemDataRole.UserRole) == "@index":
        window.activate_result(window.results.item(0))
    wait_for(
        lambda: (
            not window.jobs
            and window.results.count() > 0
            and "Copied Library" in window.results.item(0).text()
        ),
        migrated_search,
    )


def migrated_search():
    check(True, "new library index can be built and searched")
    window.apply_settings(original)
    check(len(window.catalog.manuals) == 130, "switching back preserves the original library")
    QTimer.singleShot(250, finish)


def finish():
    if window.jobs:
        QTimer.singleShot(100, finish)
        return
    report["passed"] = not report["failures"]
    (project / "data/stage3-gui-verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False), flush=True)
    window.close()
    app.exit(0 if report["passed"] else 1)


app.aboutToQuit.connect(lambda: QThreadPool.globalInstance().waitForDone(40000))
wait_for(lambda: window.history.current and window.history.current.path == "@home", home)
raise SystemExit(app.exec())

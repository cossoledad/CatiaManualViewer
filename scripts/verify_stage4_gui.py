"""Native UI checks for independent buttons, menus and persistent translation status."""

import json
import os
import time
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtCore import Qt, QThreadPool, QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from catia_manual.config import load_settings  # noqa: E402
from catia_manual.gui.app import MainWindow  # noqa: E402
from catia_manual.gui.settings import GlobalSettings  # noqa: E402

project = Path(__file__).resolve().parents[1]
app = QApplication([])
settings = load_settings()
settings = replace(
    settings,
    config_path=project / ".cache/stage4-qa.toml",
    translation={**settings.translation, "provider": "local", "automatic": True},
)
window = MainWindow(settings, "online/prtug_C2/prtugbt0501.htm")
window.show()
report = {"checks": [], "failures": []}
PAD = "online/prtug_C2/prtugbt0501.htm"
POCKET = "online/prtug_C2/prtugbt0502.htm"


def check(condition, name):
    report["checks"].append({"name": name, "passed": bool(condition)})
    if not condition:
        report["failures"].append(name)


def wait_for(predicate, next_step):
    deadline = time.monotonic() + 15

    def poll():
        if predicate():
            QTimer.singleShot(100, next_step)
        elif time.monotonic() > deadline:
            report["failures"].append("timeout before " + next_step.__name__)
            finish()
        else:
            QTimer.singleShot(100, poll)

    poll()


def page(path):
    return (
        window.current_document and window.current_document.path == path and not window.page_loading
    )


def begin():
    check(
        all(button.menu() is None for button in window.navigation_buttons),
        "navigation buttons have no overlapping menu arrow",
    )
    check(
        window.settings_button.menu() is None and window.more_button.menu() is None,
        "settings and maintenance use independent hit targets",
    )
    window.open_document(POCKET)
    wait_for(lambda: page(POCKET), click_back)


def click_back():
    QTest.mouseClick(window.navigation_buttons[0], Qt.MouseButton.LeftButton)
    wait_for(lambda: page(PAD), click_forward)


def click_forward():
    check(True, "back button navigates once")
    QTest.mouseClick(window.navigation_buttons[1], Qt.MouseButton.LeftButton)
    wait_for(lambda: page(POCKET), history_menu)


def history_menu():
    check(True, "forward button navigates once")
    QTest.mouseClick(window.history_button, Qt.MouseButton.LeftButton)
    check(QApplication.activePopupWidget() is window.history_menu, "dedicated history menu opens")
    check(
        window.back_menu.actions() and not window.forward_menu.menuAction().isEnabled(),
        "history menu reflects available directions",
    )
    window.grab().save(str(project / "data/stage4-history.png"))
    window.history_menu.close()
    QTimer.singleShot(100, more_menu)


def more_menu():
    QTest.mouseClick(window.more_button, Qt.MouseButton.LeftButton)
    popup = QApplication.activePopupWidget()
    check(
        popup is not None and any(a.text() == "更新文档索引" for a in popup.actions()),
        "maintenance menu opens separately",
    )
    if popup:
        popup.close()
    QTimer.singleShot(100, settings_click)


def settings_click():
    def inspect_dialog():
        dialogs = [
            item
            for item in app.topLevelWidgets()
            if isinstance(item, GlobalSettings) and item.isVisible()
        ]
        check(bool(dialogs), "settings button opens the dialog directly")
        for dialog in dialogs:
            dialog.reject()

    QTimer.singleShot(250, inspect_dialog)
    QTest.mouseClick(window.settings_button, Qt.MouseButton.LeftButton)
    window.update_translation_status(
        "翻译中…",
        {
            "provider": "chat-completions",
            "model": "my-translation-model",
            "endpoint": "https://example.invalid/v1",
        },
    )
    window.statusBar().showMessage("正在加载文档…")
    check(
        "my-translation-model" in window.translation_status.text()
        and window.translation_status.isVisible(),
        "provider status persists beside task messages",
    )
    window.statusBar().clearMessage()
    window.update_translation_status()
    window.activateWindow()
    window.web.setFocus()
    window.application_script("""(()=>{const node=document.querySelector('p');
      const range=document.createRange();range.selectNodeContents(node);
      const selection=window.getSelection();selection.removeAllRanges();
      selection.addRange(range);})()""")
    wait_for(
        lambda: (
            "已完成" in window.translation_status.text()
            or "本地缓存" in window.translation_status.text()
        ),
        translated,
    )


def translated():
    check(
        "本机离线模型" not in window.translation.toPlainText()
        and "本机离线模型" in window.translation_status.text(),
        "translation has no provider footer",
    )
    window.grab().save(str(project / "data/stage4-reader.png"))
    finish()


def finish():
    report["passed"] = not report["failures"]
    (project / "data/stage4-gui.json").write_text(json.dumps(report, indent=2), "utf-8")
    print(json.dumps(report))
    QThreadPool.globalInstance().waitForDone()
    window.close()
    app.exit(0 if report["passed"] else 1)


QTimer.singleShot(90000, lambda: (report["failures"].append("watchdog"), finish()))
wait_for(lambda: page(PAD), begin)
raise SystemExit(app.exec())

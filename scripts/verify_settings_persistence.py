"""Native settings acceptance with synthetic keys; no network, model or CATIA calls."""

import json
import os
import time
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtGui import QFont, QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

import catia_manual.core.translation as translation  # noqa: E402
from catia_manual.config import load_settings, save_settings  # noqa: E402
from catia_manual.core.credentials import resolve_api_key  # noqa: E402
from catia_manual.gui.about import AboutDialog  # noqa: E402
from catia_manual.gui.app import MainWindow  # noqa: E402
from catia_manual.gui.library import LibraryDialog  # noqa: E402
from catia_manual.gui.semantic import SemanticDialog  # noqa: E402
from catia_manual.gui.settings import GlobalSettings  # noqa: E402
from catia_manual.integrations.catia import CatiaLauncher  # noqa: E402

project = Path(__file__).resolve().parents[1]
folder = project / ".cache/settings-persistence" / str(time.time_ns())
(folder / "manual/online").mkdir(parents=True)
path = folder / "config.local.toml"
path.write_text("", "utf-8")
settings = save_settings(
    load_settings(path),
    {
        "documentation": {
            "root": str(folder / "manual"),
            "database": str(folder / "data/index.sqlite3"),
        },
        "translation": {
            "provider": "libretranslate",
            "endpoint": "http://fixture.invalid",
            "automatic": False,
        },
        "appearance": {"remember_window": False},
    },
)
settings = replace(settings, launcher=folder / "missing.exe", environment="")
CatiaLauncher.session_available = staticmethod(lambda: False)
app = QApplication([])
app.setFont(QFont("Microsoft YaHei UI", 10))
window = MainWindow(settings)
window.show()
dialogs = []
report = {"checks": [], "failures": []}
output = project / "data"
output.mkdir(exist_ok=True)


def check(condition, name):
    report["checks"].append({"name": name, "passed": bool(condition)})
    print(("PASS " if condition else "FAIL ") + name, flush=True)
    if not condition:
        report["failures"].append(name)


def wait(predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(20)
    raise TimeoutError("settings acceptance timed out")


def choose(form, name):
    form.provider.setCurrentIndex(form.provider.findData(name))


def save(dialog):
    dialog.accept()
    if dialog.result() != dialog.DialogCode.Accepted:
        raise ValueError(dialog.note.text())
    updated = save_settings(window.settings, dialog.changes())
    window.apply_settings(updated)
    wait(lambda: not window.jobs)
    return load_settings(path)


try:
    wait(lambda: not window.jobs and window.history.current)
    dialog = GlobalSettings(window.settings, window)
    dialogs.append(dialog)
    dialog.show()
    form = dialog.translation
    form.key_env.setText("SHARED_GUI_TEST")
    form.key.setText("fixture-libre-key")
    choose(form, "deepl")
    form.key_env.setText("SHARED_GUI_TEST")
    form.key.setText("fixture-deepl-key")
    choose(form, "chat-completions")
    form.key_env.setText("SHARED_GUI_TEST")
    form.endpoint.setText("https://fixture.invalid/v1")
    form.model.setText("fixture-model")
    form.key.setText("fixture-llm-key")
    choose(form, "libretranslate")
    check(form.key.text() == "fixture-libre-key", "switching services keeps separate draft keys")
    form.delay.setValue(600)
    form.max_chars.setValue(4200)
    dialog.font_size.setValue(11)
    dialog.zoom.setValue(120)
    dialog.reading_mode.setCurrentIndex(dialog.reading_mode.findData("bilingual"))
    dialog.search_threads.setValue(3)
    dialog.search_mode.setCurrentIndex(dialog.search_mode.findData("hybrid"))
    dialog.search_model_dir.setText(str(folder / "search-model"))
    dialog.tools.setChecked(False)
    dialog.remember_window.setChecked(True)
    restored = save(dialog)
    text = path.read_text("utf-8")
    check(
        all(
            key not in text for key in ("fixture-libre-key", "fixture-deepl-key", "fixture-llm-key")
        ),
        "saved configuration contains encrypted values only",
    )
    check(
        resolve_api_key(restored.translation) == "fixture-libre-key",
        "reloaded active provider resolves the saved key",
    )
    reopened = GlobalSettings(restored, window)
    dialogs.append(reopened)
    reopened.show()
    form = reopened.translation
    check(form.key.text() == "fixture-libre-key", "reopening settings restores the active key")
    choose(form, "deepl")
    check(form.key.text() == "fixture-deepl-key", "reopening restores the other service's key")
    choose(form, "chat-completions")
    check(
        form.key.text() == "fixture-llm-key"
        and form.model.text() == "fixture-model"
        and form.endpoint.text() == "https://fixture.invalid/v1",
        "LLM key, model and endpoint restore together",
    )
    check(
        reopened.zoom.value() == 120
        and reopened.font_size.value() == 11
        and reopened.reading_mode.currentData() == "bilingual"
        and not reopened.tools.isChecked()
        and reopened.remember_window.isChecked(),
        "reading and appearance settings restore",
    )
    check(
        form.delay.value() == 600
        and form.max_chars.value() == 4200
        and not form.automatic.isChecked()
        and reopened.search_threads.value() == 3
        and reopened.search_mode.currentData() == "hybrid"
        and reopened.search_model_dir.text() == str(folder / "search-model"),
        "translation and search preferences restore",
    )
    before = path.read_text("utf-8")
    form.key.setText("fixture-cancelled")
    reopened.reject()
    check(path.read_text("utf-8") == before, "cancel does not replace saved keys or settings")
    tester = GlobalSettings(load_settings(path), window)
    dialogs.append(tester)
    tester.show()
    tester.tabs.setCurrentIndex(1)
    tester.translation.key.setText("fixture-test-only")
    requests = []
    original_provider = translation.make_provider

    class Probe:
        def translate(self, text, target):
            return "测试译文"

    def make_probe(config):
        requests.append(config["api_key_override"])
        return Probe()

    translation.make_provider = make_probe
    tester.translation.test_service()
    wait(lambda: not tester.translation.jobs)
    translation.make_provider = original_provider
    check(
        requests == ["fixture-test-only"] and path.read_text("utf-8") == before,
        "testing a draft uses its key without persisting it",
    )
    tester.grab().save(str(output / "settings-translation.png"))
    tester.reject()
    clearer = GlobalSettings(load_settings(path), window)
    dialogs.append(clearer)
    choose(clearer.translation, "deepl")
    clearer.translation.key.clear()
    restored = save(clearer)
    cleared = GlobalSettings(restored, window)
    dialogs.append(cleared)
    check(
        not cleared.translation.key.text() and not restored.translation["api_key_encrypted"],
        "clearing and saving removes the stored key",
    )
    choose(cleared.translation, "libretranslate")
    check(
        cleared.translation.key.text() == "fixture-libre-key",
        "clearing one service preserves others",
    )
    cleared.reject()
    damaged = dict(restored.translation, api_key_encrypted="dpapi:v1:foreign-user")
    migrated = GlobalSettings(replace(restored, translation=damaged), window)
    dialogs.append(migrated)
    check(
        "重新填写" in migrated.translation.key.placeholderText(),
        "unreadable migrated key is shown as requiring replacement",
    )
    check(
        migrated.changes()["translation"]["api_key_encrypted"] == damaged["api_key_encrypted"],
        "unrelated preference save preserves an unreadable key",
    )
    migrated.reject()
    simple = GlobalSettings(load_settings(path), window)
    dialogs.append(simple)
    simple.show()
    labels = "\n".join(label.text() for label in simple.findChildren(QLabel))
    check(
        "文档目录应包含" not in labels and "通过更多菜单" not in labels,
        "settings omit the removed instructional paragraphs",
    )
    simple.grab().save(str(output / "settings-general.png"))
    simple.reject()
    library = LibraryDialog(window)
    dialogs.append(library)
    library.show()
    wait(lambda: not library.busy)
    check(not library.details.toPlainText(), "library opens without explanatory paragraphs")
    library.grab().save(str(output / "settings-library.png"))
    library.close()
    semantic = SemanticDialog(window)
    dialogs.append(semantic)
    semantic.show()
    wait(lambda: not semantic.busy)
    semantic.directory.setText(str(folder / "remembered-model"))
    semantic.directory_changed()
    wait(lambda: not semantic.busy)
    check(
        load_settings(path).search["model_dir"] == str(folder / "remembered-model"),
        "semantic model directory persists without downloading or indexing",
    )
    semantic.grab().save(str(output / "settings-semantic.png"))
    semantic.close()
    about = AboutDialog(window)
    dialogs.append(about)
    about.show()
    check(
        not any(
            "https://" in label.text() or "基于 Python" in label.text()
            for label in about.findChildren(QLabel)
        ),
        "About omits project links and technical credits",
    )
    about.grab().save(str(output / "settings-about.png"))
    about.close()
    check(
        all(
            QImage(str(project / f"src/catia_manual/assets/app.{suffix}")).pixelColor(0, 0).alpha()
            == 0
            for suffix in ("png", "ico")
        ),
        "PNG and Windows icon backgrounds are transparent",
    )
    window.showNormal()
    window.resize(1120, 740)
    QTest.qWait(100)
    window.showMaximized()
    QTest.qWait(100)
    window.close()
    wait(lambda: not window.jobs and not window.isVisible())
    restored = load_settings(path)
    check(
        restored.appearance["window_maximized"] and restored.appearance["window_width"] == 1120,
        "closing a maximized window preserves its normal size and state",
    )
    window = MainWindow(restored)
    window.show()
    wait(lambda: not window.jobs and window.history.current)
    check(window.isMaximized(), "relaunch restores the saved maximized state")
except Exception as exc:
    report["failures"].append(str(exc))
finally:
    for dialog in dialogs:
        dialog.close()
    wait(lambda: not window.jobs)
    window.close()
    app.processEvents()
    report["passed"] = not report["failures"]
    (output / "settings-persistence.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
raise SystemExit(0 if report["passed"] else 1)

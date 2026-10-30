"""Local native reader acceptance; synthetic docs, no CATIA startup or remote calls."""

import json
import os
import time
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QThreadPool  # noqa: E402
from PySide6.QtGui import QFont, QMouseEvent, QWheelEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMenu  # noqa: E402

import catia_manual.gui.app as gui  # noqa: E402
import catia_manual.gui.page_reader as reader  # noqa: E402
from catia_manual.config import load_settings  # noqa: E402
from catia_manual.core.resources import ResourceService  # noqa: E402
from catia_manual.gui.single_instance import validate_request  # noqa: E402
from catia_manual.integrations.catia import CatiaLauncher  # noqa: E402

project = Path(__file__).resolve().parents[1]
folder = project / ".cache/stage-a" / str(time.time_ns())
module = folder / "manual/online/prtug_C2"
module.mkdir(parents=True)
output = project / "data"
output.mkdir(exist_ok=True)
sample = "sample 中文 1.CATPart"
(module / sample).write_bytes(b"fixture only")
for name in ("figure.png", "other.png"):
    (module / name).write_bytes((project / "src/catia_manual/assets/app.png").read_bytes())
paragraphs = "".join(
    f'<p id="s{i}">Section {i}. Select the sketch and create a pad. '
    + "Specify a length and confirm the result. " * 10
    + "</p>"
    for i in range(18)
)
(module / "page.htm").write_text(
    '<h1>Creating a pad</h1><p><a href="' + sample + '">Example file</a> · '
    '<a href="other.htm#topic">Other topic</a></p>'
    '<img src="figure.png" width="80"><img src="other.png" width="80">' + paragraphs,
    "utf-8",
)
(module / "other.htm").write_text(
    '<h1>Other topic</h1><p id="topic">Select a profile.</p>' + paragraphs, "utf-8"
)


class Provider:
    name = "stage-a-local-fixture"

    def translate(self, text, target):
        return "选择草图，创建凸台。 " + text


reader.make_provider = gui.make_provider = lambda _: Provider()
CatiaLauncher.session_available = staticmethod(lambda: False)
opened, revealed = [], []
CatiaLauncher.open = lambda self, relative: opened.append(relative) or {"method": "fixture"}
ResourceService.reveal = lambda self, relative: revealed.append(str(self.path(relative)))
settings = replace(
    load_settings(),
    root=folder / "manual",
    database=folder / "index.sqlite3",
    config_path=folder / "config.toml",
    launcher=folder / "missing.exe",
    environment="",
    environment_dir=folder / "missing-env",
    translation={
        "provider": "libretranslate",
        "endpoint": "http://fixture.invalid",
        "automatic": False,
    },
    appearance={"remember_window": False, "show_tools": True, "reading_mode": "original"},
)
app = QApplication([])
app.setFont(QFont("Microsoft YaHei UI", 10))
window = gui.MainWindow(settings, "online/prtug_C2/page.htm")
window.show()
report = {"checks": [], "failures": []}


def check(condition, name):
    report["checks"].append({"name": name, "passed": bool(condition)})
    if not condition:
        report["failures"].append(name)


def wait(predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(20)
    raise TimeoutError("native reader acceptance timed out")


def js(script):
    result = []
    window.application_script(script, lambda value: result.append(value))
    wait(lambda: bool(result))
    return result[0]


def point(selector):
    value = json.loads(
        js(
            "JSON.stringify((()=>{const r=document.querySelector("
            + json.dumps(selector)
            + ").getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]})())"
        )
    )
    return QPoint(round(value[0]), round(value[1]))


def ready():
    wait(
        lambda: (
            window.page_ready
            and not window.page_loading
            and window.center_stack.currentWidget() == window.web
            and window.web.isVisible()
            and not window.web.cover.isVisible()
        )
    )
    QTest.qWait(100)


try:
    ready()
    wait(lambda: not window.jobs)
    check(
        not window.capabilities.sample_launch.available,
        "document-only mode disables CATIA without disabling reading",
    )
    menu = QMenu()
    relative = "online/prtug_C2/" + sample
    window.add_resource_actions(menu, relative)
    actions = {a.text(): a for a in menu.actions()}
    check(
        not actions["在 CATIA 中打开"].isEnabled()
        and actions["在文件资源管理器中选中"].isEnabled(),
        "file actions degrade independently without CATIA",
    )
    actions["在文件资源管理器中选中"].trigger()
    check(
        len(revealed) == 1 and sample in revealed[0], "Explorer action preserves Unicode and spaces"
    )

    launcher = folder / "CATSTART.exe"
    launcher.touch()
    env = folder / "CATEnv"
    env.mkdir()
    window.apply_settings(
        replace(window.settings, launcher=launcher, environment="fixture", environment_dir=env)
    )
    ready()
    check(
        window.capabilities.sample_launch.available,
        "optional launch configuration enables sample opening",
    )
    proxy = window.web.focusProxy() or window.web
    location = point('a[data-cmv-kind="sample"]')
    QTest.mouseClick(proxy, Qt.MouseButton.LeftButton, pos=location)
    QTest.qWait(200)
    check(not opened, "native single click does not launch CATIA")
    # QTest.mouseClick advances its synthetic clock past the double-click interval.
    # Deliver a coherent timestamped sequence to the actual Chromium delegate.
    stamp = int(time.monotonic() * 1000)
    for kind, offset, buttons in (
        (QEvent.Type.MouseButtonPress, 0, Qt.MouseButton.LeftButton),
        (QEvent.Type.MouseButtonRelease, 10, Qt.MouseButton.NoButton),
        (QEvent.Type.MouseButtonDblClick, 60, Qt.MouseButton.LeftButton),
        (QEvent.Type.MouseButtonRelease, 70, Qt.MouseButton.NoButton),
    ):
        event = QMouseEvent(
            kind,
            QPointF(location),
            QPointF(proxy.mapToGlobal(location)),
            Qt.MouseButton.LeftButton,
            buttons,
            Qt.KeyboardModifier.NoModifier,
        )
        event.setTimestamp(stamp + offset)
        QApplication.sendEvent(proxy, event)
    wait(lambda: bool(opened))
    QTest.qWait(200)
    check(opened == [relative], "native double click submits exactly one sample open")

    history = len(window.history.entries)
    QTest.mouseClick(proxy, Qt.MouseButton.LeftButton, pos=point("img[data-cmv-image]"))
    wait(lambda: bool(window.image_windows))
    preview = next(iter(window.image_windows))
    before = preview.view.transform().m11()
    wheel = QWheelEvent(
        QPointF(100, 100),
        QPointF(100, 100),
        QPoint(),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    QApplication.sendEvent(preview.view.viewport(), wheel)
    check(preview.view.transform().m11() > before, "image preview accepts wheel zoom")
    preview.view.original()
    check(
        abs(preview.view.transform().m11() - 1 / preview.view.devicePixelRatioF()) < 0.001,
        "image preview supports original pixel scale",
    )
    preview.move(1)
    check(preview.position == 1 and len(preview.paths) == 2, "preview can browse same-page images")
    preview.grab().save(str(output / "stage-a-image.png"))
    preview.close()
    check(
        len(window.history.entries) == history
        and window.current_document.path.endswith("page.htm"),
        "image preview does not change document navigation",
    )

    window.set_reading_mode("bilingual")
    wait(lambda: len(window.page_task.results) == len(window.page_task.page.units))
    window.open_document("online/prtug_C2/other.htm", "topic")
    ready()
    check(window.page_task.mode == "bilingual", "new pages inherit the global reading mode")
    window.set_reading_mode("chinese")
    window.go_back()
    ready()
    check(
        window.page_task.mode == "chinese" and js("document.body.dataset.mode") == "chinese",
        "back retains current global mode instead of an old page mode",
    )
    window.flush_preferences()
    check(
        load_settings(settings.config_path).appearance["reading_mode"] == "chinese",
        "reading mode persists for the next process",
    )
    window.grab().save(str(output / "stage-a-reader.png"))

    bar = window.statusBar()
    bar.publish("index", "background index", priority=10, timeout=0)
    bar.publish("error", "foreground error", priority=100, timeout=0, record=True)
    bar.publish("navigation", "loaded", priority=40, timeout=0)
    bar.clear_source("navigation")
    check(bar.currentMessage() == "foreground error", "navigation completion cannot erase an error")
    bar.clear_source("error")
    bar.clear_source("resource")
    bar.clear_source("notice")
    check(
        bar.currentMessage() == "background index",
        "lower-priority task resumes after foreground notice clears",
    )
    bar.open_activity()
    check(bar.log.count() > 0, "activity panel retains task and error details")
    bar.panel.close()
    window.show_home()
    check(
        window.details_pane.isVisible() and window.reading_controls.isVisible(),
        "home and article preserve sidebar and toolbar geometry",
    )
    try:
        validate_request({"workbench": "obsolete"})
    except ValueError:
        check(True, "single-instance protocol rejects removed macro context")
    else:
        check(False, "single-instance protocol rejects removed macro context")
except Exception as exc:
    report["failures"].append(str(exc))
finally:
    window.page_pump.stop()
    if window.page_task:
        window.page_task.paused = True
    QThreadPool.globalInstance().waitForDone()
    app.processEvents()
    window.close()
    app.processEvents()
    report["passed"] = not report["failures"]
    (output / "stage-a-gui.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))

raise SystemExit(0 if report["passed"] else 1)

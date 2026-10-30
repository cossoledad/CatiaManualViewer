"""Deterministic native WebEngine QA: translation streaming, retry, layout and cache."""

import json
import os
import shutil
import time
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtCore import QThreadPool, QTimer, QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import catia_manual.gui.app as gui  # noqa: E402
import catia_manual.gui.page_reader as reader  # noqa: E402
from catia_manual.config import load_settings  # noqa: E402

project = Path(__file__).resolve().parents[1]
folder = project / ".cache/stage5" / str(time.time_ns())
module = folder / "manual/online/prtug_C2"
module.mkdir(parents=True)
shutil.copy2(project / "src/catia_manual/assets/app.png", module / "figure.png")
paragraphs = []
for i in range(24):
    text = (
        f"Section {i}: select the sketch and create a pad. "
        + "Choose the profile, specify a length and confirm the result. " * 3
    )
    if i == 2:
        text = "FAIL_ONCE " + text
    paragraphs.append(
        f'<p id="section{i}">{text} '
        '<a href="other.htm#topic">Related instructions</a> '
        "<code>Sketch.1</code></p>"
    )
(module / "page.htm").write_text(
    '<h1>Creating a pad</h1><a name="named"></a>'
    + "".join(paragraphs[:8])
    + '<img src="figure.png" width="180"><a href="sample.CATPart">Sample.CATPart</a>'
    + "".join(paragraphs[8:]),
    "utf-8",
)
(module / "other.htm").write_text('<h1>Other page</h1><p id="topic">Use a pocket.</p>', "utf-8")
(module / "sample.CATPart").write_bytes(b"test-only")


class Provider:
    name = "stage5-deterministic"

    def __init__(self):
        self.calls = []
        self.failed = False

    def translate(self, text, target):
        self.calls.append(text)
        time.sleep(0.12)
        if "FAIL_ONCE" in text and not self.failed:
            self.failed = True
            raise ValueError("simulated rate limit; retry explicitly")
        return "中文段落：选择草图，创建凸台并确认参数。 " + text


provider = Provider()
reader.make_provider = gui.make_provider = lambda _: provider
settings = load_settings()
settings = replace(
    settings,
    root=folder / "manual",
    database=folder / "index.sqlite3",
    config_path=folder / "config.toml",
    translation={**settings.translation, "provider": "local", "automatic": True},
    appearance={**settings.appearance, "remember_window": False},
)
app = QApplication([])
window = gui.MainWindow(settings, "online/prtug_C2/page.htm")
window.show()
report = {"checks": [], "failures": []}
scratch = {}


def check(condition, name):
    report["checks"].append({"name": name, "passed": bool(condition)})
    if not condition:
        report["failures"].append(name)


def wait_for(predicate, next_step, seconds=20):
    deadline = time.monotonic() + seconds

    def poll():
        try:
            if predicate():
                QTimer.singleShot(100, next_step)
            elif time.monotonic() > deadline:
                report["failures"].append("timeout before " + next_step.__name__)
                finish()
            else:
                QTimer.singleShot(60, poll)
        except Exception as exc:
            report["failures"].append(str(exc))
            finish()

    poll()


def begin():
    check(len(window.page_task.page.units) >= 24, "render exposes all paragraph targets")
    window.set_reading_mode("bilingual")
    wait_for(lambda: 2 <= len(window.page_task.results) < 15, select_priority)


def select_priority():
    scratch["selection_at"] = len(provider.calls)
    window.activateWindow()
    window.web.setFocus()
    window.application_script("""(()=>{
      const node=[...document.querySelectorAll('.cmv-original-text')]
        .find(n=>n.textContent.includes('sketch')).firstChild;
      const start=node.textContent.indexOf('sketch');
      const range=document.createRange();range.setStart(node,start);range.setEnd(node,start+6);
      const selection=window.getSelection();selection.removeAllRanges();selection.addRange(range);
    })()""")
    wait_for(lambda: "sketch" in provider.calls[scratch["selection_at"] :], selection_priority)


def selection_priority():
    calls = provider.calls[scratch["selection_at"] :]
    check(calls.index("sketch") <= 1, "selection translation runs ahead of queued page paragraphs")
    pause()


def pause():
    check(True, "translations appear progressively before the page is complete")
    window.toggle_page_pause()
    scratch["calls"] = len(provider.calls)
    QTimer.singleShot(700, paused)


def paused():
    check(len(provider.calls) <= scratch["calls"] + 1, "pause stops submitting further paragraphs")
    window.application_script("window.scrollTo(0,1600)")
    QTimer.singleShot(150, capture_before)


def capture_before():
    window.application_script("JSON.stringify(window.cmvReader.capture())", before)


def before(position):
    position = json.loads(position)
    scratch["position"] = position
    window.toggle_page_pause()
    wait_for(lambda: len(window.page_task.results) > 8, check_position)


def check_position():
    position = scratch["position"]
    window.application_script(
        "document.querySelector('[data-unit=\""
        + position["id"]
        + "\"]').getBoundingClientRect().top",
        position_checked,
    )


def position_checked(offset):
    check(
        abs(offset - scratch["position"]["offset"]) < 5,
        "progressive insertion preserves the visible paragraph offset",
    )
    wait_for(
        lambda: (
            not window.page_worker_running
            and len(window.page_task.results) + len(window.page_task.errors)
            == len(window.page_task.page.units)
        ),
        partial,
    )


def partial():
    check(
        len(window.page_task.errors) == 1, "one failed paragraph leaves other translations visible"
    )
    identity = next(iter(window.page_task.errors))
    window.navigate(QUrl("cmv-retry:" + identity))
    wait_for(
        lambda: (
            not window.page_task.errors
            and len(window.page_task.results) == len(window.page_task.page.units)
        ),
        completed,
    )


def completed():
    check(True, "inline retry completes only the failed paragraph")
    window.grab().save(str(project / "data/stage5-bilingual.png"))
    scratch["calls"] = len(provider.calls)
    window.set_reading_mode("chinese")
    window.application_script(
        """JSON.stringify({
      mode:document.body.dataset.mode,
      images:[...document.images].every(i=>i.complete&&i.naturalWidth>0),
      links:[...document.querySelectorAll('.cmv-source a[href]')].length,
      anchor:!!document.getElementById('section12')&&!!document.getElementsByName('named')[0],
      code:document.querySelector('code').textContent,
      hidden:getComputedStyle(document.querySelector('.cmv-original-text')).display
    })""",
        chinese,
    )


def chinese(state):
    state = json.loads(state)
    check(
        state["mode"] == "chinese" and state["hidden"] == "none",
        "Chinese mode hides translated source prose",
    )
    check(
        state["images"]
        and state["links"] >= 24
        and state["anchor"]
        and state["code"] == "Sketch.1",
        "Chinese mode retains images, links, code and named/id anchors",
    )
    window.grab().save(str(project / "data/stage5-chinese.png"))
    window.set_reading_mode("original")
    window.set_reading_mode("bilingual")
    check(
        len(provider.calls) == scratch["calls"], "mode switching never repeats translated requests"
    )
    window.open_document("online/prtug_C2/page.htm", "section12")
    wait_for(lambda: window.page_ready and not window.page_loading, anchor_ready)


def anchor_ready():
    window.application_script(
        "document.getElementById('section12').getBoundingClientRect().top", anchor_checked
    )


def anchor_checked(top):
    check(abs(top) < 4, "named topic navigation still scrolls to its anchor")
    window.navigate(QUrl.fromLocalFile(str(module / "other.htm")))
    wait_for(lambda: window.page_ready and window.current_document.path.endswith("other.htm"), back)


def back():
    window.go_back()
    wait_for(
        lambda: window.page_ready and window.current_document.path.endswith("page.htm"), returned
    )


def returned():
    check(window.page_task.mode == "bilingual", "back restores the page reading mode")
    window.application_script(
        "document.getElementById('section12').getBoundingClientRect().top", returned_at
    )


def returned_at(top):
    check(abs(top) < 5, "back restores paragraph-relative reading position after cache insertion")
    window.page_sessions.clear()
    scratch["calls"] = len(provider.calls)
    window.attach_reading_page()
    wait_for(
        lambda: (
            window.page_ready and len(window.page_task.results) == len(window.page_task.page.units)
        ),
        cold_cache,
    )


def cold_cache():
    check(
        all(value.cached for value in window.page_task.results.values())
        and len(provider.calls) == scratch["calls"],
        "new page task reuses SQLite without provider requests",
    )
    finish()


def finish():
    (project / "data").mkdir(parents=True, exist_ok=True)
    window.page_pump.stop()
    if window.page_task:
        window.page_task.paused = True
    report["passed"] = not report["failures"]
    (project / "data/stage5-gui.json").write_text(json.dumps(report, indent=2), "utf-8")
    print(json.dumps(report))
    QThreadPool.globalInstance().waitForDone()
    window.close()
    app.exit(0 if report["passed"] else 1)


QTimer.singleShot(90000, lambda: (report["failures"].append("watchdog"), finish()))
wait_for(lambda: window.page_ready and not window.page_loading, begin)
raise SystemExit(app.exec())

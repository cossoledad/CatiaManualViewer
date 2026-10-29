"""Exercise actual WebEngine selection, links and navigation; never calls a remote API/CATIA."""

import json
import os
import time
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtCore import QThreadPool, QTimer, QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import catia_manual.gui.app as gui_module  # noqa: E402
from catia_manual.config import load_settings  # noqa: E402
from catia_manual.gui.app import MainWindow  # noqa: E402

app = QApplication([])
settings = load_settings()
settings = replace(
    settings,
    config_path=Path(".cache/stage2-qa.toml").resolve(),
    translation={
        **settings.translation,
        "provider": "local",
        "automatic": True,
        "selection_delay_ms": 200,
    },
)
window = MainWindow(settings, "online/prtug_C2/prtugbt0501.htm")
window.show()
report = {"checks": [], "failures": []}
sample_requests = []
original_factory = gui_module.make_provider
window.launch_sample = sample_requests.append
PAD = "online/prtug_C2/prtugbt0501.htm"
POCKET = "online/prtug_C2/prtugbt0502.htm"


def check(condition, label):
    report["checks"].append({"name": label, "passed": bool(condition)})
    if not condition:
        report["failures"].append(label)


def wait_for(predicate, next_step, timeout=15):
    deadline = time.monotonic() + timeout

    def poll():
        try:
            if predicate():
                QTimer.singleShot(150, next_step)
            elif time.monotonic() > deadline:
                report["failures"].append("Timeout before " + next_step.__name__)
                finish()
            else:
                QTimer.singleShot(100, poll)
        except Exception as exc:
            report["failures"].append(str(exc))
            finish()

    poll()


def page_is(path):
    return (
        window.current_document and window.current_document.path == path and not window.page_loading
    )


def select_word(word):
    window.application_script(
        """(()=>{
      const needle=WORD;const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
      let node;while(node=walker.nextNode()){
        const index=node.textContent.toLowerCase().indexOf(needle.toLowerCase());
        if(index<0)continue;
        const range=document.createRange();range.setStart(node,index);
        range.setEnd(node,index+needle.length);
        const selection=window.getSelection();selection.removeAllRanges();
        selection.addRange(range);break;
      }
    })()""".replace("WORD", json.dumps(word))
    )


def translation_done():
    return (
        not window.translation_running
        and not window.selection_timer.isActive()
        and ("本机离线模型" in window.translation_status.text())
        and (
            "已完成" in window.translation_status.text()
            or "本地缓存" in window.translation_status.text()
        )
    )


def word_translation():
    window.activateWindow()
    window.web.setFocus()
    select_word("pad")
    wait_for(
        lambda: translation_done() and "凸台" in window.translation.toPlainText(),
        sentence_translation,
    )


def sentence_translation():
    check(True, "selection automatically translates a word")
    window.application_script("""(()=>{const node=document.querySelector('p');
      const range=document.createRange();range.selectNodeContents(node);
      const selection=window.getSelection();selection.removeAllRanges();
      selection.addRange(range);})()""")
    wait_for(
        lambda: (
            len(window.web.selectedText()) > 20
            and translation_done()
            and "CATIA 术语" in window.translation.toPlainText()
        ),
        stale_translation,
    )


def stale_translation():
    class SlowProvider:
        name = "qa-stale-" + str(time.monotonic_ns())

        def translate(self, text, target):
            time.sleep(0.6 if text.lower() == "dimension" else 0.03)
            return text + " 已翻译"

    provider = SlowProvider()
    gui_module.make_provider = lambda _: provider
    select_word("Dimension")
    wait_for(lambda: window.translation_running, replace_selection)


def replace_selection():
    select_word("profile")
    wait_for(
        lambda: (
            window.web.selectedText().lower() == "profile"
            and not window.translation_running
            and not window.selection_timer.isActive()
            and window.translation.toPlainText().startswith("profile\n")
            and "已翻译" in window.translation.toPlainText()
        ),
        stale_checked,
    )


def stale_checked():
    check(
        "Dimension 已翻译" not in window.translation.toPlainText(),
        "stale response cannot overwrite the latest selection",
    )
    gui_module.make_provider = original_factory
    navigation_start()


def navigation_start():
    check(True, "selection automatically translates a sentence")
    window.restore_scroll(0, 600)
    QTimer.singleShot(250, lambda: window.navigate(QUrl.fromLocalFile(str(settings.root / POCKET))))
    wait_for(lambda: page_is(POCKET), back)


def back():
    check(window.history.can_back, "document link creates back history")
    window.go_back()
    wait_for(lambda: page_is(PAD), verify_back)


def verify_back():
    check(abs(window.reader_page.scrollPosition().y() - 600) < 40, "back restores scroll position")
    window.go_forward()
    wait_for(lambda: page_is(POCKET), forward)


def forward():
    check(True, "forward restores the next document")
    window.go_back()
    wait_for(lambda: page_is(PAD), anchor)


def anchor():
    window.navigate(QUrl((settings.root / PAD).as_uri() + "#RI-330128A"))
    wait_for(
        lambda: not window.page_loading and window.history.current.fragment == "RI-330128A",
        verify_anchor,
    )


def verify_anchor():
    check(window.reader_page.scrollPosition().y() > 600, "named anchor scrolls within cached page")
    check(not window.history.can_forward, "new anchor navigation truncates forward branch")
    before = len(window.history.entries)
    report["history_before_sample"] = before
    window.application_script("document.querySelector('a[href$=\"Pad1.CATPart\"]').click()")
    wait_for(lambda: bool(sample_requests), api_links)


def api_links():
    check(
        sample_requests == ["online/prtug_C2/samples/Pad1.CATPart"],
        "sample link routes to CATIA adapter",
    )
    check(
        len(window.history.entries) == report["history_before_sample"],
        "sample launch does not pollute page history",
    )
    path = "online/interfaces/InfInterfaces/interface_Documents_15814.htm"
    window.open_document(path)
    wait_for(lambda: page_is(path), click_generated_link)


def click_generated_link():
    window.application_script(
        "[...document.querySelectorAll('a')].find(a=>a.textContent==='PartDocument').click()"
    )
    wait_for(
        lambda: page_is("online/interfaces/MecModInterfaces/interface_PartDocument_23135.htm"),
        chinese_search,
    )


def chinese_search():
    check(True, "static recovery makes generated CATIA API links clickable")
    window.query.setText("装配约束")
    window.search()
    wait_for(
        lambda: window.results.count() > 0 and "个结果" in window.statusBar().currentMessage(),
        capture,
    )


def capture():
    check(
        "Assembly Constraints" in window.results.item(0).text(),
        "Chinese query finds English manual",
    )
    window.open_document(PAD)
    wait_for(lambda: page_is(PAD), final_translation)


def final_translation():
    select_word("pad")
    wait_for(
        lambda: (
            window.web.selectedText().lower() == "pad"
            and translation_done()
            and window.translation.toPlainText().splitlines()[0].lower() == "pad"
        ),
        finish,
    )


def finish():
    if window.jobs:
        QTimer.singleShot(200, finish)
        return
    output = settings.database.parent / "stage2-gui.png"
    window.grab().save(str(output))
    report["screenshot"] = str(output)
    report["passed"] = not report["failures"]
    (settings.database.parent / "stage2-gui-verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False), flush=True)
    window.close()
    app.exit(0 if report["passed"] else 1)


app.aboutToQuit.connect(lambda: QThreadPool.globalInstance().waitForDone(40000))
wait_for(lambda: page_is(PAD), word_translation)
raise SystemExit(app.exec())

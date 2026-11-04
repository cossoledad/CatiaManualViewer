"""Print an isolated snapshot of the current reading mode, without blocking navigation."""

import re
import secrets
import tempfile
from pathlib import Path

from bs4 import BeautifulSoup
from PySide6.QtCore import (
    QIODevice,
    QMarginsF,
    QObject,
    QSaveFile,
    QStandardPaths,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QPageLayout, QPageSize
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWidgets import QDialog, QFileDialog

PRINT_CSS = """
@media print {
 body {max-width:none!important;margin:0!important;padding:0!important;font-size:11pt!important;}
 img {max-width:100%!important;max-height:250mm;break-inside:avoid;}
 h1,h2,h3,h4 {break-after:avoid;}
 pre {white-space:pre-wrap;overflow-wrap:anywhere;}
 .cmv-retry {display:none!important;}
}
"""


def pdf_filename(title):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .")[:120] or "CATIA Manual"
    if re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", name):
        name = "CATIA " + name
    return name + ".pdf"


class PdfExporter(QObject):
    finished = Signal(str, bool)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.busy = False
        self.page = None
        self.snapshot = None
        self.identity = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(lambda: self.complete(False, "PDF 生成超时，请重试"))

    def available(self):
        w = self.window
        return bool(
            w.current_document
            and w.page_ready
            and not w.page_loading
            and w.center_stack.currentWidget() == w.web
            and not self.busy
        )

    def update_action(self):
        action = getattr(self.window, "pdf_action", None)
        if action is not None:
            action.setEnabled(self.available())
        share = getattr(self.window, "share_action", None)
        if share is not None:
            share.setEnabled(self.available())

    def start(self, destination=None):
        if not self.available():
            return
        w = self.window
        generation = w.navigation_generation
        if destination is None:
            folder = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.DocumentsLocation
            )
            dialog = QFileDialog(
                w,
                "打印当前页到 PDF",
                str(Path(folder) / pdf_filename(w.current_document.title)),
                "PDF 文件 (*.pdf)",
            )
            dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
            dialog.setDefaultSuffix("pdf")
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            destination = dialog.selectedFiles()[0]
        if generation != w.navigation_generation or not self.available():
            return
        self.destination = Path(destination)
        self.busy = True
        self.identity = identity = secrets.token_hex(8)
        self.update_action()
        w.statusBar().publish("pdf", "正在生成 PDF…", timeout=0)
        self.timer.start(60000)

        def captured(markup):
            if self.identity != identity:
                return
            if generation != w.navigation_generation or not markup:
                self.complete(False, "页面已切换，请重新导出")
                return
            try:
                soup = BeautifulSoup(markup, "lxml")
                if soup.title:
                    soup.title.string = w.current_document.title
                original = w.parser.source(w.current_document.path).resolve()
                for anchor in soup.select("a[href]"):
                    url = QUrl(anchor["href"])
                    if (
                        url.isLocalFile()
                        and url.fragment()
                        and Path(url.toLocalFile()).resolve() == original
                    ):
                        anchor["href"] = "#" + url.fragment()
                style = soup.new_tag("style")
                style.string = PRINT_CSS
                soup.head.append(style)
                # A file avoids setHtml's 2 MiB limit and keeps file images accessible.
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    suffix=".htm",
                    prefix="pdf-",
                    dir=w.render_cache,
                    encoding="utf-8",
                    delete=False,
                ) as output:
                    self.snapshot = Path(output.name)
                    output.write(str(soup))
                self.page = QWebEnginePage(w.profile, self)
                self.page.settings().setAttribute(
                    QWebEngineSettings.WebAttribute.JavascriptEnabled, False
                )
                self.page.loadFinished.connect(lambda ok: self.loaded(identity, ok))
                self.page.load(QUrl.fromLocalFile(str(self.snapshot)))
            except (OSError, ValueError) as exc:
                self.complete(False, "PDF 生成失败：" + str(exc))

        w.reader_page.toHtml(captured)

    def loaded(self, identity, ok):
        if self.identity != identity:
            return
        if not ok:
            self.complete(False, "PDF 页面加载失败，请重试")
            return
        layout = QPageLayout(
            QPageSize(QPageSize.PageSizeId.A4),
            QPageLayout.Orientation.Portrait,
            QMarginsF(12, 12, 12, 12),
            QPageLayout.Unit.Millimeter,
        )
        self.page.printToPdf(lambda data: self.written(identity, data), layout)

    def written(self, identity, data):
        if self.identity != identity:
            return
        data = bytes(data)
        if not data.startswith(b"%PDF-"):
            self.complete(False, "PDF 生成失败，请重试")
            return
        output = QSaveFile(str(self.destination))
        if not output.open(QIODevice.OpenModeFlag.WriteOnly):
            self.complete(False, "PDF 保存失败：" + output.errorString())
            return
        if output.write(data) != len(data):
            output.cancelWriting()
            self.complete(False, "PDF 保存失败：" + output.errorString())
            return
        if not output.commit():
            self.complete(False, "PDF 保存失败：" + output.errorString())
            return
        self.complete(True, "PDF 已保存：" + self.destination.name)

    def complete(self, success, message):
        if not self.busy:
            return
        self.busy = False
        self.identity = None
        self.timer.stop()
        if self.page is not None:
            self.page.deleteLater()
            self.page = None
        if self.snapshot is not None:
            try:
                self.snapshot.unlink(missing_ok=True)
            except OSError:
                pass  # A failed temporary-file cleanup must not mask the export result.
            self.snapshot = None
        self.update_action()
        self.window.statusBar().publish("pdf", message, timeout=6000, record=True)
        self.finished.emit(str(self.destination), success)

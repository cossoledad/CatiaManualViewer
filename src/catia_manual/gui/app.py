import argparse
import hashlib
import json
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QFont
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from catia_manual.config import Settings, load_settings
from catia_manual.core.index import ManualIndex
from catia_manual.core.parser import ManualParser
from catia_manual.core.paths import resolve_link
from catia_manual.core.translation import TranslationService, make_provider
from catia_manual.integrations.catia import CatiaLauncher


class JobSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    progress = Signal(object)
    finished = Signal()


class Job(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.signals = JobSignals()

    def run(self):
        try:
            self.signals.result.emit(self.function(self.signals.progress.emit))
        except Exception as exc:
            self.signals.error.emit(str(exc))
        finally:
            self.signals.finished.emit()


class LocalRequests(QWebEngineUrlRequestInterceptor):
    def __init__(self, roots: list[Path], parent=None):
        super().__init__(parent)
        self.roots = [root.resolve() for root in roots]

    def interceptRequest(self, info):
        url = info.requestUrl()
        allowed = url.scheme() in {"data", "about"}
        if url.isLocalFile():
            path = Path(url.toLocalFile()).resolve()
            allowed = any(path.is_relative_to(root) for root in self.roots)
        if not allowed:
            info.block(True)


class ReaderPage(QWebEnginePage):
    navigate = Signal(QUrl)

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        if (
            is_main_frame
            and navigation_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked
        ):
            self.navigate.emit(url)
            return False
        return True


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.parser = ManualParser(settings.root)
        self.render_cache = settings.database.parent / "rendered"
        self.render_cache.mkdir(parents=True, exist_ok=True)
        self.current_document = None
        self.history: list[str] = []
        self.jobs: set[Job] = set()
        self.search_generation = 0
        self.setWindowTitle("CATIA 文档阅读器 · V5-6R2023")
        self.resize(1480, 940)
        self.setMinimumSize(1000, 650)

        container = QWidget()
        layout = QVBoxLayout(container)
        header = QHBoxLayout()
        brand = QLabel("CATIA  /  文档阅读器")
        brand.setStyleSheet("font-size:20px;font-weight:600;color:#163c60;padding:8px")
        header.addWidget(brand)
        header.addStretch()
        self.module = QComboBox()
        self.module.setMinimumWidth(200)
        self.module.addItem("全部手册", None)
        header.addWidget(self.module)
        self.query = QLineEdit()
        self.query.setPlaceholderText("搜索英文标题、功能或操作步骤，例如 pad、fillet…")
        self.query.setMinimumWidth(300)
        self.query.returnPressed.connect(self.search)
        header.addWidget(self.query, 1)
        search = QPushButton("搜索")
        search.clicked.connect(self.search)
        header.addWidget(search)
        self.build_button = QPushButton("建立 / 更新索引")
        self.build_button.clicked.connect(self.build_index)
        header.addWidget(self.build_button)
        layout.addLayout(header)
        split = QSplitter()
        self.tabs = QTabWidget()
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemExpanded.connect(self.expand_module)
        self.tree.itemActivated.connect(self.activate_tree)
        self.tree.itemClicked.connect(self.activate_tree)
        self.results = QListWidget()
        self.results.setWordWrap(True)
        self.results.itemClicked.connect(self.activate_result)
        self.tabs.addTab(self.tree, "手册目录")
        self.tabs.addTab(self.results, "搜索结果")
        split.addWidget(self.tabs)

        center = QWidget()
        center_layout = QVBoxLayout(center)
        navigation = QHBoxLayout()
        back = QPushButton("← 返回")
        back.clicked.connect(self.go_back)
        navigation.addWidget(back)
        self.page_title = QLabel("欢迎")
        self.page_title.setWordWrap(True)
        navigation.addWidget(self.page_title, 1)
        self.translate_button = QPushButton("翻译选中文字")
        self.translate_button.clicked.connect(self.translate_selection)
        navigation.addWidget(self.translate_button)
        center_layout.addLayout(navigation)
        self.profile = QWebEngineProfile(self)
        self.interceptor = LocalRequests([settings.root, self.render_cache], self.profile)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        self.web = QWebEngineView()
        self.reader_page = ReaderPage(self.profile, self.web)
        self.web.setPage(self.reader_page)
        self.reader_page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptEnabled, False
        )
        self.reader_page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False
        )
        self.reader_page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
        )
        self.reader_page.navigate.connect(self.navigate)
        self.web.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.web.customContextMenuRequested.connect(self.context_menu)
        center_layout.addWidget(self.web, 1)
        split.addWidget(center)

        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.addWidget(QLabel("本页示例 · 点击在 CATIA 打开"))
        self.samples = QListWidget()
        self.samples.itemClicked.connect(self.open_sample)
        side_layout.addWidget(self.samples, 1)
        side_layout.addWidget(QLabel("翻译 / 术语辅助"))
        self.translation = QTextBrowser()
        self.translation.setPlainText(
            "选择正文中的英文后点击“翻译选中文字”。\n\n"
            "当前默认使用离线术语辅助。整句翻译需在 config.local.toml 配置翻译服务。"
        )
        side_layout.addWidget(self.translation, 2)
        split.addWidget(side)
        split.setSizes([290, 900, 290])
        layout.addWidget(split, 1)
        self.setCentralWidget(container)
        self.statusBar().showMessage("准备就绪 · 首次使用请建立索引；目录与正文可直接浏览")
        self.setStyleSheet(
            "QMainWindow{background:#f4f7fb} QPushButton{padding:6px 10px} "
            "QLineEdit,QComboBox{padding:6px} QTreeWidget,QListWidget,"
            "QTextBrowser{border:1px solid #dce3ec;border-radius:4px}"
        )
        self.load_modules()
        initial = "online/prtug_C2/prtugbt0501.htm"
        if self.parser.source(initial).is_file():
            QTimer.singleShot(0, lambda: self.open_document(initial))

    def submit(self, function, callback, progress=None, finished=None):
        job = Job(function)
        self.jobs.add(job)
        job.signals.result.connect(callback)
        job.signals.error.connect(self.show_error)
        if progress:
            job.signals.progress.connect(progress)
        job.signals.finished.connect(lambda: self.jobs.discard(job))
        if finished:
            job.signals.finished.connect(finished)
        QThreadPool.globalInstance().start(job)

    def show_error(self, message):
        self.statusBar().showMessage(message)
        QMessageBox.warning(self, "操作未完成", message)

    def load_modules(self):
        online = self.settings.root / "online"
        if not online.is_dir():
            self.statusBar().showMessage(f"找不到文档目录: {online}")
            return
        for path in sorted(online.iterdir()):
            if not path.is_dir() or not any(path.glob("*.htm")):
                continue
            self.module.addItem(path.name, path.name)
            item = QTreeWidgetItem([path.name])
            item.setData(0, Qt.ItemDataRole.UserRole, {"module": path.name})
            item.addChild(QTreeWidgetItem(["展开以加载目录…"]))
            self.tree.addTopLevelItem(item)
            if path.name == "prtug_C2":
                item.setExpanded(True)

    def expand_module(self, item):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or "module" not in data or data.get("loaded"):
            return
        data["loaded"] = True
        item.setData(0, Qt.ItemDataRole.UserRole, data)
        item.takeChildren()
        folder = self.settings.root / "online" / data["module"]
        maps = sorted(p for p in folder.glob("*sm.htm") if not p.name.endswith("ssm.htm"))
        entries = []
        if maps:
            relative = maps[0].relative_to(self.settings.root).as_posix()
            entries = self.parser.toc(relative)
            item.setText(0, f"{self.parser.parse(relative).title} · {folder.name}")
        stack = [(-1, item)]
        for entry in entries:
            while len(stack) > 1 and stack[-1][0] >= entry.depth:
                stack.pop()
            child = QTreeWidgetItem([entry.title])
            child.setData(
                0, Qt.ItemDataRole.UserRole, {"path": entry.path, "fragment": entry.fragment}
            )
            stack[-1][1].addChild(child)
            stack.append((entry.depth, child))
        if not entries:
            for path in sorted(folder.glob("*.htm")):
                child = QTreeWidgetItem([path.stem])
                child.setData(
                    0,
                    Qt.ItemDataRole.UserRole,
                    {"path": path.relative_to(self.settings.root).as_posix()},
                )
                item.addChild(child)

    def activate_tree(self, item, column=0):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if data and "path" in data:
            self.open_document(data["path"], data.get("fragment", ""))

    def activate_result(self, item):
        self.open_document(item.data(Qt.ItemDataRole.UserRole))

    def open_document(self, relative: str, fragment: str = "", remember: bool = True):
        try:
            document = self.parser.parse(relative)
            if document.kind == "frameset":
                candidates = [
                    link for link in document.links if link.kind == "document" and link.exists
                ]
                if candidates:
                    document = self.parser.parse(candidates[-1].target)
            if remember and self.current_document:
                self.history.append(self.current_document.path)
            self.current_document = document
            self.page_title.setText(document.title)
            self.samples.clear()
            seen = set()
            for link in document.links:
                if link.kind != "sample" or link.target in seen:
                    continue
                seen.add(link.target)
                item = QListWidgetItem(
                    (link.label or Path(link.target).name) + ("" if link.exists else "（文件缺失）")
                )
                item.setData(Qt.ItemDataRole.UserRole, link.target)
                item.setToolTip(link.target)
                self.samples.addItem(item)
            # setHtml uses a data URL with a ~2 MiB limit. Some real CATIA indexes exceed it.
            cache_name = hashlib.sha256(document.path.encode()).hexdigest()[:24] + ".html"
            cached = self.render_cache / cache_name
            cached.write_text(self.parser.render(document.path), encoding="utf-8")
            base = QUrl.fromLocalFile(str(cached))
            if fragment:
                base.setFragment(fragment)
            self.web.load(base)
            self.statusBar().showMessage(document.path)
        except Exception as exc:
            self.show_error(str(exc))

    def navigate(self, url):
        if not self.current_document:
            return
        link = resolve_link(
            self.settings.root, self.parser.source(self.current_document.path), url.toString()
        )
        if link.kind == "document" and link.target:
            self.open_document(link.target, link.fragment)
        elif link.kind == "sample" and link.target:
            self.launch_sample(link.target)

    def go_back(self):
        if self.history:
            self.open_document(self.history.pop(), remember=False)

    def search(self):
        query = self.query.text().strip()
        if not query:
            return
        module = self.module.currentData()
        self.search_generation += 1
        generation = self.search_generation
        self.statusBar().showMessage("搜索中…")

        def work(_):
            with ManualIndex(self.settings.database, self.settings.root) as index:
                return index.search(query, module, 80)

        def done(hits):
            if generation != self.search_generation:
                return
            self.results.clear()
            for hit in hits:
                item = QListWidgetItem(f"{hit.title}\n{hit.module}\n{hit.snippet}")
                item.setData(Qt.ItemDataRole.UserRole, hit.path)
                item.setToolTip(hit.path)
                self.results.addItem(item)
            self.tabs.setCurrentWidget(self.results)
            self.statusBar().showMessage(
                f"找到 {len(hits)} 个结果（最多显示 80 个）；若无结果，请先建立索引或使用英文关键词"
            )

        self.submit(work, done)

    def build_index(self):
        self.build_button.setEnabled(False)
        module = self.module.currentData()
        self.statusBar().showMessage("正在扫描文档并建立索引…")

        def work(progress):
            with ManualIndex(self.settings.database, self.settings.root) as index:
                return index.build(module=module, progress=progress)

        def done(report):
            self.statusBar().showMessage(
                f"索引完成：更新 {report['updated']}，未变化 {report['unchanged']}，"
                f"错误 {len(report['errors'])}"
            )
            if report["errors"]:
                self.show_error(json.dumps(report["errors"][:5], ensure_ascii=False, indent=2))

        self.submit(
            work,
            done,
            progress=lambda p: self.statusBar().showMessage(
                f"索引中 {p['processed']} / {p['total']}"
            ),
            finished=lambda: self.build_button.setEnabled(True),
        )

    def open_sample(self, item):
        self.launch_sample(item.data(Qt.ItemDataRole.UserRole))

    def launch_sample(self, relative):
        self.statusBar().showMessage("正在交给 CATIA 打开示例…")
        self.submit(
            lambda _: CatiaLauncher(self.settings).open(relative),
            lambda result: self.statusBar().showMessage(
                f"已提交给 CATIA（{result['method']}）：{result['path']}"
            ),
        )

    def context_menu(self, position):
        menu = self.web.createStandardContextMenu()
        action = QAction("翻译选中文字", menu)
        action.setEnabled(bool(self.web.selectedText().strip()))
        action.triggered.connect(self.translate_selection)
        menu.addAction(action)
        menu.exec(self.web.mapToGlobal(position))

    def translate_selection(self):
        text = self.web.selectedText().strip()
        if not text:
            self.statusBar().showMessage("请先在正文中选中英文单词或段落")
            return
        self.translate_button.setEnabled(False)
        self.translation.setPlainText("处理中…")

        def work(_):
            with ManualIndex(self.settings.database, self.settings.root) as index:
                service = TranslationService(index, make_provider(self.settings.translation))
                return service.translate(
                    text, self.settings.translation.get("target_language", "zh-CN")
                )

        self.submit(
            work,
            lambda result: self.translation.setPlainText(
                text
                + "\n\n"
                + result.text
                + "\n\n"
                + result.provider
                + (" · 本地缓存" if result.cached else "")
            ),
            finished=lambda: self.translate_button.setEnabled(True),
        )

    def closeEvent(self, event):
        if self.jobs:
            self.statusBar().showMessage("后台任务仍在运行，完成后可关闭窗口")
            event.ignore()
            return
        # Release the page before its profile to avoid WebEngine shutdown warnings.
        self.web.setPage(QWebEnginePage(self.web))
        self.reader_page.deleteLater()
        event.accept()


def run(settings: Settings) -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("CatiaManualViewer")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = MainWindow(settings)
    window.show()
    return app.exec()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    return run(load_settings(args.config))


if __name__ == "__main__":
    raise SystemExit(main())

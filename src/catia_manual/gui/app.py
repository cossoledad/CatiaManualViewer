import argparse
import hashlib
import json
import sys
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QFont, QKeySequence
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineSettings,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from catia_manual.config import Settings, load_settings, save_translation
from catia_manual.core.index import ManualIndex
from catia_manual.core.navigation import Location, NavigationHistory
from catia_manual.core.parser import ManualParser
from catia_manual.core.paths import resolve_link
from catia_manual.core.search import SearchService
from catia_manual.core.translation import TranslationService, make_provider
from catia_manual.gui.settings import TranslationSettings
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
    def __init__(self, settings: Settings, initial_target: str | None = None):
        super().__init__()
        self.settings = settings
        self.parser = ManualParser(settings.root)
        self.render_cache = settings.database.parent / "rendered"
        self.render_cache.mkdir(parents=True, exist_ok=True)
        self.current_document = None
        self.history = NavigationHistory()
        self.document_cache = OrderedDict()
        self.navigation_generation = 0
        self.page_loading = False
        self.pending_restore = None
        self.expected_render = None
        self.requested_history_position = None
        self.jobs: set[Job] = set()
        self.search_generation = 0
        self.translation_generation = 0
        self.translation_running = False
        self.translation_processing = False
        self.translation_pending = None
        self.selection_timer = QTimer(self)
        self.selection_timer.setSingleShot(True)
        self.selection_timer.timeout.connect(self.translate_selection)
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
        self.query.setPlaceholderText("中英文搜索，例如 创建凸台、装配约束、曲面偏置…")
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
        self.back_action = QAction("← 后退", self)
        self.back_action.setShortcut(QKeySequence("Alt+Left"))
        self.back_action.triggered.connect(self.go_back)
        self.forward_action = QAction("前进 →", self)
        self.forward_action.setShortcut(QKeySequence("Alt+Right"))
        self.forward_action.triggered.connect(self.go_forward)
        self.addActions([self.back_action, self.forward_action])
        self.back_menu, self.forward_menu = QMenu(self), QMenu(self)
        for action, menu in (
            (self.back_action, self.back_menu),
            (self.forward_action, self.forward_menu),
        ):
            button = QToolButton()
            button.setDefaultAction(action)
            button.setMenu(menu)
            button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
            navigation.addWidget(button)
        self.page_title = QLabel("欢迎")
        self.page_title.setWordWrap(True)
        navigation.addWidget(self.page_title, 1)
        self.auto_translate = QCheckBox("划词即译")
        self.auto_translate.setChecked(settings.translation.get("automatic", True))
        self.auto_translate.toggled.connect(self.toggle_translation)
        navigation.addWidget(self.auto_translate)
        configure = QPushButton("翻译设置")
        configure.clicked.connect(self.configure_translation)
        navigation.addWidget(configure)
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
        self.reader_page.selectionChanged.connect(self.selection_changed)
        self.reader_page.scrollPositionChanged.connect(self.scroll_changed)
        self.web.loadFinished.connect(self.page_loaded)
        self.web.installEventFilter(self)
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
        side_layout.addWidget(QLabel("即时翻译 · 选中词或句子"))
        self.translation = QTextBrowser()
        self.translation.setPlainText(
            "双击单词，或拖动选择句子，选区稳定后自动显示译文。\n\n"
            "可在“翻译设置”切换本机离线模型或翻译服务。"
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
        self.update_history_actions()
        initial = "online/prtug_C2/prtugbt0501.htm"
        if initial_target:
            QTimer.singleShot(0, lambda: self.open_target(initial_target))
        elif self.parser.source(initial).is_file():
            QTimer.singleShot(0, lambda: self.open_document(initial))

    def submit(self, function, callback, progress=None, finished=None, error=None):
        job = Job(function)
        self.jobs.add(job)
        job.signals.result.connect(callback)
        job.signals.error.connect(error or self.show_error)
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

    def open_document(
        self,
        relative: str,
        fragment: str = "",
        remember: bool = True,
        history_index: int | None = None,
    ):
        if self.current_document and not self.page_loading:
            position = self.reader_page.scrollPosition()
            self.history.save_scroll(position.x(), position.y())
        self.navigation_generation += 1
        generation = self.navigation_generation
        self.page_loading = True
        self.expected_render = None
        self.requested_history_position = history_index
        self.invalidate_translation()
        self.statusBar().showMessage("正在加载文档…")

        def prepare(_):
            source = self.parser.source(relative)
            stat = source.stat()
            key = (str(source), stat.st_mtime_ns, stat.st_size)
            cached_result = self.document_cache.get(key)
            if cached_result is not None:
                return cached_result
            document = self.parser.parse(relative)
            visited = {document.path}
            while document.kind == "frameset":
                candidates = [
                    link
                    for link in document.links
                    if link.kind == "document" and link.exists and link.target not in visited
                ]
                if not candidates:
                    break
                # Prefer the main/body frame over navigation panes.
                chosen = next(
                    (
                        link
                        for link in reversed(candidates)
                        if not any(
                            word in link.target.lower()
                            for word in ("toc.htm", "nav.htm", "ssm.htm")
                        )
                    ),
                    candidates[-1],
                )
                document = self.parser.parse(chosen.target)
                visited.add(document.path)
            cached = self.render_cache / (
                hashlib.sha256(document.path.encode()).hexdigest()[:24] + ".html"
            )
            temporary = cached.with_suffix(f".{generation}.tmp")
            temporary.write_text(self.parser.render(document.path), encoding="utf-8")
            temporary.replace(cached)
            return key, document, cached

        def prepared(result):
            if generation != self.navigation_generation:
                return
            key, document, cached = result
            self.document_cache[key] = result
            self.document_cache.move_to_end(key)
            while len(self.document_cache) > 8:
                self.document_cache.popitem(last=False)
            same_page = (
                self.current_document
                and self.current_document.path == document.path
                and self.current_document.content_hash == document.content_hash
            )
            self.current_document = document
            self.requested_history_position = None
            self.expected_render = cached.resolve()
            if history_index is not None:
                location = self.history.jump(history_index)
                self.pending_restore = (location.x, location.y)
            else:
                if remember:
                    self.history.visit(Location(document.path, fragment, document.title))
                self.pending_restore = None
            self.page_title.setText(document.title)
            self.page_title.setToolTip(document.path)
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
            self.update_history_actions()
            if same_page:
                if self.pending_restore is not None:
                    self.restore_scroll(*self.pending_restore)
                else:
                    self.scroll_to_anchor(fragment)
                self.pending_restore = None
                self.page_loading = False
                self.statusBar().showMessage(document.path + ("#" + fragment if fragment else ""))
                return
            base = QUrl.fromLocalFile(str(cached))
            if fragment:
                base.setFragment(fragment)
            self.web.load(base)
            self.statusBar().showMessage(document.path)

        def failed(message):
            if generation == self.navigation_generation:
                self.page_loading = False
                self.requested_history_position = None
                self.show_error(message)

        self.submit(prepare, prepared, error=failed)

    def application_script(self, script, callback=None):
        world = QWebEngineScript.ScriptWorldId.ApplicationWorld.value
        if callback:
            self.reader_page.runJavaScript(script, world, callback)
        else:
            self.reader_page.runJavaScript(script, world)

    def restore_scroll(self, x, y):
        self.application_script(f"window.scrollTo({json.dumps(x)}, {json.dumps(y)});")

    def scroll_to_anchor(self, fragment):
        if not fragment:
            self.restore_scroll(0, 0)
            return
        self.application_script(
            "(()=>{const key="
            + json.dumps(fragment)
            + ";const element=document.getElementById(key)"
            "||document.getElementsByName(key)[0];if(element)element.scrollIntoView();})()"
        )

    def page_loaded(self, ok):
        proxy = self.web.focusProxy()
        if proxy is not None:
            proxy.installEventFilter(self)
        if self.expected_render is None or not self.web.url().isLocalFile():
            return
        if Path(self.web.url().toLocalFile()).resolve() != self.expected_render:
            return
        if not ok:
            self.page_loading = False
            self.statusBar().showMessage("页面加载失败")
            return
        if self.pending_restore is not None:
            self.restore_scroll(*self.pending_restore)
            self.pending_restore = None
        self.page_loading = False

    def scroll_changed(self, position):
        if not self.page_loading:
            self.history.save_scroll(position.x(), position.y())

    def navigate(self, url):
        if not self.current_document:
            return
        if url.isLocalFile() and Path(url.toLocalFile()).resolve().is_relative_to(
            self.render_cache.resolve()
        ):
            self.open_document(self.current_document.path, url.fragment())
            return
        link = resolve_link(
            self.settings.root, self.parser.source(self.current_document.path), url.toString()
        )
        if link.kind == "document" and link.target:
            self.open_document(link.target, link.fragment)
        elif link.kind == "sample" and link.target:
            self.launch_sample(link.target)
        elif link.kind == "external":
            QDesktopServices.openUrl(QUrl(link.href))
        elif (
            link.kind == "resource"
            and link.exists
            and Path(link.target).suffix.lower() in {".pdf", ".txt"}
        ):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.settings.root / link.target)))
        else:
            self.statusBar().showMessage("该链接暂不可用：" + url.toDisplayString())

    def open_target(self, target: str):
        link = resolve_link(self.settings.root, self.settings.root / self.settings.homepage, target)
        if link.kind == "document" and link.target:
            self.open_document(link.target, link.fragment)
        elif link.kind == "sample" and link.target:
            self.launch_sample(link.target)
        else:
            self.show_error("无法映射到本机 CATIA 文档：" + target)

    def go_back(self):
        position = self.requested_history_position
        position = self.history.position if position is None else position
        if position > 0:
            self.go_to_history(position - 1)

    def go_forward(self):
        position = self.requested_history_position
        position = self.history.position if position is None else position
        if position + 1 < len(self.history.entries):
            self.go_to_history(position + 1)

    def go_to_history(self, position):
        location = self.history.entries[position]
        self.open_document(location.path, location.fragment, history_index=position)

    def update_history_actions(self):
        self.back_action.setEnabled(self.history.can_back)
        self.forward_action.setEnabled(self.history.can_forward)
        for menu, positions in (
            (
                self.back_menu,
                range(self.history.position - 1, max(-1, self.history.position - 15), -1),
            ),
            (
                self.forward_menu,
                range(
                    self.history.position + 1,
                    min(len(self.history.entries), self.history.position + 15),
                ),
            ),
        ):
            menu.clear()
            for position in positions:
                entry = self.history.entries[position]
                label = (entry.title or entry.path) + (
                    " #" + entry.fragment if entry.fragment else ""
                )
                action = menu.addAction(label)
                action.triggered.connect(
                    lambda checked=False, target=position: self.go_to_history(target)
                )

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.BackButton:
                self.go_back()
                return True
            if event.button() == Qt.MouseButton.ForwardButton:
                self.go_forward()
                return True
        return super().eventFilter(watched, event)

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
                return SearchService(index, self.settings.translation).search(query, module, 80)

        def done(response):
            if generation != self.search_generation:
                return
            self.results.clear()
            for hit in response.hits:
                item = QListWidgetItem(f"{hit.title}\n{hit.module}\n{hit.snippet}")
                item.setData(Qt.ItemDataRole.UserRole, hit.path)
                item.setToolTip(hit.path)
                self.results.addItem(item)
            self.tabs.setCurrentWidget(self.results)
            summary = f"找到 {len(response.hits)} 个结果（最多 80 个）"
            if response.plan.expanded != query:
                summary += " · 查询展开：" + response.plan.expanded
            if response.plan.unknown:
                summary += " · " + "；".join(response.plan.notes)
            self.statusBar().showMessage(summary)

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
        if not text or self.page_loading:
            return
        maximum = int(self.settings.translation.get("max_selection_chars", 3000))
        if len(text) > maximum:
            self.translation.setPlainText(
                f"当前选择 {len(text)} 个字符，超过自动翻译上限 {maximum}。\n"
                "请缩小选区或调整翻译设置。"
            )
            return
        self.translation_pending = (
            text,
            self.translation_generation,
            dict(self.settings.translation),
        )
        self.start_translation()

    def invalidate_translation(self):
        self.translation_generation += 1
        self.selection_timer.stop()
        self.translation_pending = None
        if self.translation_processing:
            self.translation.setPlainText("双击单词或选中句子，选区稳定后自动翻译。")
            self.translation_processing = False

    def selection_changed(self):
        self.invalidate_translation()
        if (
            self.auto_translate.isChecked()
            and not self.page_loading
            and self.web.selectedText().strip()
        ):
            self.selection_timer.start(
                int(self.settings.translation.get("selection_delay_ms", 350))
            )

    def start_translation(self):
        if self.translation_running or self.translation_pending is None:
            return
        text, generation, config = self.translation_pending
        self.translation_pending = None
        self.translation_running = True
        self.translation_processing = True
        self.translation.setPlainText(text + "\n\n翻译中…")

        def work(_):
            with ManualIndex(self.settings.database, self.settings.root) as index:
                service = TranslationService(index, make_provider(config))
                return service.translate(text, config.get("target_language", "zh-CN"))

        def done(result):
            if generation != self.translation_generation:
                return
            self.translation_processing = False
            output = text + "\n\n" + result.text
            if config.get("provider") == "local":
                import re

                from catia_manual.core.translation import GLOSSARY

                terms = [
                    f"{word}：{value}"
                    for word, value in GLOSSARY.items()
                    if re.search(r"\b" + re.escape(word) + r"s?\b", text, re.I)
                ]
                if terms and len(text.split()) > 2:
                    output += "\n\nCATIA 术语\n" + "\n".join(terms)
            provider_name = {
                "local": "本机离线模型",
                "libretranslate": "翻译服务",
                "glossary": "术语辅助",
                "chat-completions": "大模型翻译 API",
            }.get(config.get("provider"), "翻译")
            self.translation.setPlainText(
                output + "\n\n" + provider_name + (" · 本地缓存" if result.cached else "")
            )

        def failed(message):
            if generation == self.translation_generation:
                self.translation_processing = False
                self.translation.setPlainText(
                    text + "\n\n翻译未完成：" + message + "\n可在翻译设置中检查服务，或右键重试。"
                )

        def finished():
            self.translation_running = False
            if self.translation_pending is not None:
                QTimer.singleShot(0, self.start_translation)

        self.submit(work, done, finished=finished, error=failed)

    def toggle_translation(self, enabled):
        self.invalidate_translation()
        try:
            self.settings = save_translation(self.settings, {"automatic": enabled})
        except OSError as exc:
            self.statusBar().showMessage(f"本次设置已应用，保存失败：{exc}")
        if enabled:
            self.selection_changed()

    def configure_translation(self):
        dialog = TranslationSettings(self.settings.translation, self)
        if dialog.exec() != TranslationSettings.DialogCode.Accepted:
            return
        values = dialog.values()
        try:
            make_provider({**self.settings.translation, **values})
            dialog.apply_session_key()
            self.settings = save_translation(self.settings, values)
            self.auto_translate.blockSignals(True)
            self.auto_translate.setChecked(values["automatic"])
            self.auto_translate.blockSignals(False)
            self.invalidate_translation()
            self.translation.setPlainText("翻译设置已保存。重新选择英文即可自动翻译。")
            self.selection_changed()
        except Exception as exc:
            self.show_error(str(exc))

    def closeEvent(self, event):
        if self.jobs:
            self.statusBar().showMessage("后台任务仍在运行，完成后可关闭窗口")
            event.ignore()
            return
        # Release the page before its profile to avoid WebEngine shutdown warnings.
        self.web.setPage(QWebEnginePage(self.web))
        self.reader_page.deleteLater()
        event.accept()


def run(settings: Settings, initial_target: str | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("CatiaManualViewer")
    app.setFont(QFont("Microsoft YaHei UI", 10))
    window = MainWindow(settings, initial_target)
    window.show()
    return app.exec()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--open", dest="open_target")
    args = parser.parse_args()
    return run(load_settings(args.config), args.open_target)


if __name__ == "__main__":
    raise SystemExit(main())

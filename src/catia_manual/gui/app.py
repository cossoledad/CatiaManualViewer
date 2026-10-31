import argparse
import hashlib
import json
import sys
import threading
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import (
    QEvent,
    QFile,
    QIODevice,
    QObject,
    QRunnable,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QColor, QDesktopServices, QFont
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineSettings,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWidgets import (
    QApplication,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QTreeWidgetItem,
    QWidget,
)

from catia_manual.config import Settings, load_settings, save_settings, save_translation
from catia_manual.core.index import ManualIndex
from catia_manual.core.library_contract import SourceLocation
from catia_manual.core.navigation import Location, NavigationHistory
from catia_manual.core.parser import ManualParser
from catia_manual.core.paths import resolve_link
from catia_manual.core.providers import PROVIDERS
from catia_manual.core.reader_state import ReaderState
from catia_manual.core.resources import IMAGE_EXTENSIONS
from catia_manual.core.search import SearchService
from catia_manual.core.source import resolve_source
from catia_manual.core.translation import TranslationService, make_provider
from catia_manual.gui.map_workspace import MapWorkspace
from catia_manual.gui.page_reader import PageReader
from catia_manual.gui.reading_navigation import ReadingNavigation
from catia_manual.gui.relation_workspace import RelationWorkspace
from catia_manual.gui.resource_actions import ResourceActions, ResourceBridge
from catia_manual.gui.search_focus import TERMS_ROLE, SearchFocus, focus_terms, snippet_text
from catia_manual.gui.settings import GlobalSettings
from catia_manual.gui.status import StatusCenter
from catia_manual.gui.style import APP_NAME, ASSETS, app_icon
from catia_manual.gui.widgets import ReaderView
from catia_manual.gui.workspace import WorkspaceUI


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
            and navigation_type == QWebEnginePage.NavigationType.NavigationTypeBackForward
        ):
            # Application history owns navigation; Chromium must not replay a second history.
            return False
        if (
            is_main_frame
            and navigation_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked
        ):
            self.navigate.emit(url)
            return False
        return True


class MainWindow(
    RelationWorkspace,
    MapWorkspace,
    SearchFocus,
    ReadingNavigation,
    ResourceActions,
    PageReader,
    WorkspaceUI,
    QMainWindow,
):
    def __init__(self, settings: Settings, initial_target: str | None = None):
        super().__init__()
        self.settings = settings
        self.setStatusBar(StatusCenter(self))
        self.reading_state = ReaderState.from_settings(settings.appearance)
        self.preference_changes = {}
        self.preference_timer = QTimer(self)
        self.preference_timer.setSingleShot(True)
        self.preference_timer.timeout.connect(self.flush_preferences)
        self.parser = ManualParser(settings.root)
        self.render_cache = settings.database.parent / "rendered"
        self.render_cache.mkdir(parents=True, exist_ok=True)
        self.current_document = None
        self.library_id = None
        self.history = NavigationHistory()
        self.document_cache = OrderedDict()
        self.navigation_generation = 0
        self.page_loading = False
        self.pending_restore = None
        self.expected_render = None
        self.requested_history_position = None
        self.jobs: set[Job] = set()
        self.index_running = False
        self.init_resources()
        self.search_generation = 0
        self.translation_generation = 0
        self.translation_running = False
        self.translation_processing = False
        self.translation_pending = None
        self.selection_timer = QTimer(self)
        self.selection_timer.setSingleShot(True)
        self.selection_timer.timeout.connect(self.translate_selection)
        self.init_page_reader()
        self.init_function_map()
        self.init_relations()
        self.build_interface()
        self.refresh_capabilities(probe=True)
        self.update_history_actions()
        QTimer.singleShot(400, self.upgrade_search_structure)
        if initial_target:
            QTimer.singleShot(0, lambda: self.open_target(initial_target))
        else:
            QTimer.singleShot(0, self.show_home)
        if not (settings.root / "online").is_dir():
            QTimer.singleShot(250, self.configure_settings)

    def create_reader(self):
        self.profile = QWebEngineProfile(self)
        self.interceptor = LocalRequests([self.settings.root, self.render_cache], self.profile)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        web = ReaderView()
        self.reader_page = ReaderPage(self.profile, web)
        self.resource_channel = QWebChannel(self.reader_page)
        self.resource_bridge = ResourceBridge(self.reader_page)
        self.resource_bridge.requested.connect(self.resource_request)
        self.resource_channel.registerObject("resources", self.resource_bridge)
        self.reader_page.setWebChannel(
            self.resource_channel, QWebEngineScript.ScriptWorldId.ApplicationWorld.value
        )
        channel_script = QFile(":/qtwebchannel/qwebchannel.js")
        if not channel_script.open(QIODevice.OpenModeFlag.ReadOnly):
            raise RuntimeError("无法加载资源交互桥")
        self.resource_script = (
            bytes(channel_script.readAll()).decode("utf-8")
            + "\n"
            + (ASSETS / "reader_resources.js").read_text("utf-8")
        )
        web.setPage(self.reader_page)
        self.reader_page.setBackgroundColor(QColor("white"))
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
        web.loadFinished.connect(self.page_loaded)
        web.installEventFilter(self)
        web.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        web.customContextMenuRequested.connect(self.context_menu)
        return web

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
        self.statusBar().publish("error", message, priority=100, timeout=12000, record=True)

    def remember_preferences(self, changes):
        self.settings = replace(self.settings, appearance={**self.settings.appearance, **changes})
        self.preference_changes.update(changes)
        self.preference_timer.start(400)

    def flush_preferences(self):
        self.preference_timer.stop()
        if self.preference_changes:
            changes = dict(self.preference_changes)
            self.preference_changes.clear()
            try:
                self.settings = save_settings(self.settings, {"appearance": changes})
            except OSError as exc:
                self.show_error(f"本次偏好已应用，保存失败：{exc}")

    def expand_module(self, item):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data or "module" not in data or data.get("loaded"):
            return
        data["loaded"] = True
        item.setData(0, Qt.ItemDataRole.UserRole, data)
        item.takeChildren()
        manual = self.catalog.manuals[data["module"]]
        entries = self.parser.toc(manual.toc) if manual.toc else []
        stack = [(-1, item)]
        for entry in entries:
            while len(stack) > 1 and stack[-1][0] >= entry.depth:
                stack.pop()
            child = QTreeWidgetItem([entry.title])
            child.setData(
                0,
                Qt.ItemDataRole.UserRole,
                {"path": entry.path, "fragment": entry.fragment, "manual": manual.id},
            )
            stack[-1][1].addChild(child)
            stack.append((entry.depth, child))

    def activate_tree(self, item, column=0):
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not data:
            return
        if "path" in data:
            self.open_document(
                data["path"],
                data.get("fragment", ""),
                placement=data.get("placement", {"manual": data["manual"]}),
            )
        elif "module" in data:
            self.open_manual(data["module"])
        elif "category" in data:
            item.setExpanded(True)
            self.show_home(data["category"])

    def activate_result(self, item):
        if item.data(Qt.ItemDataRole.UserRole) == "@index":
            self.build_index()
            return
        source = item.data(Qt.ItemDataRole.UserRole + 1)
        placement = item.data(Qt.ItemDataRole.UserRole + 2)
        terms = item.data(TERMS_ROLE)
        if source:
            self.open_source(source, placement=placement, search_terms=terms)
        else:
            self.open_document(
                item.data(Qt.ItemDataRole.UserRole), placement=placement, search_terms=terms
            )

    def manage_library(self):
        from catia_manual.gui.library import LibraryDialog

        self.flush_preferences()
        self.flush_reading_locations()
        dialog = LibraryDialog(self)
        dialog.exec()
        dialog.deleteLater()

    def manage_terms(self):
        from catia_manual.gui.library import TermsDialog

        dialog = TermsDialog(self)
        dialog.exec()
        dialog.deleteLater()

    def manage_semantic(self):
        from catia_manual.gui.semantic import SemanticDialog

        if self.index_running:
            self.show_error("请等待文档索引完成后再建立语义索引")
            return
        self.flush_preferences()
        dialog = SemanticDialog(self)
        dialog.exec()
        dialog.deleteLater()

    def open_source(
        self, value, history_index=None, placement=None, search_terms=None, element_location=None
    ):
        if history_index is not None:
            placement = self.history.entries[history_index].placement
        try:
            source = SourceLocation.from_dict(value)
        except (ValueError, TypeError, KeyError) as exc:
            self.show_error(str(exc))
            return
        self.save_location_scroll()
        self.detach_reading_page()
        self.navigation_generation += 1
        generation = self.navigation_generation
        self.page_loading = True
        self.requested_history_position = history_index
        self.statusBar().publish("source", "正在核对原文位置…", priority=40, timeout=0)
        if self.center_stack.currentWidget() == self.web:
            self.web.begin_navigation()
        settings = self.settings

        def work(_):
            with ManualIndex(settings.database, settings.root) as index:
                return resolve_source(ManualParser(settings.root), source, index.library_id)

        def failed(message):
            if generation != self.navigation_generation:
                return
            self.statusBar().clear_source("source")
            self.page_loading = False
            self.requested_history_position = None
            self.web.reveal()
            if self.reading_page:
                self.attach_reading_page()
            self.show_error(message)

        def done(result):
            if generation != self.navigation_generation:
                return
            if result.source is None:
                failed(result.reason)
                return
            self.statusBar().clear_source("source")
            self.open_document(
                result.source.document.path,
                result.source.fragment,
                history_index=history_index,
                source_location=result.source.to_dict(),
                placement=placement,
                search_terms=search_terms,
                element_location=element_location,
            )
            if result.status.startswith("relocated"):
                self.statusBar().showMessage("原文已修订，已重新定位来源", 4000)

        self.submit(work, done, error=failed)

    def open_document(
        self,
        relative: str,
        fragment: str = "",
        remember: bool = True,
        history_index: int | None = None,
        source_location: dict | None = None,
        placement: dict | None = None,
        search_terms: list[str] | None = None,
        element_location: dict | None = None,
    ):
        self.statusBar().clear_source("source")
        self.save_location_scroll()
        self.detach_reading_page()
        parser, render_cache = self.parser, self.render_cache
        settings = self.settings
        preferred = placement or (
            self.history.entries[history_index].placement if history_index is not None else None
        )
        if preferred is None and self.history.current:
            preferred = self.history.current.placement
        self.navigation_generation += 1
        generation = self.navigation_generation
        self.page_loading = True
        self.expected_render = None
        self.requested_history_position = history_index
        self.invalidate_translation()
        if self.center_stack.currentWidget() == self.web:
            self.web.begin_navigation()
        self.statusBar().publish("navigation", "正在加载文档…", priority=40, timeout=0)

        def prepare(_):
            source = parser.source(relative)
            key = (str(source), hashlib.sha256(source.read_bytes()).hexdigest())
            cached_result = self.document_cache.get(key)
            if cached_result is not None and cached_result[1].path == relative:
                return (*cached_result[:4], self.navigation_data(settings, relative, preferred))
            document = parser.parse(relative)
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
                document = parser.parse(chosen.target)
                visited.add(document.path)
            cached = render_cache / (
                hashlib.sha256(document.path.encode()).hexdigest()[:24] + ".html"
            )
            temporary = cached.with_suffix(f".{generation}.tmp")
            reading_page = parser.render_page(document.path)
            temporary.write_text(reading_page.html, encoding="utf-8")
            temporary.replace(cached)
            return (
                key,
                document,
                cached,
                reading_page,
                self.navigation_data(settings, document.path, preferred),
            )

        def prepared(result):
            if generation != self.navigation_generation:
                return
            key, document, cached, reading_page, self.pending_navigation = result
            self.reading_page = reading_page
            self.reading_fragment = fragment
            self.pending_reading_restore = None
            self.document_cache[key] = result
            self.document_cache.move_to_end(key)
            while len(self.document_cache) > 8:
                self.document_cache.popitem(last=False)
            same_page = (
                self.current_document
                and self.center_stack.currentWidget() == self.web
                and self.current_document.path == document.path
                and self.current_document.content_hash == document.content_hash
            )
            self.current_document = document
            self.pending_relation_element = element_location if history_index is None else None
            if not self.map_active():
                self.details_pane.setVisible(self.settings.appearance.get("show_tools", True))
            self.requested_history_position = None
            self.expected_render = cached.resolve()
            if history_index is not None:
                location = self.history.jump(history_index)
                self.pending_restore = (location.x, location.y)
                self.pending_reading_restore = location.reading_anchor
            else:
                if remember:
                    selected = (
                        self.pending_navigation["selected"]
                        or {"manual": self.pending_navigation["manual"]}
                        if self.pending_navigation
                        else preferred
                    )
                    self.history.visit(
                        Location(document.path, fragment, document.title, placement=selected)
                    )
                self.pending_restore = None
                if self.history.current:
                    self.history.current.search_terms = search_terms
                    self.history.current.element_location = element_location
            if source_location:
                self.pending_reading_restore = {
                    "source_block": source_location.get("block_id", ""),
                    "fragment": source_location.get("fragment", ""),
                    "offset": source_location.get("offset_css_px", 0),
                }
                if self.history.current:
                    self.history.current.source = source_location
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
            self.sample_info.setText(
                "双击打开 · 右键定位" if self.samples.count() else "本页暂无示例文件"
            )
            self.sample_group.setVisible(self.samples.count() > 0)
            self.update_history_actions()
            if same_page:
                self.web.reveal()
                if self.pending_restore is not None:
                    self.restore_scroll(*self.pending_restore)
                else:
                    self.scroll_to_anchor(fragment)
                self.pending_restore = None
                self.page_loading = False
                self.attach_reading_page(self.pending_reading_restore, fragment)
                self.statusBar().clear_source("navigation")
                self.apply_reading_navigation()
                return
            base = QUrl.fromLocalFile(str(cached))
            if fragment:
                base.setFragment(fragment)
            self.web.begin_navigation()
            self.web.load(base)

        def failed(message):
            if generation == self.navigation_generation:
                self.page_loading = False
                self.requested_history_position = None
                self.statusBar().clear_source("navigation")
                self.web.reveal()
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
            self.web.reveal()
            self.statusBar().clear_source("navigation")
            self.show_error("页面加载失败")
            return
        if self.pending_restore is not None:
            self.restore_scroll(*self.pending_restore)
            self.pending_restore = None
        self.page_loading = False
        self.application_script(self.resource_script)
        self.attach_reading_page(self.pending_reading_restore, self.reading_fragment)

    def reveal_reading_page(self):
        if not self.page_ready or self.page_loading:
            return
        generation = self.navigation_generation
        # Give the native WebEngine surface its final visible geometry under the cover.
        # A hidden widget's first frame can otherwise appear at its pre-layout dimensions.
        self.center_stack.setCurrentWidget(self.web)
        self.map_chrome(False)

        def reveal():
            if generation == self.navigation_generation and self.page_ready:
                self.web.reveal()
                self.statusBar().clear_source("navigation")
                self.apply_reading_navigation()
                self.restore_relation_element()

        QTimer.singleShot(40, reveal)

    def scroll_changed(self, position):
        if not self.page_loading and self.center_stack.currentWidget() == self.web:
            self.history.save_scroll(position.x(), position.y())
            self.position_timer.start(150)

    def navigate(self, url):
        if url.scheme() == "cmv-retry":
            self.retry_page_translation(url.path())
            return
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
            self.statusBar().publish("resource", link.target + " · 双击打开，右键定位")
        elif link.target and Path(link.target).suffix.lower() in IMAGE_EXTENSIONS:
            self.preview_image(link.target)
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

    def activate_request(self, request):
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        if request.get("open"):
            self.open_target(request["open"])

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
        if location.path == "@relations" and location.relation_state:
            self.show_relation_graph(
                request=location.relation_state["request"],
                state=location.relation_state,
                history_index=position,
            )
            return
        if location.path == "@map" and location.map_state:
            self.show_function_map(
                request=location.map_state["request"],
                state=location.map_state,
                history_index=position,
            )
            return
        if location.path == "@home" or location.path.startswith("@category:"):
            category = (
                location.path.split(":", 1)[1] if location.path.startswith("@category:") else None
            )
            self.show_home(category, remember=False, history_index=position)
            return
        if location.source:
            self.open_source(location.source, history_index=position)
            return
        self.open_document(location.path, location.fragment, history_index=position)

    def update_history_actions(self):
        self.back_action.setEnabled(self.history.can_back)
        self.forward_action.setEnabled(self.history.can_forward)
        self.history_button.setEnabled(self.history.can_back or self.history.can_forward)
        self.back_menu.menuAction().setEnabled(self.history.can_back)
        self.forward_menu.menuAction().setEnabled(self.history.can_forward)
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
        if event.type() in {
            QEvent.Type.MouseButtonPress,
            QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick,
        } and event.button() in {Qt.MouseButton.BackButton, Qt.MouseButton.ForwardButton}:
            if event.type() in {QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick}:
                if event.button() == Qt.MouseButton.BackButton:
                    self.go_back()
                else:
                    self.go_forward()
            event.accept()
            return True
        if event.type() == QEvent.Type.ChildAdded:
            child = event.child()
            if isinstance(child, QWidget):
                child.installEventFilter(self)
        return super().eventFilter(watched, event)

    def search(self):
        query = self.query.text().strip()
        if not query:
            return
        previous = getattr(self, "search_cancel", None)
        if previous is not None:
            previous.set()
        cancel = self.search_cancel = threading.Event()
        modules = self.scope_modules()
        settings = self.settings
        scope_label = self.module.currentText().strip()
        self.search_generation += 1
        generation = self.search_generation
        self.statusBar().publish("search", "搜索中…", priority=30, timeout=0)

        def work(_):
            with ManualIndex(settings.database, settings.root) as index:
                response = SearchService(index, settings.translation, settings.search).search(
                    query, limit=80, modules=modules, cancel=cancel
                )
                return response, index.connection.execute(
                    "SELECT count(*) FROM documents"
                ).fetchone()[0]

        def done(result):
            if generation != self.search_generation:
                return
            response, indexed = result
            self.results.clear()
            terms = focus_terms(query, response.plan.expanded)
            self.tree_delegate.terms = terms
            self.tree.viewport().update()
            for hit in response.hits:
                manual = self.catalog.manuals.get(hit.module)
                if hit.placement:
                    manual = self.catalog.manuals.get(hit.placement["manual"], manual)
                label = manual.label if manual else "参考文档"
                from catia_manual.core.structure import ROLE_LABELS

                label += " · " + ROLE_LABELS.get(hit.page_role, "正文")
                preview = snippet_text(" ".join(hit.snippet.split()), terms)[:170]
                trail = hit.placement["trail"] if hit.placement else hit.heading_path
                heading = " / ".join(trail[1:-1])
                item = QListWidgetItem(
                    (f"{hit.title_zh} / {hit.title}" if hit.title_zh else hit.title)
                    + f"\n{label}"
                    + (" · " + heading if heading else "")
                    + f"\n{preview}"
                )
                item.setData(Qt.ItemDataRole.UserRole, hit.path)
                item.setData(Qt.ItemDataRole.UserRole + 1, hit.source)
                item.setData(Qt.ItemDataRole.UserRole + 2, hit.placement)
                item.setData(TERMS_ROLE, terms)
                item.setToolTip(hit.reason + "\n" + hit.path)
                self.results.addItem(item)
            self.tabs.setCurrentWidget(self.results)
            if indexed == 0:
                item = QListWidgetItem("建立搜索索引")
                item.setData(Qt.ItemDataRole.UserRole, "@index")
                self.results.addItem(item)
                self.statusBar().publish("search", "此文档尚未建立索引", record=True)
                return
            label = {"keyword": "关键词", "hybrid": "混合", "semantic": "语义"}.get(
                settings.search.get("mode", "keyword"), "关键词"
            )
            summary = f"{label} · {scope_label} · {len(response.hits)} 个结果"
            self.query.setToolTip(response.plan.expanded or query)
            notes = [note for note in response.plan.notes if note != "中文术语映射到英文原文"]
            if notes:
                summary += " · " + "；".join(notes)
            self.statusBar().publish("search", summary, record=True)

        def failed(message):
            if generation == self.search_generation:
                self.statusBar().clear_source("search")
                self.show_error(message)

        self.submit(work, done, error=failed)

    def build_index(self):
        if not self.build_button.isEnabled():
            return
        self.index_running = True
        self.build_button.setEnabled(False)
        settings = self.settings
        self.statusBar().publish("index", "正在扫描文档并建立索引…", priority=10, timeout=0)

        def work(progress):
            with ManualIndex(settings.database, settings.root) as index:
                return index.build(progress=progress)

        def done(report):
            if settings.root != self.settings.root or settings.database != self.settings.database:
                return
            self.statusBar().publish(
                "index",
                f"索引完成：更新 {report['updated']}，未变化 {report['unchanged']}，"
                f"错误 {len(report['errors'])}",
                priority=10,
                record=True,
            )
            if report["errors"]:
                self.show_error(json.dumps(report["errors"][:5], ensure_ascii=False, indent=2))
            elif self.query.text().strip():
                self.search()

        def finished():
            self.index_running = False
            if settings.root != self.settings.root or settings.database != self.settings.database:
                self.statusBar().clear_source("index")
            self.refresh_capabilities()

        def failed(message):
            self.statusBar().clear_source("index")
            self.show_error(message)

        self.submit(
            work,
            done,
            progress=lambda p: (
                self.statusBar().publish(
                    "index",
                    f"索引中 {p['processed']} / {p['total']}",
                    priority=10,
                    timeout=0,
                )
                if settings.root == self.settings.root
                and settings.database == self.settings.database
                else None
            ),
            finished=finished,
            error=failed,
        )

    def translate_selection(self):
        text = self.web.selectedText().strip()
        if not text or self.page_loading or self.center_stack.currentWidget() != self.web:
            return
        self.refresh_capabilities()
        if not self.capabilities.selection_translation.available:
            self.translation.setPlainText(
                text + "\n\n" + self.capabilities.selection_translation.reason
            )
            self.update_translation_status("未配置可用服务")
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
        self.update_translation_status()
        if self.translation_processing:
            self.translation.setPlainText("双击单词或选中句子，选区稳定后自动翻译。")
            self.translation_processing = False

    def update_translation_status(self, activity="", config=None):
        config = self.settings.translation if config is None else config
        provider = config.get("provider", "glossary")
        label = PROVIDERS.get(provider, {}).get("label", provider)
        model = config.get("model", "").strip()
        if provider == "local":
            model = "Argos 英中 / 中英"
        parts = [label, model]
        if not self.auto_translate.isChecked():
            parts.append("即译已暂停")
        if activity:
            parts.append(activity)
        self.translation_status.setText(" · ".join(part for part in parts if part))
        self.translation_status.setToolTip(
            "翻译服务："
            + label
            + ("\n模型：" + model if model else "")
            + ("\n地址：" + config["endpoint"] if config.get("endpoint") else "")
            + ("\n状态：" + activity if activity else "")
        )

    def selection_changed(self):
        self.invalidate_translation()
        if (
            self.auto_translate.isChecked()
            and not self.page_loading
            and self.center_stack.currentWidget() == self.web
            and self.web.selectedText().strip()
        ):
            self.selection_timer.start(
                int(self.settings.translation.get("selection_delay_ms", 350))
            )

    def start_translation(self):
        if self.translation_running or self.page_worker_running or self.translation_pending is None:
            return
        text, generation, config = self.translation_pending
        self.translation_pending = None
        self.translation_running = True
        self.translation_processing = True
        self.update_translation_status("翻译中…", config)
        self.translation.setPlainText(text + "\n\n翻译中…")
        settings = self.settings

        def work(_):
            with ManualIndex(settings.database, settings.root) as index:
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
            self.translation.setPlainText(output)
            self.update_translation_status("本地缓存" if result.cached else "已完成", config)

        def failed(message):
            if generation == self.translation_generation:
                self.translation_processing = False
                self.update_translation_status("翻译未完成", config)
                self.translation.setPlainText(
                    text + "\n\n翻译未完成：" + message + "\n可在翻译设置中检查服务，或右键重试。"
                )

        def finished():
            self.translation_running = False
            if self.translation_pending is not None:
                QTimer.singleShot(0, self.start_translation)
            self.page_pump.start(100)

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
        self.configure_settings(tab=1)

    def configure_settings(self, checked=False, tab=0):
        self.flush_preferences()
        dialog = GlobalSettings(self.settings, self)
        dialog.tabs.setCurrentIndex(tab)
        if dialog.exec() != GlobalSettings.DialogCode.Accepted:
            return
        try:
            changes = dialog.changes()
            updated = save_settings(self.settings, changes)
            self.apply_settings(updated)
        except Exception as exc:
            self.show_error(str(exc))

    def apply_settings(self, settings):
        self.save_map_location()
        self.flush_map_state()
        if getattr(self, "search_cancel", None) is not None:
            self.search_cancel.set()
        library_changed = (
            settings.root != self.settings.root or settings.database != self.settings.database
        )
        self.detach_reading_page()
        self.invalidate_translation()
        self.search_generation += 1
        self.settings = settings
        self.statusBar().clear_source("search")
        self.reading_state = ReaderState.from_settings(settings.appearance)
        self.resources = type(self.resources)(settings.root)
        self.refresh_capabilities(probe=True)
        QApplication.instance().setFont(
            QFont("Microsoft YaHei UI", int(settings.appearance.get("font_size", 10)))
        )
        self.web.setZoomFactor(settings.appearance.get("reader_zoom", 100) / 100)
        self.auto_translate.blockSignals(True)
        self.auto_translate.setChecked(settings.translation.get("automatic", True))
        self.auto_translate.blockSignals(False)
        self.update_translation_status()
        self.tools_action.setChecked(settings.appearance.get("show_tools", True))
        self.toggle_tools(settings.appearance.get("show_tools", True))
        if library_changed:
            self.navigation_generation += 1
            self.parser = ManualParser(settings.root)
            self.render_cache = settings.database.parent / "rendered"
            self.render_cache.mkdir(parents=True, exist_ok=True)
            self.interceptor.roots = [settings.root.resolve(), self.render_cache.resolve()]
            self.document_cache.clear()
            self.current_document = None
            self.library_id = None
            self.reading_positions.clear()
            self.history = NavigationHistory()
            self.reload_catalog()
            self.results.clear()
            self.function_map.snapshot = None
            self.relation_graph.snapshot = None
            self.translation.clear()
            self.show_home()
        else:
            self.map_chrome(self.map_active())
            self.selection_changed()
            if not self.map_active():
                self.attach_reading_page()
        self.statusBar().showMessage("设置已保存", 2000)

    def closeEvent(self, event):
        self.save_map_location()
        self.flush_map_state()
        if getattr(self, "search_cancel", None) is not None:
            self.search_cancel.set()
        if getattr(self, "semantic_cancel", None) is not None:
            self.semantic_cancel.set()
        self.page_pump.stop()
        self.position_timer.stop()
        self.flush_reading_locations()
        if self.page_task:
            self.page_task.paused = True
        if self.jobs:
            self.statusBar().showMessage("正在保存并等待后台任务完成…")
            QTimer.singleShot(100, self.close)
            event.ignore()
            return
        self.flush_preferences()
        for preview in list(self.image_windows):
            preview.close()
        if self.settings.appearance.get("remember_window", True):
            try:
                geometry = self.normalGeometry()
                self.settings = save_settings(
                    self.settings,
                    {
                        "appearance": {
                            "window_width": geometry.width(),
                            "window_height": geometry.height(),
                            "window_maximized": self.isMaximized(),
                        }
                    },
                )
            except OSError:
                pass
        # Release the page before its profile to avoid WebEngine shutdown warnings.
        self.web.setPage(QWebEnginePage(self.web))
        self.reader_page.deleteLater()
        event.accept()


def run(settings: Settings, initial_target: str | None = None) -> int:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CatiaManualViewer.Desktop")
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(app_icon())
    app.setFont(QFont("Microsoft YaHei UI", int(settings.appearance.get("font_size", 10))))
    from catia_manual.gui.single_instance import InstanceBroker

    broker = InstanceBroker(settings, app)
    request = {"open": initial_target} if initial_target else {}
    try:
        if not broker.take_or_forward(request):
            return 0
    except (ValueError, RuntimeError) as exc:
        QMessageBox.warning(None, APP_NAME, str(exc))
        return 1
    window = MainWindow(settings, initial_target)
    broker.request.connect(window.activate_request)
    app.aboutToQuit.connect(broker.close)
    window.show()
    return app.exec()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path)
    parser.add_argument("--open", dest="open_target")
    parser.add_argument("--diagnostics", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    settings = load_settings(args.config)
    if args.diagnostics:
        from catia_manual.diagnostics import verify_runtime

        return verify_runtime(settings, args.diagnostics)
    return run(settings, args.open_target)


if __name__ == "__main__":
    raise SystemExit(main())

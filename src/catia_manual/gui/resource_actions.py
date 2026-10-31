"""One resource policy for document links, context menus and sample lists."""

import json
import time

from PySide6.QtCore import QObject, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMenu

from catia_manual.core.capabilities import detect_capabilities
from catia_manual.core.paths import resolve_link
from catia_manual.core.resources import IMAGE_EXTENSIONS, ResourceService
from catia_manual.gui.image_preview import ImagePreview
from catia_manual.gui.style import icon
from catia_manual.integrations.catia import CatiaLauncher


class ResourceBridge(QObject):
    requested = Signal(str, str, str)

    @Slot(str, str, str)
    def activate(self, key, action, target):
        if action in {"select", "open", "image"} and len(target) <= 8192:
            self.requested.emit(key, action, target)


class ResourceActions:
    def original_url(self, relative, fragment=""):
        path = self.parser.source(relative)
        if not path.is_file():
            raise FileNotFoundError("原始页面不存在：" + relative)
        url = QUrl.fromLocalFile(str(path))
        if fragment:
            url.setFragment(fragment)
        return url

    def open_original(self, relative, fragment=""):
        try:
            url = self.original_url(relative, fragment)
            if not QDesktopServices.openUrl(url):
                self.show_error("无法启动默认浏览器")
        except (ValueError, OSError) as exc:
            self.show_error(str(exc))

    def add_original_action(self, menu, relative, fragment="", label="在浏览器中打开原始页面"):
        action = menu.addAction(icon("external"), label)
        try:
            self.original_url(relative, fragment)
        except (ValueError, OSError):
            action.setEnabled(False)
        action.triggered.connect(lambda: self.open_original(relative, fragment))
        return action

    def result_context_menu(self, position):
        item = self.results.itemAt(position)
        if item is None or item.data(Qt.ItemDataRole.UserRole) == "@index":
            return
        source = item.data(Qt.ItemDataRole.UserRole + 1) or {}
        menu = QMenu(self)
        self.add_original_action(
            menu, item.data(Qt.ItemDataRole.UserRole), source.get("fragment", "")
        )
        menu.exec(self.results.viewport().mapToGlobal(position))
        menu.deleteLater()

    def tree_context_menu(self, position):
        item = self.tree.itemAt(position)
        data = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        if not data:
            return
        path = data.get("path")
        if not path and data.get("module") in self.catalog.manuals:
            path = self.catalog.manuals[data["module"]].entry
        if not path:
            return
        menu = QMenu(self)
        self.add_original_action(menu, path, data.get("fragment", ""))
        menu.exec(self.tree.viewport().mapToGlobal(position))
        menu.deleteLater()

    def init_resources(self):
        self.resources = ResourceService(self.settings.root)
        self.catia_session = False
        self.capabilities = detect_capabilities(self.settings)
        self.capability_probe_running = False
        self.last_capability_probe = 0
        self.opening_samples = set()
        self.image_windows = set()

    def refresh_capabilities(self, *, probe=False):
        self.capabilities = detect_capabilities(self.settings, catia_session=self.catia_session)
        self.build_button.setEnabled(
            self.capabilities.documents.available and not self.index_running
        )
        if (
            probe
            and not self.capability_probe_running
            and time.monotonic() - self.last_capability_probe > 5
        ):
            self.capability_probe_running = True
            self.last_capability_probe = time.monotonic()

            def done(available):
                self.catia_session = available
                self.refresh_capabilities()

            def finished():
                self.capability_probe_running = False

            self.submit(
                lambda _: CatiaLauncher.session_available(),
                done,
                finished=finished,
                error=lambda _: None,
            )

    def selected_sample(self, item):
        relative = item.data(Qt.ItemDataRole.UserRole)
        self.statusBar().publish("resource", relative + " · 双击打开，右键定位", timeout=4000)

    def open_sample(self, item):
        self.launch_sample(item.data(Qt.ItemDataRole.UserRole))

    def launch_sample(self, relative):
        if relative in self.opening_samples:
            return
        try:
            self.resources.path(relative)
        except (ValueError, OSError) as exc:
            self.show_error(str(exc))
            return
        self.refresh_capabilities()
        if not self.capabilities.sample_launch.available:
            self.show_error(self.capabilities.sample_launch.reason)
            self.refresh_capabilities(probe=True)
            return
        settings = self.settings
        self.opening_samples.add(relative)
        self.statusBar().publish("resource", "正在交给 CATIA 打开示例…", priority=30, timeout=0)

        def done(result):
            self.statusBar().publish(
                "resource", "已提交给 CATIA：" + relative, priority=30, record=True
            )

        def finished():
            self.opening_samples.discard(relative)

        def failed(message):
            self.statusBar().clear_source("resource")
            self.show_error(message)

        self.submit(
            lambda _: CatiaLauncher(settings).open(relative), done, finished=finished, error=failed
        )

    def resource_request(self, key, action, target):
        if (
            not self.current_document
            or not self.reading_page
            or key != self.reading_page.key
            or self.page_loading
        ):
            return
        link = resolve_link(
            self.settings.root, self.parser.source(self.current_document.path), target
        )
        if not link.target or link.kind == "blocked":
            return
        if action == "image" and link.exists:
            self.preview_image(link.target)
        elif link.kind == "sample":
            if action == "open":
                self.launch_sample(link.target)
            elif action == "select":
                self.statusBar().publish("resource", link.target + " · 双击打开，右键定位")

    def reveal_resource(self, relative):
        try:
            self.resources.reveal(relative)
            self.statusBar().publish("resource", "已定位：" + relative, record=True)
        except (ValueError, OSError) as exc:
            self.show_error(str(exc))

    def add_resource_actions(self, menu, relative, *, image=False):
        try:
            path = self.resources.path(relative, must_exist=False)
        except (ValueError, OSError):
            return
        exists = path.is_file()
        if image:
            action = menu.addAction("预览图片")
            action.setEnabled(exists)
            action.triggered.connect(lambda: self.preview_image(relative))
        else:
            self.refresh_capabilities(probe=True)
            action = menu.addAction("在 CATIA 中打开")
            action.setEnabled(exists and self.capabilities.sample_launch.available)
            action.setToolTip(
                "双击打开" if action.isEnabled() else self.capabilities.sample_launch.reason
            )
            action.triggered.connect(lambda: self.launch_sample(relative))
        action = menu.addAction("在文件资源管理器中选中")
        action.setEnabled(exists)
        action.triggered.connect(lambda: self.reveal_resource(relative))
        menu.addAction("复制文件路径").triggered.connect(
            lambda: QApplication.clipboard().setText(str(path))
        )
        if not exists:
            menu.addAction("文件缺失").setEnabled(False)

    def sample_context_menu(self, position):
        item = self.samples.itemAt(position)
        if item is None:
            return
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        self.add_resource_actions(menu, item.data(Qt.ItemDataRole.UserRole))
        menu.exec(self.samples.viewport().mapToGlobal(position))
        menu.deleteLater()

    def context_menu(self, position):
        request = self.web.lastContextMenuRequest()
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        if request and self.current_document:
            for url in (request.linkUrl(), request.mediaUrl()):
                if url.isEmpty():
                    continue
                link = resolve_link(
                    self.settings.root,
                    self.parser.source(self.current_document.path),
                    url.toString(),
                )
                if not link.target:
                    continue
                if link.kind == "document":
                    self.add_original_action(
                        menu, link.target, link.fragment, "在浏览器中打开链接原文"
                    )
                    menu.addSeparator()
                is_image = (
                    self.resources.path(link.target, must_exist=False).suffix.lower()
                    in IMAGE_EXTENSIONS
                )
                if link.kind == "sample" or is_image:
                    self.add_resource_actions(menu, link.target, image=is_image)
                    menu.addSeparator()
        if self.web.selectedText():
            menu.addAction("复制选中文字").triggered.connect(
                lambda: QApplication.clipboard().setText(self.web.selectedText())
            )
            action = menu.addAction("翻译选中文字")
            action.setEnabled(self.capabilities.selection_translation.available)
            action.triggered.connect(self.translate_selection)
        if self.current_document:
            if not menu.isEmpty():
                menu.addSeparator()
            location = self.history.current
            self.add_original_action(
                menu, self.current_document.path, location.fragment if location else ""
            )
        if menu.isEmpty():
            action = menu.addAction("当前没有可用的资源操作")
            action.setEnabled(False)
        menu.exec(self.web.mapToGlobal(position))
        menu.deleteLater()

    def preview_image(self, relative):
        try:
            self.resources.image(relative)
        except (ValueError, OSError) as exc:
            self.show_error(str(exc))
            return
        generation = self.navigation_generation
        resources = self.resources

        def ready(urls):
            if generation != self.navigation_generation:
                return
            paths = []
            try:
                for url in json.loads(urls or "[]"):
                    link = resolve_link(
                        self.settings.root, self.parser.source(self.current_document.path), url
                    )
                    if link.target and link.exists:
                        paths.append(link.target)
                preview = ImagePreview(resources, paths, relative, self)
            except (ValueError, OSError, TypeError) as exc:
                self.show_error(str(exc))
                return
            self.image_windows.add(preview)
            preview.destroyed.connect(lambda: self.image_windows.discard(preview))
            preview.show()

        self.application_script(
            "JSON.stringify([...document.querySelectorAll('img[data-cmv-image]')].map(n=>n.src))",
            ready,
        )

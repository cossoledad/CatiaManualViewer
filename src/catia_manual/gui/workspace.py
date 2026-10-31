from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from catia_manual.core.catalog import ManualCatalog
from catia_manual.core.navigation import Location
from catia_manual.gui.function_map import FunctionMapWidget
from catia_manual.gui.search_focus import MatchDelegate
from catia_manual.gui.style import APP_NAME, STYLESHEET, app_icon, icon
from catia_manual.gui.widgets import ElidedLabel, SearchField


class CatalogCard(QFrame):
    clicked = Signal()

    def __init__(self, title, subtitle, detail, parent=None, mark_name="book"):
        super().__init__(parent)
        self.setObjectName("catalogCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumHeight(145)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        mark = QLabel()
        mark.setPixmap(icon(mark_name, "#32878a").pixmap(24, 24))
        layout.addWidget(mark)
        for text, name in ((title, "categoryTitle"), (subtitle, "muted"), (detail, "eyebrow")):
            label = QLabel(text)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setObjectName(name)
            label.setWordWrap(True)
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            layout.addWidget(label)
        mark.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.clicked.emit()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space}:
            self.clicked.emit()
        else:
            super().keyPressEvent(event)


class WorkspaceUI:
    def show_about(self):
        from catia_manual.gui.about import AboutDialog

        dialog = AboutDialog(self)
        dialog.exec()
        dialog.deleteLater()

    def build_interface(self):
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        remember = self.settings.appearance.get("remember_window", True)
        self.resize(
            int(self.settings.appearance.get("window_width", 1480)) if remember else 1480,
            int(self.settings.appearance.get("window_height", 940)) if remember else 940,
        )
        if remember and self.settings.appearance.get("window_maximized", False):
            self.setWindowState(Qt.WindowState.WindowMaximized)
        self.setMinimumSize(1060, 680)
        self.setStyleSheet(STYLESHEET)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QFrame()
        header.setObjectName("topbar")
        top = QHBoxLayout(header)
        top.setContentsMargins(20, 12, 20, 12)
        top.setSpacing(12)
        logo = QLabel()
        logo.setPixmap(app_icon().pixmap(34, 34))
        top.addWidget(logo)
        brand = QLabel(APP_NAME)
        brand.setObjectName("brand")
        top.addWidget(brand)
        top.addSpacing(24)
        self.module = QComboBox()
        self.module.setMinimumWidth(190)
        self.module.setMaximumWidth(245)
        self.module.setToolTip("搜索范围")
        self.module.currentIndexChanged.connect(self.scope_changed)
        top.addWidget(self.module)
        self.query = SearchField()
        self.query.setPlaceholderText("搜索功能、命令或操作步骤")
        self.query.returnPressed.connect(self.search)
        top.addWidget(self.query, 1)
        search = QPushButton("搜索")
        search.setObjectName("primary")
        search.clicked.connect(self.search)
        top.addWidget(search)
        self.settings_action = QAction(icon("settings"), "设置", self)
        self.settings_action.triggered.connect(self.configure_settings)
        self.settings_action.setShortcut(QKeySequence("Ctrl+,"))
        self.addAction(self.settings_action)
        menu = QMenu(self)
        self.build_button = QAction(icon("refresh"), "更新文档索引", self)
        self.build_button.triggered.connect(self.build_index)
        menu.addAction(self.build_button)
        self.library_action = menu.addAction(icon("layers"), "文档库管理…")
        self.library_action.triggered.connect(self.manage_library)
        self.terms_action = menu.addAction(icon("book"), "术语与别名…")
        self.terms_action.triggered.connect(self.manage_terms)
        self.semantic_action = menu.addAction(icon("search"), "智能搜索…")
        self.semantic_action.triggered.connect(self.manage_semantic)
        self.tools_action = QAction(icon("tools"), "显示阅读侧栏", self)
        self.tools_action.setCheckable(True)
        self.tools_action.setChecked(self.settings.appearance.get("show_tools", True))
        self.tools_action.triggered.connect(self.toggle_tools)
        menu.addAction(self.tools_action)
        self.settings_button = QToolButton()
        self.settings_button.setDefaultAction(self.settings_action)
        self.settings_button.setToolTip("设置 · Ctrl+,")
        top.addWidget(self.settings_button)
        self.more_button = QToolButton()
        self.more_button.setIcon(icon("more"))
        self.more_button.setToolTip("更多操作")
        self.more_button.setAccessibleName("更多操作")
        self.more_button.clicked.connect(
            lambda: menu.popup(self.more_button.mapToGlobal(self.more_button.rect().bottomLeft()))
        )
        top.addWidget(self.more_button)
        menu.addSeparator()
        self.about_action = menu.addAction(icon("info"), "关于 Catia Manual Viewer")
        self.about_action.triggered.connect(self.show_about)
        layout.addWidget(header)
        self.split = QSplitter()
        self.tabs = QTabWidget()
        self.tabs.setMinimumWidth(235)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.tree.setAnimated(True)
        self.tree_delegate = MatchDelegate(self.tree, tree=True)
        self.tree.setItemDelegate(self.tree_delegate)
        self.tree.itemExpanded.connect(self.expand_module)
        self.tree.itemClicked.connect(self.activate_tree)
        self.tree.itemActivated.connect(self.activate_tree)
        self.results = QListWidget()
        self.results.setWordWrap(True)
        self.results.setItemDelegate(MatchDelegate(self.results))
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.itemClicked.connect(self.activate_result)
        self.results.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.results.customContextMenuRequested.connect(self.result_context_menu)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.tree_context_menu)
        self.tabs.addTab(self.tree, "功能导航")
        self.tabs.addTab(self.results, "搜索结果")
        self.split.addWidget(self.tabs)
        middle = QWidget()
        middle.setMinimumWidth(320)
        middle.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        center = QVBoxLayout(middle)
        center.setContentsMargins(12, 8, 12, 0)
        nav = QHBoxLayout()
        nav.setSpacing(4)
        home = QToolButton()
        home.setIcon(icon("home"))
        home.setToolTip("功能首页")
        home.clicked.connect(lambda: self.show_home())
        nav.addWidget(home)
        self.back_action = QAction(icon("back"), "后退", self)
        self.forward_action = QAction(icon("forward"), "前进", self)
        self.back_action.setShortcut(QKeySequence("Alt+Left"))
        self.forward_action.setShortcut(QKeySequence("Alt+Right"))
        self.back_action.triggered.connect(self.go_back)
        self.forward_action.triggered.connect(self.go_forward)
        self.addActions([self.back_action, self.forward_action])
        self.back_menu, self.forward_menu = QMenu(self), QMenu(self)
        self.history_menu = QMenu(self)
        self.history_menu.addMenu(self.back_menu).setText("后退记录")
        self.history_menu.addMenu(self.forward_menu).setText("前进记录")
        self.navigation_buttons = []
        for action in (self.back_action, self.forward_action):
            button = QToolButton()
            button.setDefaultAction(action)
            nav.addWidget(button)
            self.navigation_buttons.append(button)
        self.history_button = QToolButton()
        self.history_button.setIcon(icon("history"))
        self.history_button.setToolTip("浏览记录")
        self.history_button.setAccessibleName("浏览记录")
        self.history_button.clicked.connect(
            lambda: self.history_menu.popup(
                self.history_button.mapToGlobal(self.history_button.rect().bottomLeft())
            )
        )
        nav.addWidget(self.history_button)
        self.page_title = ElidedLabel("功能首页")
        self.page_title.setObjectName("muted")
        self.page_title.setMinimumWidth(0)
        nav.addSpacing(8)
        nav.addWidget(self.page_title, 1)
        self.add_page_controls(nav)
        self.focus_action = QAction(icon("highlight"), "突出显示搜索匹配", self)
        self.focus_action.setCheckable(True)
        self.focus_action.setChecked(True)
        self.focus_action.setEnabled(False)
        self.focus_action.triggered.connect(lambda: self.update_search_focus())
        self.focus_button = QToolButton()
        self.focus_button.setDefaultAction(self.focus_action)
        nav.addWidget(self.focus_button)
        self.map_button = QToolButton()
        self.map_button.setIcon(icon("network"))
        self.map_button.setCheckable(True)
        self.map_button.setToolTip("功能地图")
        self.map_button.setAccessibleName("功能地图")
        self.map_button.clicked.connect(self.toggle_function_map)
        nav.addWidget(self.map_button)
        self.tools_button = QToolButton()
        self.tools_button.setIcon(icon("tools"))
        self.tools_button.setCheckable(True)
        self.tools_button.setChecked(self.tools_action.isChecked())
        self.tools_button.setToolTip("阅读侧栏")
        self.tools_button.setAccessibleName("阅读侧栏")
        self.tools_button.toggled.connect(self.toggle_tools)
        nav.addWidget(self.tools_button)
        center.addLayout(nav)
        breadcrumbs = QHBoxLayout()
        self.return_results = QToolButton()
        self.return_results.setIcon(icon("search"))
        self.return_results.setToolTip("返回搜索结果")
        self.return_results.setAccessibleName("返回搜索结果")
        self.return_results.clicked.connect(lambda: self.tabs.setCurrentWidget(self.results))
        self.return_results.hide()
        breadcrumbs.addWidget(self.return_results)
        self.breadcrumb = QLabel()
        self.breadcrumb.setTextFormat(Qt.TextFormat.RichText)
        self.breadcrumb.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.breadcrumb.linkActivated.connect(self.breadcrumb_clicked)
        self.breadcrumb.setMinimumWidth(0)
        self.breadcrumb.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.breadcrumb.setFixedHeight(32)
        breadcrumbs.addWidget(self.breadcrumb, 1)
        self.reference_reading = QToolButton()
        self.reference_reading.setIcon(icon("book"))
        self.reference_reading.setAccessibleName("参考说明")
        self.reference_reading.setToolTip("查看当前文章引用的参考说明")
        self.reference_reading.clicked.connect(self.open_reference_reading)
        self.reference_reading.hide()
        breadcrumbs.addWidget(self.reference_reading)
        self.other_positions = QToolButton()
        self.other_positions.setIcon(icon("network"))
        self.other_positions.setToolTip("其他功能位置")
        self.other_positions.setAccessibleName("其他位置")
        self.other_positions.clicked.connect(self.choose_other_position)
        self.other_positions.hide()
        breadcrumbs.addWidget(self.other_positions)
        center.addLayout(breadcrumbs)
        self.center_stack = QStackedWidget()
        self.home_scroll = QScrollArea()
        self.home_scroll.setWidgetResizable(True)
        self.home_content = QWidget()
        self.home_layout = QVBoxLayout(self.home_content)
        self.home_layout.setContentsMargins(24, 28, 24, 28)
        self.home_scroll.setWidget(self.home_content)
        self.center_stack.addWidget(self.home_scroll)
        self.web = self.create_reader()
        self.center_stack.addWidget(self.web)
        self.function_map = FunctionMapWidget()
        self.function_map.opened.connect(self.open_map_node)
        self.function_map.toggled.connect(self.map_toggle_node)
        self.function_map.paged.connect(self.map_page_node)
        self.function_map.drilled.connect(self.map_drill_node)
        self.function_map.upRequested.connect(self.map_up)
        self.function_map.allRequested.connect(
            lambda: self.refresh_function_map({"scope": {"kind": "all"}},
                                              reveal=True, keep_view=False)
        )
        self.function_map.currentRequested.connect(self.map_current)
        self.function_map.stateChanged.connect(self.save_map_location)
        self.function_map.view.viewport().installEventFilter(self)
        self.function_map.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.function_map.view.customContextMenuRequested.connect(self.map_context_menu)
        self.center_stack.addWidget(self.function_map)
        center.addWidget(self.center_stack, 1)
        self.split.addWidget(middle)
        self.details_pane = QWidget()
        self.details_pane.setMinimumWidth(235)
        self.details_pane.setObjectName("readingSidebar")
        details = QVBoxLayout(self.details_pane)
        details.setContentsMargins(10, 16, 14, 12)
        side_header = QHBoxLayout()
        side_title = QLabel("阅读工具")
        side_title.setObjectName("sectionTitle")
        side_header.addWidget(side_title)
        side_header.addStretch()
        close_tools = QToolButton()
        close_tools.setIcon(icon("close"))
        close_tools.setToolTip("收起阅读侧栏")
        close_tools.clicked.connect(lambda: self.toggle_tools(False))
        side_header.addWidget(close_tools)
        details.addLayout(side_header)
        self.sample_group = QWidget()
        samples_layout = QVBoxLayout(self.sample_group)
        samples_layout.setContentsMargins(0, 0, 0, 0)
        samples_layout.addWidget(QLabel("示例文件"))
        self.samples = QListWidget()
        self.samples.setMaximumHeight(190)
        self.samples.itemClicked.connect(self.selected_sample)
        self.samples.itemDoubleClicked.connect(self.open_sample)
        self.samples.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.samples.customContextMenuRequested.connect(self.sample_context_menu)
        for shortcut in ("Return", "Enter"):
            action = QAction(self.samples)
            action.setShortcut(QKeySequence(shortcut))
            action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
            action.triggered.connect(
                lambda: (
                    self.open_sample(self.samples.currentItem())
                    if self.samples.currentItem()
                    else None
                )
            )
            self.samples.addAction(action)
        samples_layout.addWidget(self.samples)
        self.sample_info = QLabel("选择文章查看资源")
        self.sample_info.setObjectName("muted")
        samples_layout.addWidget(self.sample_info)
        self.sample_group.setMinimumHeight(100)
        self.sample_group.setMaximumHeight(220)
        details.addWidget(self.sample_group)
        translation_header = QHBoxLayout()
        translation_header.addWidget(QLabel("翻译"))
        translation_header.addStretch()
        self.auto_translate = QCheckBox("即译")
        self.auto_translate.setChecked(self.settings.translation.get("automatic", True))
        self.auto_translate.toggled.connect(self.toggle_translation)
        translation_header.addWidget(self.auto_translate)
        details.addLayout(translation_header)
        self.translation = QTextBrowser()
        self.translation.setPlaceholderText("选中文字查看翻译")
        details.addWidget(self.translation, 1)
        self.split.addWidget(self.details_pane)
        sizes = self.settings.appearance.get("sidebar_sizes", [275, 925, 280])
        if (
            not isinstance(sizes, list)
            or len(sizes) != 3
            or any(not isinstance(n, int) or n < 0 for n in sizes)
        ):
            sizes = [275, 925, 280]
        self.split.setSizes(sizes)
        self.tools_width = max(235, sizes[2])
        self.split.splitterMoved.connect(
            lambda *_: self.remember_preferences({"sidebar_sizes": self.split.sizes()})
        )
        self.split.setCollapsible(0, False)
        self.split.setCollapsible(1, False)
        self.split.setCollapsible(2, False)
        self.split.setStretchFactor(1, 1)
        self.details_pane.setVisible(self.tools_action.isChecked())
        layout.addWidget(self.split, 1)
        self.setCentralWidget(container)
        self.translation_status = ElidedLabel()
        self.translation_status.setObjectName("translationStatus")
        self.translation_status.setTextFormat(Qt.TextFormat.PlainText)
        self.translation_status.setMaximumWidth(290)
        self.translation_status.setMinimumWidth(0)
        self.statusBar().addPermanentWidget(self.translation_status)
        self.update_translation_status()
        self.web.setZoomFactor(self.settings.appearance.get("reader_zoom", 100) / 100)
        focus_search = QAction(self)
        focus_search.setShortcut(QKeySequence("Ctrl+K"))
        focus_search.triggered.connect(lambda: (self.query.setFocus(), self.query.selectAll()))
        self.addAction(focus_search)
        self.reload_catalog()

    def reload_catalog(self):
        self.catalog = ManualCatalog(self.settings.root)
        self.module.blockSignals(True)
        self.module.clear()
        self.module.addItem("全部功能", {"kind": "all"})
        self.tree.clear()
        self.module_items = {}
        for category in self.catalog.categories:
            self.module.addItem(category.label, {"kind": "category", "id": category.id})
            node = QTreeWidgetItem([category.label])
            node.setIcon(0, icon("grid"))
            node.setToolTip(0, category.title)
            node.setData(0, Qt.ItemDataRole.UserRole, {"category": category.id})
            self.tree.addTopLevelItem(node)
            for manual in category.manuals:
                self.module.addItem("  " + manual.label, {"kind": "manual", "id": manual.id})
                child = QTreeWidgetItem([manual.label])
                child.setToolTip(0, manual.title)
                child.setIcon(0, icon("book"))
                child.setData(
                    0, Qt.ItemDataRole.UserRole, {"module": manual.id, "category": category.id}
                )
                if manual.toc:
                    child.addChild(QTreeWidgetItem(["…"]))
                node.addChild(child)
                self.module_items[manual.id] = child
        self.module.blockSignals(False)

    def set_scope(self, kind, identity=None):
        for index in range(self.module.count()):
            value = self.module.itemData(index)
            if value.get("kind") == kind and (identity is None or value.get("id") == identity):
                self.module.setCurrentIndex(index)
                return

    def scope_changed(self):
        label = self.module.currentText().strip()
        self.query.setPlaceholderText(
            "搜索功能、命令或操作步骤" if self.module.currentIndex() == 0 else f"在{label}中搜索"
        )

    def scope_modules(self):
        scope = self.module.currentData() or {"kind": "all"}
        if scope["kind"] == "category":
            return self.catalog.scope([m.id for m in self.catalog.category(scope["id"]).manuals])
        if scope["kind"] == "manual":
            return self.catalog.scope([scope["id"]])
        return None

    def save_location_scroll(self):
        if self.page_loading:
            return
        if self.map_active():
            self.save_map_location()
            return
        if self.center_stack.currentWidget() == self.web:
            position = self.reader_page.scrollPosition()
            self.history.save_scroll(position.x(), position.y())
        else:
            self.history.save_scroll(0, self.home_scroll.verticalScrollBar().value())

    def show_home(self, category_id=None, remember=True, history_index=None):
        self.statusBar().clear_source("source")
        self.save_location_scroll()
        self.detach_reading_page()
        self.navigation_generation += 1
        self.page_loading = False
        self.expected_render = None
        self.requested_history_position = None
        self.invalidate_translation()
        self.center_stack.setCurrentWidget(self.home_scroll)
        self.map_chrome(False)
        self.breadcrumb.clear()
        self.other_positions.hide()
        self.pending_navigation = None
        self.reference_reading.hide()
        self.web.reveal()
        self.details_pane.setVisible(self.settings.appearance.get("show_tools", True))
        self.samples.clear()
        self.sample_group.hide()
        self.sample_info.setText("选择文章查看资源")
        self.translation.clear()
        self.focus_action.setEnabled(False)
        while self.home_layout.count():
            item = self.home_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        category = self.catalog.category(category_id) if category_id else None
        self.set_scope("category" if category else "all", category.id if category else None)
        title = category.label if category else "探索 CATIA 文档"
        self.page_title.setText(category.label if category else "功能首页")
        eyebrow = QLabel(category.title if category else "CATIA V5 · DOCUMENTATION")
        eyebrow.setObjectName("eyebrow")
        self.home_layout.addWidget(eyebrow)
        hero = QLabel(title)
        hero.setObjectName("hero")
        self.home_layout.addWidget(hero)
        subtitle = QLabel(
            "设计、分析与制造，按功能查阅。" if not category else f"{len(category.manuals)} 本手册"
        )
        subtitle.setObjectName("muted")
        self.home_layout.addWidget(subtitle)
        self.home_layout.addSpacing(16)
        grid_widget = QWidget()
        grid = QGridLayout(grid_widget)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(16)
        items = category.manuals if category else self.catalog.categories
        for index, entry in enumerate(items):
            category_marks = {
                "infrastructure": "layers",
                "mechanical-design": "cube",
                "shape-design-styling": "surface",
                "analysis": "chart",
                "equipment-systems-engineering": "system",
                "machining": "tool",
                "product-synthesis": "network",
            }
            card = CatalogCard(
                entry.label,
                entry.title if entry.title != entry.label else "",
                "打开手册" if category else f"{len(entry.manuals)} 本手册",
                mark_name="book" if category else category_marks.get(entry.id, "grid"),
            )
            if category:
                card.clicked.connect(lambda identity=entry.id: self.open_manual(identity))
            else:
                card.clicked.connect(lambda identity=entry.id: self.show_home(identity))
            grid.addWidget(card, index // 2, index % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.home_layout.addWidget(grid_widget)
        if not items:
            empty = QLabel("请选择文档目录以开始阅读。")
            empty.setObjectName("muted")
            self.home_layout.addWidget(empty)
            button = QPushButton("选择文档目录")
            button.clicked.connect(self.configure_settings)
            self.home_layout.addWidget(button)
        self.home_layout.addStretch()
        if history_index is not None:
            location = self.history.jump(history_index)
            self.home_scroll.verticalScrollBar().setValue(int(location.y))
        elif remember:
            self.history.visit(
                Location("@category:" + category.id if category else "@home", title=title)
            )
            self.home_scroll.verticalScrollBar().setValue(0)
        self.update_history_actions()
        self.statusBar().clear_source("navigation")

    def open_manual(self, identity):
        manual = self.catalog.manuals[identity]
        self.set_scope("manual", identity)
        item = self.module_items.get(identity)
        if item:
            self.tree.setCurrentItem(item)
            item.parent().setExpanded(True)
            item.setExpanded(True)
        self.open_document(manual.entry)

    def toggle_tools(self, enabled):
        if not enabled and self.details_pane.isVisible():
            self.tools_width = max(235, self.split.sizes()[2])
        self.remember_preferences({"show_tools": enabled})
        for control in (self.tools_action, self.tools_button):
            control.blockSignals(True)
            control.setChecked(enabled)
            control.blockSignals(False)
        self.details_pane.setVisible(enabled and not self.map_active())
        if enabled:
            sizes = self.split.sizes()
            if sizes[2] < 235:
                width = min(self.tools_width, max(235, self.split.width() // 4))
                self.split.setSizes([sizes[0], max(320, sizes[1] - width), width])

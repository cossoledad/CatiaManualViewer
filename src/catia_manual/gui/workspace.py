from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QPushButton,
    QScrollArea,
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
from catia_manual.gui.style import APP_NAME, STYLESHEET, app_icon, icon


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
    def build_interface(self):
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(
            int(self.settings.appearance.get("window_width", 1480)),
            int(self.settings.appearance.get("window_height", 940)),
        )
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
        self.query = QLineEdit()
        self.query.setObjectName("search")
        self.query.setClearButtonEnabled(True)
        self.query.setPlaceholderText("搜索功能、命令或操作步骤")
        self.query.addAction(icon("search"), QLineEdit.ActionPosition.LeadingPosition)
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
        menu.addAction(self.settings_action)
        self.build_button = QAction(icon("refresh"), "更新文档索引", self)
        self.build_button.triggered.connect(self.build_index)
        menu.addAction(self.build_button)
        self.tools_action = QAction("显示阅读侧栏", self)
        self.tools_action.setCheckable(True)
        self.tools_action.setChecked(self.settings.appearance.get("show_tools", True))
        self.tools_action.triggered.connect(self.toggle_tools)
        menu.addAction(self.tools_action)
        configure = QToolButton()
        configure.setDefaultAction(self.settings_action)
        configure.setMenu(menu)
        configure.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        top.addWidget(configure)
        layout.addWidget(header)
        self.split = QSplitter()
        self.tabs = QTabWidget()
        self.tabs.setMinimumWidth(235)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(16)
        self.tree.setAnimated(True)
        self.tree.itemExpanded.connect(self.expand_module)
        self.tree.itemClicked.connect(self.activate_tree)
        self.tree.itemActivated.connect(self.activate_tree)
        self.results = QListWidget()
        self.results.setWordWrap(True)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.itemClicked.connect(self.activate_result)
        self.tabs.addTab(self.tree, "功能导航")
        self.tabs.addTab(self.results, "搜索结果")
        self.split.addWidget(self.tabs)
        middle = QWidget()
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
        for action, history_menu in (
            (self.back_action, self.back_menu),
            (self.forward_action, self.forward_menu),
        ):
            button = QToolButton()
            button.setDefaultAction(action)
            button.setMenu(history_menu)
            button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
            nav.addWidget(button)
        self.page_title = QLabel("功能首页")
        self.page_title.setObjectName("muted")
        self.page_title.setMinimumWidth(0)
        nav.addSpacing(8)
        nav.addWidget(self.page_title, 1)
        center.addLayout(nav)
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
        center.addWidget(self.center_stack, 1)
        self.split.addWidget(middle)
        self.details_pane = QWidget()
        self.details_pane.setMinimumWidth(235)
        details = QVBoxLayout(self.details_pane)
        details.setContentsMargins(10, 16, 14, 12)
        self.sample_group = QWidget()
        samples_layout = QVBoxLayout(self.sample_group)
        samples_layout.setContentsMargins(0, 0, 0, 0)
        samples_layout.addWidget(QLabel("示例文件"))
        self.samples = QListWidget()
        self.samples.setMaximumHeight(190)
        self.samples.itemClicked.connect(self.open_sample)
        samples_layout.addWidget(self.samples)
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
        self.split.setSizes([275, 925, 280])
        self.split.setCollapsible(0, False)
        self.split.setCollapsible(1, False)
        layout.addWidget(self.split, 1)
        self.setCentralWidget(container)
        self.web.setZoomFactor(self.settings.appearance.get("reader_zoom", 100) / 100)
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
            return self.catalog.category(scope["id"]).modules
        if scope["kind"] == "manual":
            return self.catalog.manuals[scope["id"]].search_modules
        return None

    def save_location_scroll(self):
        if self.page_loading:
            return
        if self.center_stack.currentWidget() == self.web:
            position = self.reader_page.scrollPosition()
            self.history.save_scroll(position.x(), position.y())
        else:
            self.history.save_scroll(0, self.home_scroll.verticalScrollBar().value())

    def show_home(self, category_id=None, remember=True, history_index=None):
        self.save_location_scroll()
        self.navigation_generation += 1
        self.page_loading = False
        self.expected_render = None
        self.requested_history_position = None
        self.invalidate_translation()
        self.center_stack.setCurrentWidget(self.home_scroll)
        self.details_pane.hide()
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
        self.statusBar().clearMessage()

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
        from catia_manual.config import save_settings

        self.settings = save_settings(self.settings, {"appearance": {"show_tools": enabled}})
        self.details_pane.setVisible(enabled and self.center_stack.currentWidget() == self.web)

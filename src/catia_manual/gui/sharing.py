"""Share actual articles, open durable references, and opt into a Windows URL handler."""

import json
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from catia_manual.agent.options import options
from catia_manual.core.library_contract import DocumentRef, SourceLocation
from catia_manual.core.readonly_index import ReadOnlyIndex
from catia_manual.core.sharing import browser_link, link_for_source, resolve_page_link
from catia_manual.gui.style import ASSETS
from catia_manual.runtime import is_compiled, process_executable


def register_protocol():
    if sys.platform != "win32":
        raise OSError("当前仅支持 Windows 链接关联")
    import winreg

    executable = process_executable()
    command = (
        f'"{executable}" --share-uri "%1"'
        if is_compiled()
        else (f'"{executable}" -m catia_manual.entry --share-uri "%1"')
    )
    base = r"Software\Classes\catia-manual"
    values = {
        "": {"": "URL:Catia Manual Viewer", "URL Protocol": ""},
        r"shell\open\command": {"": command},
        "DefaultIcon": {"": f'"{executable}",0' if is_compiled() else str(ASSETS / "app.ico")},
    }
    for suffix, entries in values.items():
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, base + ("\\" + suffix if suffix else "")
        ) as key:
            for name, value in entries.items():
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


class ShareDialog(QDialog):
    def __init__(self, window, source):
        super().__init__(window)
        self.setWindowTitle("分享页面")
        self.resize(650, 240)
        self.uri = link_for_source(source.to_dict())
        self.window = window
        layout = QVBoxLayout(self)
        title = QLabel(window.current_document.title)
        title.setWordWrap(True)
        layout.addWidget(title)
        form = QFormLayout()
        layout.addLayout(form)
        self.uri_field = self.add_link(form, "应用链接", self.uri)
        self.address = QComboBox()
        from catia_manual.gui.agent import local_addresses

        profile = options(window.settings.agent)
        addresses = local_addresses() if profile["host"] == "0.0.0.0" else [profile["host"]]
        self.address.addItems(addresses)
        form.addRow("服务地址", self.address)
        self.web_field = self.add_link(form, "浏览器链接", "")
        self.web_field.parentWidget().setToolTip("服务运行时可用；启用访问令牌时请使用 Agent")
        self.address.currentTextChanged.connect(self.refresh)
        window.agent_controller.changed.connect(self.refresh)
        buttons = QHBoxLayout()
        register = QPushButton("关联应用链接")
        register.clicked.connect(self.register)
        buttons.addWidget(register)
        open_agent = QPushButton("Agent 接入…")
        open_agent.clicked.connect(window.manage_agent)
        buttons.addWidget(open_agent)
        buttons.addStretch()
        done = QPushButton("完成")
        done.clicked.connect(self.accept)
        buttons.addWidget(done)
        layout.addLayout(buttons)
        self.note = QLabel("应用链接也可粘贴到“更多 → 打开分享链接”。")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.refresh()

    def add_link(self, form, label, value):
        from PySide6.QtWidgets import QWidget

        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        field = QLineEdit(value)
        field.setReadOnly(True)
        field.setCursorPosition(0)
        copy = QPushButton("复制")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(field.text()))
        field.textChanged.connect(lambda text: copy.setEnabled(bool(text)))
        layout.addWidget(field, 1)
        layout.addWidget(copy)
        copy.setEnabled(bool(value))
        form.addRow(label, row)
        return field

    def refresh(self):
        profile = options(self.window.settings.agent)
        available = self.window.agent_controller.state == "running" and not profile["require_token"]
        self.web_field.setText(
            browser_link(f"http://{self.address.currentText()}:{profile['port']}", self.uri)
            if available
            else ""
        )
        self.web_field.setPlaceholderText("请启动无令牌的 Agent 服务")
        self.web_field.setCursorPosition(0)

    def register(self):
        try:
            register_protocol()
            self.note.setText("已关联当前程序；移动便携版后请重新关联。")
        except OSError:
            self.note.setText("无法关联应用链接；可在查看器中粘贴打开。")


class Sharing:
    def share_page(self):
        if not self.pdf_exporter.available():
            return
        generation, document = self.navigation_generation, self.current_document
        library_id = self.library_id

        def captured(value):
            if generation != self.navigation_generation or not self.pdf_exporter.available():
                return
            try:
                position = json.loads(value or "null") or {}
                block = next(
                    (b for b in document.blocks if b.id == position.get("source_block")), None
                )
                source = SourceLocation(
                    DocumentRef(library_id, document.path),
                    document.content_hash,
                    fragment=block.fragment if block else (self.history.current.fragment or ""),
                    block_id=block.id if block else "",
                )
                dialog = ShareDialog(self, source)
                dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
                dialog.open()
            except (ValueError, TypeError, KeyError) as exc:
                self.show_error(str(exc))

        self.application_script(
            "JSON.stringify(window.cmvReader && window.cmvReader.capture())", captured
        )

    def ask_share_link(self):
        value, ok = QInputDialog.getText(self, "打开分享链接", "应用链接或浏览器分享链接")
        if ok and value.strip():
            self.open_target(value.strip())

    def open_share_link(self, value):
        settings = self.settings
        parser = self.parser
        generation = self.navigation_generation
        self.share_lookup = getattr(self, "share_lookup", 0) + 1
        lookup = self.share_lookup

        def work(_):
            with ReadOnlyIndex(settings.database, settings.root) as index:
                _, source, _ = resolve_page_link(index, parser, value)
                return source.to_dict()

        def done(source):
            if (
                generation == self.navigation_generation
                and lookup == self.share_lookup
                and settings.root == self.settings.root
            ):
                self.open_source(source)

        self.submit(work, done)

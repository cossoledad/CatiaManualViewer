"""Library and term management adapters. Filesystem and SQLite work stays off the UI thread."""

import uuid
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
)

from catia_manual.config import save_settings
from catia_manual.core.index import ManualIndex
from catia_manual.core.library import attach_copy, snapshot, verify_copy
from catia_manual.core.query import TERMS
from catia_manual.core.terminology import aliases as bundled_aliases


class LibraryDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.busy = False
        self.verification = None
        self.source = None
        self.setWindowTitle("文档库 · Catia Manual Viewer")
        self.resize(740, 560)
        layout = QVBoxLayout(self)
        self.summary = QLabel("正在读取文档库…")
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        form = QFormLayout()
        root_row = QHBoxLayout()
        self.root = QLineEdit(str(window.settings.root))
        self.root.textChanged.connect(self.invalidate)
        root_row.addWidget(self.root, 1)
        self.browse = QPushButton("选择副本目录")
        self.browse.clicked.connect(self.choose_root)
        root_row.addWidget(self.browse)
        form.addRow("待验证目录", root_row)
        layout.addLayout(form)
        row = QHBoxLayout()
        self.verify_button = QPushButton("选择已有索引并验证…")
        self.verify_button.clicked.connect(self.choose_index)
        self.backup_button = QPushButton("导出当前库快照…")
        self.backup_button.clicked.connect(self.backup)
        row.addWidget(self.verify_button)
        row.addWidget(self.backup_button)
        layout.addLayout(row)
        self.details = QTextBrowser()
        layout.addWidget(self.details, 1)
        self.revision = QCheckBox("确认这些差异属于同一文档库的修订，保留库 ID 并增量更新")
        self.revision.toggled.connect(self.update_attach)
        self.revision.setVisible(False)
        layout.addWidget(self.revision)
        self.attach_button = QPushButton("复制并复用索引")
        self.attach_button.setEnabled(False)
        self.root.setEnabled(False)
        self.browse.setEnabled(False)
        self.attach_button.clicked.connect(self.attach)
        layout.addWidget(self.attach_button)
        self.run(self.status_work, self.status_loaded)

    def status_work(self, progress):
        settings = self.window.settings
        with ManualIndex(settings.database, settings.root) as index:
            return index.stats()

    def status_loaded(self, info):
        self.summary.setText(
            f"库 ID：{info['library_id']}\n文章 {info['documents']} "
            f"· 章节块 {info['chunks']} · 文件 {info['files']}\n"
            + (
                "完整清单已就绪"
                if info["manifest_verified"]
                else "请先通过更多菜单完整更新索引，才能验证迁移副本"
            )
        )
        self.details.setPlainText(
            "副本验证会完整读取文档、图片和示例文件。\n"
            "一致副本保留库 ID、译文缓存、阅读位置和用户术语。\n"
            "复用时创建新的索引副本，原数据库保持原有绑定。"
        )

    def run(self, work, done):
        if self.busy:
            return
        self.busy = True
        self.root.setEnabled(False)
        self.browse.setEnabled(False)
        self.verify_button.setEnabled(False)
        self.backup_button.setEnabled(False)
        self.attach_button.setEnabled(False)

        def failed(message):
            self.details.setPlainText(message)

        def progress(value):
            self.details.setPlainText(f"正在校验文件 {value['processed']} / {value['total']}")

        def finished():
            self.busy = False
            self.verify_button.setEnabled(True)
            self.backup_button.setEnabled(True)
            self.root.setEnabled(True)
            self.browse.setEnabled(True)
            self.update_attach()

        def deliver(result):
            try:
                done(result)
            except Exception as exc:
                failed(str(exc))

        self.window.submit(work, deliver, progress=progress, error=failed, finished=finished)

    def invalidate(self):
        self.verification = None
        self.attach_button.setEnabled(False)
        self.revision.setChecked(False)
        self.revision.setVisible(False)

    def choose_root(self):
        root = QFileDialog.getExistingDirectory(
            self, "选择包含 online 的文档副本", self.root.text()
        )
        if root:
            self.root.setText(root)

    def choose_index(self):
        source, _ = QFileDialog.getOpenFileName(
            self,
            "选择要复用的文档索引或快照",
            str(self.window.settings.database.parent),
            "SQLite (*.sqlite3 *.sqlite *.db)",
        )
        if source:
            self.verify_index(Path(source), Path(self.root.text()).expanduser().resolve())

    def verify_index(self, source, root):
        self.invalidate()
        self.source, self.target_root = source, root

        def done(result):
            if root != Path(self.root.text()).expanduser().resolve():
                return
            self.verification = result
            self.revision.setVisible(not result["matched"])
            text = (
                f"副本一致，共 {result['files']} 个文件。"
                if result["matched"]
                else f"副本存在差异：新增 {len(result['added'])}、删除 {len(result['removed'])}、"
                f"修改 {len(result['modified'])}。\n其他版本应作为新库；仅同库修订才保留 ID。"
            )
            for key, label in (("added", "新增"), ("removed", "删除"), ("modified", "修改")):
                if result[key]:
                    text += "\n" + label + "：\n" + "\n".join(result[key][:20])
            self.details.setPlainText(text)

        self.run(lambda progress: verify_copy(source, root, progress), done)

    def update_attach(self):
        enabled = bool(
            self.verification and (self.verification["matched"] or self.revision.isChecked())
        )
        self.attach_button.setEnabled(enabled and not self.busy)

    def attach(self):
        if not self.verification:
            return
        root, source = self.target_root, self.source
        accept_revision = self.revision.isChecked()
        destination = self.window.settings.database.parent / (
            "library-" + uuid.uuid4().hex[:12] + ".sqlite3"
        )

        def done(result):
            updated = save_settings(
                self.window.settings,
                {"documentation": {"root": str(root), "database": str(destination)}},
            )
            self.window.apply_settings(updated)
            self.window.statusBar().showMessage(
                "文档库已复用" + ("，请完整更新索引" if result["requires_update"] else "")
            )
            self.verification = None
            self.details.setPlainText("已使用：" + str(destination))
            self.summary.setText("库 ID：" + result["library_id"])

        self.run(
            lambda progress: attach_copy(
                source, root, destination, accept_revision=accept_revision, progress=progress
            ),
            done,
        )

    def backup(self):
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "导出一致性快照（请选择新文件）",
            str(self.window.settings.database.parent / "library-snapshot.sqlite3"),
            "SQLite (*.sqlite3)",
        )
        if destination:
            database = self.window.settings.database
            self.run(
                lambda _: snapshot(database, Path(destination)),
                lambda result: self.details.setPlainText("快照已保存：" + str(result)),
            )

    def reject(self):
        if not self.busy:
            super().reject()

    def closeEvent(self, event):
        event.ignore() if self.busy else event.accept()


class TermsDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window, self.busy = window, False
        self.setWindowTitle("术语与别名 · Catia Manual Viewer")
        self.resize(700, 600)
        layout = QVBoxLayout(self)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("筛选中文、英文或来源")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self.filter_rows)
        layout.addWidget(self.filter)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["别名", "英文关键词", "范围", "来源"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemSelectionChanged.connect(self.selected)
        layout.addWidget(self.table, 1)
        form = QFormLayout()
        self.alias, self.english = QLineEdit(), QLineEdit()
        form.addRow("术语别名", self.alias)
        form.addRow("英文关键词（同义词用 | 分隔）", self.english)
        self.scope = QComboBox()
        self.scope.addItem("全部功能", "")
        for manual in window.catalog.manuals.values():
            self.scope.addItem(manual.label, manual.id)
        form.addRow("作用范围", self.scope)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        self.save = QPushButton("保存用户别名")
        self.save.clicked.connect(self.store)
        self.remove = QPushButton("移除用户覆盖")
        self.remove.clicked.connect(self.delete)
        buttons.addWidget(self.save)
        buttons.addWidget(self.remove)
        layout.addLayout(buttons)
        self.note = QLabel()
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.note)
        self.reload()

    def work(self, function, done):
        if self.busy:
            return
        self.busy = True
        self.save.setEnabled(False)
        self.remove.setEnabled(False)
        settings = self.window.settings

        def run(_):
            with ManualIndex(settings.database, settings.root) as index:
                function(index)
                return [
                    dict(row)
                    for row in index.connection.execute(
                        "SELECT * FROM term_aliases ORDER BY alias,module"
                    )
                ]

        def finished():
            self.busy = False
            self.save.setEnabled(True)
            self.remove.setEnabled(True)

        self.window.submit(run, done, error=self.note.setText, finished=finished)

    def reload(self):
        self.work(lambda _: None, self.populate)

    def populate(self, users):
        self.table.setRowCount(0)
        rows = [(alias, " | ".join(english), "", "内置") for alias, english in TERMS.items()]
        rows += [
            (alias, " | ".join(english), "", "随包命令词库")
            for alias, english in bundled_aliases().items()
            if alias not in TERMS
        ]
        rows += [(row["alias"], row["english"], row["module"], "用户") for row in users]
        for alias, english, scope, origin in sorted(rows):
            row = self.table.rowCount()
            self.table.insertRow(row)
            for column, value in enumerate((alias, english, scope or "全部", origin)):
                self.table.setItem(row, column, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        self.filter_rows()
        self.note.setText(
            "用户别名实时用于查询展开，不需重建索引。限定范围也适用于包含该手册的分类。"
        )

    def filter_rows(self):
        text = self.filter.text().strip().casefold()
        for row in range(self.table.rowCount()):
            content = " ".join(self.table.item(row, column).text() for column in range(4))
            self.table.setRowHidden(row, text not in content.casefold())

    def selected(self):
        row = self.table.currentRow()
        if row < 0 or not self.table.item(row, 0):
            return
        self.alias.setText(self.table.item(row, 0).text())
        self.english.setText(self.table.item(row, 1).text())
        scope = self.table.item(row, 2).text()
        self.scope.setCurrentIndex(max(0, self.scope.findData("" if scope == "全部" else scope)))

    def store(self):
        alias, english, scope = self.alias.text(), self.english.text(), self.scope.currentData()
        self.work(lambda index: index.set_alias(alias, english, scope), self.populate)

    def delete(self):
        alias, scope = self.alias.text(), self.scope.currentData()
        self.work(lambda index: index.delete_alias(alias, scope), self.populate)

    def reject(self):
        if not self.busy:
            super().reject()

    def closeEvent(self, event):
        event.ignore() if self.busy else event.accept()

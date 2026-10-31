"""Export/import adapters; all archive, document and database work runs off the UI thread."""

import shutil
import threading
import tomllib
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from catia_manual import __version__
from catia_manual.config import save_settings
from catia_manual.core.transfer import (
    activation_changes,
    export_bundle,
    import_bundle,
    import_directory,
    inspect_bundle,
)


class TransferDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window, self.busy, self.cancelled = window, False, threading.Event()
        self.archive = self.manifest = None
        self.setWindowTitle("数据迁移 · Catia Manual Viewer")
        self.resize(620, 390)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        export = QWidget()
        rows = QVBoxLayout(export)
        rows.addWidget(QLabel("设置、索引、译文缓存、阅读位置与图布局"))
        self.documents = QCheckBox("包含文档副本")
        self.documents.setChecked(True)
        rows.addWidget(self.documents)
        self.models = QCheckBox("包含本机模型")
        self.models.setChecked(True)
        rows.addWidget(self.models)
        note = QLabel("API Key 不导出；CATIA 使用目标电脑的配置。")
        note.setObjectName("muted")
        rows.addWidget(note)
        rows.addStretch()
        self.export_button = QPushButton("导出迁移包…")
        self.export_button.setObjectName("primary")
        self.export_button.clicked.connect(self.choose_export)
        rows.addWidget(self.export_button)
        self.tabs.addTab(export, "导出")
        importing = QWidget()
        rows = QVBoxLayout(importing)
        row = QHBoxLayout()
        self.filename = QLineEdit()
        self.filename.setReadOnly(True)
        self.filename.setPlaceholderText("迁移包 .zip")
        row.addWidget(self.filename, 1)
        self.browse_button = QPushButton("选择…")
        self.browse_button.clicked.connect(self.choose_import)
        row.addWidget(self.browse_button)
        rows.addLayout(row)
        self.summary = QLabel("")
        rows.addWidget(self.summary)
        form = QFormLayout()
        row = QHBoxLayout()
        self.root = QLineEdit(str(window.settings.root))
        row.addWidget(self.root, 1)
        self.root_button = QPushButton("浏览")
        self.root_button.clicked.connect(self.choose_root)
        row.addWidget(self.root_button)
        form.addRow("文档副本", row)
        rows.addLayout(form)
        rows.addStretch()
        self.import_button = QPushButton("导入并使用")
        self.import_button.setObjectName("primary")
        self.import_button.setEnabled(False)
        self.import_button.clicked.connect(self.start_import)
        rows.addWidget(self.import_button)
        self.tabs.addTab(importing, "导入")
        self.progress = QProgressBar()
        self.progress.hide()
        layout.addWidget(self.progress)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.reject)
        layout.addWidget(self.close_button)

    def set_busy(self, busy):
        self.busy = busy
        self.tabs.setEnabled(not busy)
        self.close_button.setText("取消" if busy else "关闭")
        self.progress.setVisible(busy)
        self.progress.setRange(0, 0)
        if busy:
            self.cancelled.clear()

    def run(self, work, done, *, wait_for_saves=False):
        if self.busy:
            return
        self.set_busy(True)
        self.status.setText("等待后台保存…" if wait_for_saves else "正在读取…")

        def progress(value):
            self.progress.setRange(0, value.get("total", 0))
            self.progress.setValue(value.get("processed", 0))

        def report(value):
            if self.cancelled.is_set():
                raise ValueError("操作已取消")
            return value

        def execute(emit):
            def update(value):
                report(None)
                emit(value)

            report(None)
            return work(update)

        def failed(message):
            self.status.setText(message)

        def deliver(value):
            try:
                done(value)
            except (ValueError, OSError) as exc:
                failed(str(exc))

        def start():
            if self.cancelled.is_set():
                self.set_busy(False)
                self.status.setText("操作已取消")
            elif wait_for_saves and self.window.jobs:
                QTimer.singleShot(80, start)
            else:
                self.status.setText("处理中…")
                self.window.submit(
                    execute,
                    deliver,
                    progress=progress,
                    error=failed,
                    finished=lambda: self.set_busy(False),
                )

        QTimer.singleShot(100, start)

    def choose_export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出迁移包", f"CatiaManualViewer-{__version__}-data.zip", "迁移包 (*.zip)"
        )
        if path:
            self.export_to(Path(path))

    def export_to(self, path):
        self.window.flush_preferences()
        self.window.save_map_location()
        self.window.flush_map_state()
        self.window.flush_reading_locations()
        settings = self.window.settings
        documents, models = self.documents.isChecked(), self.models.isChecked()
        self.run(
            lambda progress: export_bundle(
                settings,
                path,
                include_documents=documents,
                include_models=models,
                progress=progress,
                cancelled=self.cancelled.is_set,
            ),
            lambda value: self.status.setText("已导出：" + value["archive"]),
            wait_for_saves=True,
        )

    def choose_import(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择迁移包", "", "迁移包 (*.zip)")
        if path:
            self.inspect(Path(path))

    def inspect(self, path):
        self.archive = self.manifest = None
        self.import_button.setEnabled(False)

        def done(value):
            self.archive, self.manifest = path, value
            self.filename.setText(str(path))
            self.filename.setCursorPosition(0)
            self.filename.setToolTip(str(path))
            self.summary.setText(
                f"v{value['app_version']} · {len(value['files'])} 个文件 · "
                f"{value['total_bytes'] / 1024**2:.0f} MB"
            )
            self.root.setEnabled(not value["documents"])
            self.root_button.setEnabled(not value["documents"])
            self.root.setText(
                "随迁移包导入" if value["documents"] else str(self.window.settings.root)
            )
            self.root.setCursorPosition(0)
            self.import_button.setEnabled(True)
            self.status.clear()

        self.run(lambda _: inspect_bundle(path), done)

    def choose_root(self):
        path = QFileDialog.getExistingDirectory(self, "选择文档副本", self.root.text())
        if path:
            self.root.setText(path)

    def start_import(self):
        if not self.manifest or not self.archive:
            return
        destination = import_directory(self.window.settings.database.parent)
        root = None if self.manifest["documents"] else Path(self.root.text()).expanduser().resolve()

        def activate(value):
            changes = tomllib.loads(Path(value["config"]).read_text("utf-8"))
            current = self.window.settings
            changes = activation_changes(current, changes)
            if current.config_path and current.config_path.is_file():
                shutil.copy2(current.config_path, destination / "previous-config.toml")
            settings = save_settings(current, changes)
            self.window.apply_settings(settings)
            self.import_button.setEnabled(False)
            self.status.setText("迁移完成，已使用导入的数据。")

        self.window.save_map_location()
        self.window.flush_map_state()
        self.window.flush_reading_locations()
        archive = self.archive
        self.run(
            lambda progress: import_bundle(
                archive, destination, root=root, progress=progress, cancelled=self.cancelled.is_set
            ),
            activate,
            wait_for_saves=True,
        )

    def reject(self):
        if self.busy:
            self.cancelled.set()
            self.status.setText("正在取消…")
        else:
            super().reject()

    def closeEvent(self, event):
        if self.busy:
            self.reject()
            event.ignore()
        else:
            event.accept()

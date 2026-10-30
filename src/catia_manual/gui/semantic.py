"""Desktop controls for explicit model installation and resumable semantic indexing."""

import threading
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from catia_manual.config import save_settings
from catia_manual.core.embedding import (
    FINGERPRINT,
    Cancelled,
    download_model,
    make_embedder,
    model_status,
)
from catia_manual.core.index import ManualIndex
from catia_manual.core.semantic import PASSAGE_VERSION, SemanticIndex


class SemanticDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window, self.busy, self.closing = window, False, False
        self.cancel = threading.Event()
        self.setWindowTitle("智能搜索 · Catia Manual Viewer")
        self.resize(660, 370)
        layout = QVBoxLayout(self)
        intro = QLabel("中文描述也能检索英文手册。模型和索引在本机运行，搜索文本不发送到在线服务。")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        model = QLabel("Multilingual E5 Small · CPU · 约 135 MB · MIT")
        layout.addWidget(model)
        row = QHBoxLayout()
        self.directory = QLineEdit(
            window.settings.search.get("model_dir", "data/search-models/e5-small")
        )
        self.directory.setCursorPosition(0)
        self.directory.editingFinished.connect(self.refresh)
        self.browse = QPushButton("选择模型目录")
        self.browse.clicked.connect(self.choose_directory)
        row.addWidget(self.directory, 1)
        row.addWidget(self.browse)
        layout.addLayout(row)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.summary)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        row = QHBoxLayout()
        self.download = QPushButton("下载／继续下载模型")
        self.download.clicked.connect(self.install)
        self.build = QPushButton("建立／继续语义索引")
        self.build.clicked.connect(self.index)
        self.pause = QPushButton("暂停")
        self.pause.clicked.connect(self.cancel.set)
        self.pause.setEnabled(False)
        for button in (self.download, self.build, self.pause):
            row.addWidget(button)
        layout.addLayout(row)
        self.activate = QPushButton("启用混合搜索")
        self.activate.clicked.connect(self.enable)
        layout.addWidget(self.activate)
        self.note = QLabel("首次需下载模型并建立索引；中断后可继续，已完成内容会保留。")
        self.note.setWordWrap(True)
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.note)
        layout.addStretch()
        self.refresh()

    def config(self):
        base = self.window.settings.config_path
        path = Path(self.directory.text().strip()).expanduser()
        if not path.is_absolute():
            path = (base.parent if base else Path.cwd()) / path
        return {**self.window.settings.search, "model_dir": str(path.resolve())}

    def choose_directory(self):
        selected = QFileDialog.getExistingDirectory(
            self, "选择离线语义模型目录", self.directory.text()
        )
        if selected:
            self.directory.setText(selected)
            self.refresh()

    def refresh(self):
        if self.busy:
            return
        config, settings = self.config(), self.window.settings

        def work(_):
            with ManualIndex(settings.database, settings.root) as index:
                return model_status(config["model_dir"]), SemanticIndex(index).status(
                    FINGERPRINT + ":" + PASSAGE_VERSION
                )

        def done(value):
            model, status = value
            states = {
                "ready": "已完成",
                "absent": "尚未建立",
                "paused": "已暂停",
                "failed": "可重试",
                "building": "可继续",
                "stale": "文档已变化，请更新",
            }
            self.summary.setText(
                model["reason"]
                + "\n语义索引："
                + states.get(status["state"], status["state"])
                + f" · {status['valid']} 个有效片段"
            )
            self.activate.setEnabled(model["ready"] and status["valid"] > 0)
            self.build.setEnabled(model["ready"])
            total = status["total"]
            self.progress.setValue(int(100 * status["processed"] / total) if total else 0)

        self.run(work, done, refresh_after=False)

    def run(self, work, done, *, refresh_after=True):
        if self.busy:
            return
        self.busy = True
        self.cancel.clear()
        self.window.semantic_cancel = self.cancel
        for widget in (self.directory, self.browse, self.download, self.build, self.activate):
            widget.setEnabled(False)
        self.pause.setEnabled(True)
        self.progress.setValue(0)

        def update(value):
            total = value.get("total", 0)
            self.progress.setValue(int(100 * value["processed"] / total) if total else 0)
            self.note.setText(f"已处理 {value['processed']:,} / {total:,}")

        def finished():
            self.busy = False
            self.window.semantic_cancel = None
            self.pause.setEnabled(False)
            for widget in (self.directory, self.browse, self.download):
                widget.setEnabled(True)
            if self.closing:
                super(SemanticDialog, self).reject()
            elif refresh_after:
                self.refresh()

        def failed(message):
            self.note.setText(message)
            self.window.statusBar().publish("semantic", message, record=True)

        def delivered(result):
            try:
                done(result)
            except Exception as exc:
                failed(str(exc))

        self.window.submit(work, delivered, progress=update, finished=finished, error=failed)

    def install(self):
        config = self.config()
        self.note.setText("正在从 Hugging Face 下载固定修订，校验后使用…")

        def work(progress):
            try:
                return download_model(config["model_dir"], progress=progress, cancel=self.cancel)
            except Cancelled:
                return {"paused": True}

        self.run(
            work,
            lambda result: self.note.setText(
                "下载已暂停，可继续"
                if result.get("paused")
                else "模型已通过完整校验，可建立语义索引"
            ),
        )

    def index(self):
        config, settings = self.config(), self.window.settings
        self.note.setText("正在准备模型及文章片段…")

        def work(progress):
            with ManualIndex(settings.database, settings.root) as index:
                return SemanticIndex(index).build(
                    make_embedder(config), progress=progress, cancel=self.cancel
                )

        def done(result):
            self.note.setText(
                ("已完成" if result["state"] == "ready" else "已暂停，可继续")
                + f" · 新生成 {result['embedded']} · 复用 {result['reused']}"
            )
            self.window.settings = save_settings(settings, {"search": config})

        self.run(work, done)

    def enable(self):
        try:
            self.window.settings = save_settings(
                self.window.settings, {"search": {**self.config(), "mode": "hybrid"}}
            )
        except OSError as exc:
            self.note.setText(str(exc))
            return
        self.note.setText("已启用混合搜索，可直接输入中文描述。搜索模式也可在全局设置中切换。")
        self.window.statusBar().publish("search", "已启用本机混合搜索", record=True)
        if self.window.query.text().strip():
            self.window.search()

    def reject(self):
        if self.busy:
            self.closing = True
            self.cancel.set()
        else:
            super().reject()

    def closeEvent(self, event):
        if self.busy:
            self.closing = True
            self.cancel.set()
            event.ignore()
        else:
            event.accept()

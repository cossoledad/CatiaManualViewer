from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from catia_manual.core.credentials import set_session_key
from catia_manual.core.providers import PROVIDERS, provider_profile


class TranslationSettings(QDialog):
    def __init__(self, config: dict, parent=None, embedded=False):
        super().__init__(parent)
        self._base_config = deepcopy(config)
        self._profiles = deepcopy(config.get("profiles", {}))
        self._session_values = {}
        self._current_provider = config.get("provider", "local")
        profile = provider_profile(config, self._current_provider)
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.setWindowTitle("翻译服务与划词设置")
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.form = form
        self.provider = QComboBox()
        for name, preset in PROVIDERS.items():
            self.provider.addItem(preset["label"], name)
        self.provider.setCurrentIndex(
            max(0, self.provider.findData(config.get("provider", "local")))
        )
        form.addRow("翻译服务", self.provider)
        self.endpoint = QLineEdit(profile.get("endpoint", ""))
        self.endpoint.setPlaceholderText("https://服务地址/v1 或完整翻译接口地址")
        form.addRow("服务地址", self.endpoint)
        self.model = QLineEdit(profile.get("model", ""))
        self.model.setPlaceholderText("服务提供的模型名称")
        form.addRow("模型名称", self.model)
        self.key_env = QLineEdit(profile.get("api_key_env", "CATIA_TRANSLATION_API_KEY"))
        form.addRow("API Key 环境变量名", self.key_env)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("仅本次运行；留空则使用环境变量中的 Key")
        form.addRow("API Key", self.key)
        self.automatic = QCheckBox("选中单词或句子后自动翻译")
        self.automatic.setChecked(config.get("automatic", True))
        form.addRow(self.automatic)
        self.delay = QSpinBox()
        self.delay.setRange(150, 2000)
        self.delay.setSuffix(" ms")
        self.delay.setValue(int(config.get("selection_delay_ms", 350)))
        form.addRow("选区稳定等待", self.delay)
        self.max_chars = QSpinBox()
        self.max_chars.setRange(100, 10000)
        self.max_chars.setValue(int(config.get("max_selection_chars", 3000)))
        form.addRow("单次自动翻译字符上限", self.max_chars)
        self.provider.currentIndexChanged.connect(self.provider_changed)
        self.update_fields()
        layout.addLayout(form)
        note = QLabel("在线源会接收所选文字。密钥仅用于本次运行。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.test_button = QPushButton("测试当前服务")
        self.test_button.clicked.connect(self.test_service)
        layout.addWidget(self.test_button)
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        self.test_result.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.test_result)
        self.jobs = set()
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        layout.addWidget(self.buttons)
        if embedded:
            self.buttons.hide()

    def remember_profile(self):
        if self.key.text():
            self._session_values[self.key_env.text().strip()] = self.key.text()
        else:
            self._session_values.pop(self.key_env.text().strip(), None)
        self._profiles[self._current_provider] = {
            "endpoint": self.endpoint.text().strip(),
            "model": self.model.text().strip(),
            "api_key_env": self.key_env.text().strip(),
        }

    def provider_changed(self):
        self.remember_profile()
        self._current_provider = self.provider.currentData()
        profile = provider_profile({"profiles": self._profiles}, self._current_provider)
        self.endpoint.setText(profile.get("endpoint", ""))
        self.model.setText(profile.get("model", ""))
        self.key_env.setText(profile.get("api_key_env", "CATIA_TRANSLATION_API_KEY"))
        self.key.clear()
        self.key.setText(self._session_values.get(self.key_env.text().strip(), ""))
        self.test_result.clear()
        self.update_fields()

    def update_fields(self):
        remote = self.provider.currentData() not in {"local", "glossary"}
        self.form.setRowVisible(self.endpoint, remote)
        self.form.setRowVisible(self.key_env, remote)
        self.form.setRowVisible(self.key, remote)
        self.form.setRowVisible(self.model, self.provider.currentData() == "chat-completions")
        self.endpoint.setEnabled(remote)
        self.key_env.setEnabled(remote)
        self.key.setEnabled(remote)
        self.model.setEnabled(self.provider.currentData() == "chat-completions")

    def values(self) -> dict:
        self.remember_profile()
        return {
            "provider": self.provider.currentData(),
            "endpoint": self.endpoint.text().strip(),
            "api_key_env": self.key_env.text().strip(),
            "model": self.model.text().strip(),
            "automatic": self.automatic.isChecked(),
            "selection_delay_ms": self.delay.value(),
            "max_selection_chars": self.max_chars.value(),
            "target_language": "zh-CN",
            "profiles": deepcopy(self._profiles),
        }

    def apply_session_key(self):
        self.remember_profile()
        for variable, value in self._session_values.items():
            if not variable or "=" in variable or "\x00" in variable:
                raise ValueError("API Key 环境变量名无效")
            set_session_key(variable, value)

    def test_service(self):
        from catia_manual.core.translation import make_provider
        from catia_manual.gui.app import Job

        try:
            config = {**self._base_config, **self.values()}
            config["api_key_override"] = self.key.text()
            provider = make_provider(config)
        except Exception as exc:
            self.test_result.setText(str(exc))
            return
        self.test_button.setEnabled(False)
        self.buttons.setEnabled(False)
        for widget in (self.provider, self.endpoint, self.model, self.key_env, self.key):
            widget.setEnabled(False)
        self.test_result.setText("正在测试…")
        job = Job(lambda _: provider.translate("Select the sketch and click Pad.", "zh-CN"))
        self.jobs.add(job)
        job.signals.result.connect(lambda result: self.test_result.setText("服务可用：" + result))
        job.signals.error.connect(self.test_result.setText)
        job.signals.finished.connect(lambda: self.jobs.discard(job))
        job.signals.finished.connect(lambda: self.test_button.setEnabled(True))
        job.signals.finished.connect(lambda: self.buttons.setEnabled(True))
        job.signals.finished.connect(lambda: self.provider.setEnabled(True))
        job.signals.finished.connect(self.update_fields)
        QThreadPool.globalInstance().start(job)

    def closeEvent(self, event):
        if self.jobs:
            self.test_result.setText("服务测试尚未结束，请稍候关闭。")
            event.ignore()
        else:
            event.accept()

    def reject(self):
        if not self.jobs:
            super().reject()


class GlobalSettings(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.original = settings
        self.setWindowTitle("设置 · Catia Manual Viewer")
        self.resize(700, 650)
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        general = QWidget()
        form = QFormLayout(general)
        form.setSpacing(16)
        self.root = self.path_field(form, "文档根目录", str(settings.root))
        self.data_dir = self.path_field(form, "索引与缓存目录", str(settings.database.parent))
        self.model_dir = self.path_field(
            form, "离线模型目录", settings.translation.get("model_dir", "data/models")
        )
        from PySide6.QtWidgets import QGroupBox

        catia = QGroupBox("CATIA（可选，仅用于打开示例）")
        catia_form = QFormLayout(catia)
        self.launcher = self.path_field(catia_form, "启动器", str(settings.launcher), file=True)
        self.environment_dir = self.path_field(
            catia_form, "环境目录", str(settings.environment_dir)
        )
        self.environment = QLineEdit(settings.environment)
        catia_form.addRow("环境名称", self.environment)
        form.addRow(catia)
        hint = QLabel("文档目录应包含 online 文件夹。仅阅读文档时无需安装 CATIA。")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        form.addRow(hint)
        self.tabs.addTab(general, "文档与 CATIA")
        self.translation = TranslationSettings(settings.translation, parent, embedded=True)
        self.tabs.addTab(self.translation, "翻译")
        appearance = QWidget()
        visual = QFormLayout(appearance)
        visual.setSpacing(18)
        self.font_size = QSpinBox()
        self.font_size.setRange(9, 16)
        self.font_size.setValue(int(settings.appearance.get("font_size", 10)))
        visual.addRow("界面字号", self.font_size)
        self.zoom = QSpinBox()
        self.zoom.setRange(70, 200)
        self.zoom.setSingleStep(10)
        self.zoom.setSuffix(" %")
        self.zoom.setValue(int(settings.appearance.get("reader_zoom", 100)))
        visual.addRow("阅读缩放", self.zoom)
        self.reading_mode = QComboBox()
        for label, value in (("原文", "original"), ("双语", "bilingual"), ("中文", "chinese")):
            self.reading_mode.addItem(label, value)
        self.reading_mode.setCurrentIndex(
            max(0, self.reading_mode.findData(settings.appearance.get("reading_mode", "original")))
        )
        visual.addRow("阅读模式（跨页保留）", self.reading_mode)
        self.tools = QCheckBox("显示翻译与示例侧栏")
        self.tools.setChecked(settings.appearance.get("show_tools", True))
        visual.addRow(self.tools)
        self.remember_window = QCheckBox("记住窗口大小")
        self.remember_window.setChecked(settings.appearance.get("remember_window", True))
        visual.addRow(self.remember_window)
        self.tabs.addTab(appearance, "阅读与界面")
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color:#b14747")
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        layout.addWidget(buttons)

    def path_field(self, form, label, value, file=False):
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        field = QLineEdit(value)
        field.setCursorPosition(0)
        field.setToolTip(value)
        button = QPushButton("浏览")
        button.setFixedWidth(64)

        def choose():
            selected = (
                QFileDialog.getOpenFileName(
                    self, label, field.text(), "程序 (*.exe);;所有文件 (*)"
                )[0]
                if file
                else QFileDialog.getExistingDirectory(self, label, field.text())
            )
            if selected:
                field.setText(selected)
                field.setCursorPosition(0)

        button.clicked.connect(choose)
        row.addWidget(field, 1)
        row.addWidget(button)
        form.addRow(label, widget)
        return field

    def changes(self):
        return {
            "documentation": {
                "root": self.root.text().strip(),
                "database": str(Path(self.data_dir.text()) / self.original.database.name),
            },
            "catia": {
                "launcher": self.launcher.text().strip(),
                "environment": self.environment.text().strip(),
                "environment_dir": self.environment_dir.text().strip(),
            },
            "translation": {
                **self.translation.values(),
                "model_dir": self.model_dir.text().strip(),
            },
            "appearance": {
                "font_size": self.font_size.value(),
                "reader_zoom": self.zoom.value(),
                "reading_mode": self.reading_mode.currentData(),
                "show_tools": self.tools.isChecked(),
                "remember_window": self.remember_window.isChecked(),
            },
        }

    def accept(self):
        if self.translation.jobs:
            self.note.setText("翻译测试尚未结束。")
            return
        base = self.original.config_path.parent if self.original.config_path else Path.cwd()
        root = Path(self.root.text().strip()).expanduser()
        if not root.is_absolute():
            root = base / root
        if not (root / "online").is_dir() and (root / "English/online").is_dir():
            root /= "English"
        if not (root / "online").is_dir():
            self.tabs.setCurrentIndex(0)
            self.note.setText("请选择包含 online 文件夹的文档目录。")
            return
        if not self.data_dir.text().strip() or not self.model_dir.text().strip():
            self.note.setText("请选择数据与模型目录。")
            return
        self.root.setText(str(root.resolve()))
        try:
            import tempfile

            directory = Path(self.data_dir.text().strip()).expanduser().resolve()
            if not Path(self.data_dir.text().strip()).expanduser().is_absolute():
                directory = (base / self.data_dir.text().strip()).resolve()
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=directory):
                pass
        except OSError:
            self.note.setText("索引与缓存目录不可写，请选择其他目录。")
            return
        self.data_dir.setText(str(directory))
        super().accept()

    def reject(self):
        if not self.translation.jobs:
            super().reject()

    def closeEvent(self, event):
        if self.translation.jobs:
            event.ignore()
        else:
            event.accept()

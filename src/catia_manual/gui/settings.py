from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from catia_manual.core.credentials import set_session_key


class TranslationSettings(QDialog):
    def __init__(self, config: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("翻译服务与划词设置")
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.provider = QComboBox()
        for label, name in (
            ("大模型 API（OpenAI 兼容）", "chat-completions"),
            ("本机离线翻译（英中互译）", "local"),
            ("LibreTranslate 服务", "libretranslate"),
            ("仅术语辅助", "glossary"),
        ):
            self.provider.addItem(label, name)
        self.provider.setCurrentIndex(
            max(0, self.provider.findData(config.get("provider", "local")))
        )
        form.addRow("翻译服务", self.provider)
        self.endpoint = QLineEdit(config.get("endpoint", ""))
        self.endpoint.setPlaceholderText("https://服务地址/v1 或完整翻译接口地址")
        form.addRow("服务地址", self.endpoint)
        self.model = QLineEdit(config.get("model", ""))
        self.model.setPlaceholderText("服务提供的模型名称")
        form.addRow("模型名称", self.model)
        self.key_env = QLineEdit(config.get("api_key_env", "CATIA_TRANSLATION_API_KEY"))
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
        self.provider.currentIndexChanged.connect(self.update_fields)
        self.update_fields()
        layout.addLayout(form)
        note = QLabel(
            "本机模式需要已安装 data/models 中的模型。\n"
            "API 模式会将选中文字和需要翻译的中文查询发送到所填服务。\n"
            "输入的 API Key 仅在当前进程中使用，不写入项目配置。"
        )
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
        layout.addWidget(self.buttons)

    def update_fields(self):
        remote = self.provider.currentData() in {"libretranslate", "chat-completions"}
        self.endpoint.setEnabled(remote)
        self.key_env.setEnabled(remote)
        self.key.setEnabled(remote)
        self.model.setEnabled(self.provider.currentData() == "chat-completions")

    def values(self) -> dict:
        return {
            "provider": self.provider.currentData(),
            "endpoint": self.endpoint.text().strip(),
            "api_key_env": self.key_env.text().strip(),
            "model": self.model.text().strip(),
            "automatic": self.automatic.isChecked(),
            "selection_delay_ms": self.delay.value(),
            "max_selection_chars": self.max_chars.value(),
            "target_language": "zh-CN",
        }

    def apply_session_key(self):
        if self.key.text():
            variable = self.key_env.text().strip()
            if not variable or "=" in variable or "\x00" in variable:
                raise ValueError("API Key 环境变量名无效")
            set_session_key(variable, self.key.text())

    def test_service(self):
        from catia_manual.core.translation import make_provider
        from catia_manual.gui.app import Job

        try:
            self.apply_session_key()
            config = {**self.parent().settings.translation, **self.values()}
            provider = make_provider(config)
        except Exception as exc:
            self.test_result.setText(str(exc))
            return
        self.test_button.setEnabled(False)
        self.buttons.setEnabled(False)
        self.test_result.setText("正在测试…")
        job = Job(lambda _: provider.translate("Select the sketch and click Pad.", "zh-CN"))
        self.jobs.add(job)
        job.signals.result.connect(lambda result: self.test_result.setText("服务可用：" + result))
        job.signals.error.connect(self.test_result.setText)
        job.signals.finished.connect(lambda: self.jobs.discard(job))
        job.signals.finished.connect(lambda: self.test_button.setEnabled(True))
        job.signals.finished.connect(lambda: self.buttons.setEnabled(True))
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

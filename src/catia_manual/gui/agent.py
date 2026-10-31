"""Viewer-owned backend process and compact LAN connection settings."""

import ipaddress
import json
import os
import secrets
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal
from PySide6.QtNetwork import QAbstractSocket, QNetworkInterface
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from catia_manual.agent.options import codex_config, options, valid_token
from catia_manual.config import save_settings
from catia_manual.core.credentials import protect_key, unprotect_key
from catia_manual.core.readonly_index import ReadOnlyIndex
from catia_manual.runtime import is_compiled


def local_addresses():
    addresses = {"127.0.0.1"}
    for interface in QNetworkInterface.allInterfaces():
        if not interface.flags() & QNetworkInterface.InterfaceFlag.IsUp:
            continue
        for entry in interface.addressEntries():
            address = entry.ip()
            if address.protocol() == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol:
                value = address.toString()
                if ipaddress.IPv4Address(value).is_private:
                    addresses.add(value)
    return sorted(addresses, key=lambda s: (s == "127.0.0.1", s))


class AgentController(QObject):
    changed = Signal()

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.read_output)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.process_error)
        self.timer = QTimer(self)
        self.timer.setInterval(300)
        self.timer.timeout.connect(self.tick)
        self.state, self.message, self.runtime = "stopped", "未启动", None
        self.started_at = 0
        self.output = ""
        self.closing = False

    @property
    def active(self):
        return self.process.state() != QProcess.ProcessState.NotRunning

    def save(self, changes):
        self.window.flush_preferences()
        self.window.settings = save_settings(
            self.window.settings, {"agent": {**self.window.settings.agent, **changes}}
        )

    def token(self, create=False):
        config = options(self.window.settings.agent)
        try:
            token = unprotect_key(config["token_encrypted"])
            token = valid_token(token) if token else ""
        except ValueError:
            token = ""
        if not token and create:
            token = secrets.token_urlsafe(32)
            self.save({"token_encrypted": protect_key(token)})
        return token

    def start(self, *, automatic=False):
        if self.active:
            return
        try:
            profile = options(self.window.settings.agent)
            with ReadOnlyIndex(self.window.settings.database, self.window.settings.root) as index:
                library_id = index.library_id
            if automatic and profile["library_id"] != library_id:
                raise ValueError("文档库尚未授权，请在 Agent 接入中启动服务")
            if profile["library_id"] and profile["library_id"] != library_id:
                self.save({"token_encrypted": ""})
            self.save({"library_id": library_id})
            token = self.token(create=True) if profile["require_token"] else ""
            config = self.window.settings.config_path
            if config is None:
                raise ValueError("请先保存软件设置")
            identity = secrets.token_hex(12)
            directory = Path(tempfile.gettempdir()) / "CatiaManualViewer-agent"
            directory.mkdir(parents=True, exist_ok=True)
            self.runtime = directory / (identity + ".json")
            self.runtime.with_suffix(".lease").write_text(identity, "ascii")
            self.identity = identity
            self.expected_library = library_id
            self.expected_port = profile["port"]
            env = QProcessEnvironment.systemEnvironment()
            env.remove("CMV_AGENT_BOOT_TOKEN")
            if token:
                env.insert("CMV_AGENT_BOOT_TOKEN", token)
            env.insert("CMV_AGENT_INSTANCE", identity)
            env.insert("PYTHONUTF8", "1")
            self.process.setProcessEnvironment(env)
            arguments = ["--config", str(config), "--runtime", str(self.runtime)]
            self.process.setProgram(sys.executable)
            self.process.setArguments(
                ["--agent-service", *arguments]
                if is_compiled()
                else ["-m", "catia_manual.agent.server", *arguments]
            )
            self.output = ""
            self.state, self.message = "starting", "正在启动…"
            self.started_at = time.monotonic()
            self.process.start()
            self.timer.start()
        except sqlite3.Error:
            self.state, self.message = "stopped", "请先完成文档库索引"
        except (OSError, ValueError) as exc:
            self.state, self.message = "stopped", str(exc)
        self.changed.emit()

    def read_output(self):
        self.output = (
            self.output
            + bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace")
        )[-8000:]

    def process_error(self, _):
        if self.process.state() == QProcess.ProcessState.NotRunning:
            self.message = "服务未能启动，请检查 Agent 依赖和监听设置"
            self.finished(-1, None)

    def tick(self):
        if not self.active:
            return
        if self.state != "stopping" and self.runtime:
            try:
                os.utime(self.runtime.with_suffix(".lease"), None)
                if self.state == "starting" and self.runtime.is_file():
                    data = json.loads(self.runtime.read_text("utf-8"))
                    if (
                        data.get("instance") == self.identity
                        and data.get("library_id") == self.expected_library
                        and data.get("port") == self.expected_port
                        and data.get("state") == "ready"
                    ):
                        self.state, self.message = "running", "服务运行中"
                        self.changed.emit()
            except (OSError, ValueError):
                pass
            if self.state == "starting" and time.monotonic() - self.started_at > 15:
                self.stop("服务启动超时，请检查端口占用与依赖")
        elif time.monotonic() - self.started_at > 5:
            self.process.kill()

    def stop(self, message="服务已停止"):
        self.message = message
        if self.runtime:
            self.runtime.with_suffix(".lease").unlink(missing_ok=True)
        if self.active:
            self.state = "stopping"
            self.started_at = time.monotonic()
            self.timer.start()
        else:
            self.state = "stopped"
        self.changed.emit()

    def finished(self, exit_code, _):
        was_stopping = self.state == "stopping"
        self.state = "stopped"
        self.timer.stop()
        if self.runtime:
            self.runtime.unlink(missing_ok=True)
            self.runtime.with_suffix(".lease").unlink(missing_ok=True)
        if not was_stopping and exit_code:
            self.message = "服务启动失败：请检查端口占用、索引或 Agent 依赖"
        elif not was_stopping:
            self.message = "服务已停止"
        self.changed.emit()


class AgentDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window, self.controller = window, window.agent_controller
        self.setWindowTitle("Agent 接入 · Catia Manual Viewer")
        self.resize(650, 520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.host = QComboBox()
        self.host.addItem("仅本机", "127.0.0.1")
        self.host.addItem("局域网", "0.0.0.0")
        profile = options(window.settings.agent)
        if profile["host"] not in {"127.0.0.1", "0.0.0.0"}:
            self.host.addItem(profile["host"], profile["host"])
        self.host.setCurrentIndex(self.host.findData(profile["host"]))
        form.addRow("监听范围", self.host)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(profile["port"])
        form.addRow("端口", self.port)
        self.subnets = QLineEdit(", ".join(profile["allow_subnets"]))
        form.addRow("允许网段", self.subnets)
        self.require_token = QCheckBox("使用访问令牌")
        self.require_token.setChecked(profile["require_token"])
        form.addRow(self.require_token)
        self.auto_start = QCheckBox("随软件启动")
        self.auto_start.setChecked(profile["auto_start"])
        form.addRow(self.auto_start)
        layout.addLayout(form)
        self.status = QLabel(self.controller.message)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.start_button = QPushButton("启动服务")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self.start)
        self.stop_button = QPushButton("停止")
        self.stop_button.clicked.connect(lambda: self.controller.stop())
        row.addWidget(self.start_button)
        row.addWidget(self.stop_button)
        layout.addLayout(row)
        form = QFormLayout()
        self.address = QComboBox()
        self.address.addItems(local_addresses())
        self.address.setCurrentText("127.0.0.1")
        self.address.currentIndexChanged.connect(self.preview)
        form.addRow("连接地址", self.address)
        self.key = QLineEdit()
        self.key.setReadOnly(True)
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.token_controls = QWidget()
        row = QHBoxLayout(self.token_controls)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.key, 1)
        copy_key = QPushButton("复制令牌")
        copy_key.clicked.connect(self.copy_token)
        row.addWidget(copy_key)
        self.rotate_button = QPushButton("重新生成")
        self.rotate_button.clicked.connect(self.rotate)
        row.addWidget(self.rotate_button)
        self.token_label = QLabel("访问令牌")
        form.addRow(self.token_label, self.token_controls)
        layout.addLayout(form)
        self.config = QPlainTextEdit()
        self.config.setReadOnly(True)
        self.config.setMaximumHeight(110)
        layout.addWidget(self.config)
        copy = QPushButton("复制 Codex 配置")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.config.toPlainText()))
        layout.addWidget(copy)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setObjectName("muted")
        layout.addWidget(self.note)
        self.require_token.toggled.connect(self.refresh)
        self.host.currentIndexChanged.connect(self.preview)
        self.port.valueChanged.connect(self.preview)
        self.controller.changed.connect(self.refresh)
        self.preview()
        self.refresh()

    def values(self):
        return options(
            {
                **self.window.settings.agent,
                "host": self.host.currentData(),
                "port": self.port.value(),
                "allow_subnets": [v.strip() for v in self.subnets.text().split(",") if v.strip()],
                "auto_start": self.auto_start.isChecked(),
                "require_token": self.require_token.isChecked(),
            }
        )

    def preview(self):
        self.subnets.setEnabled(
            self.host.currentData() != "127.0.0.1" and not self.controller.active
        )
        self.address.setEnabled(self.host.currentData() != "127.0.0.1")
        address = (
            self.address.currentText() if self.host.currentData() != "127.0.0.1" else "127.0.0.1"
        )
        required = self.require_token.isChecked()
        self.config.setPlainText(codex_config(address, self.port.value(), require_token=required))
        self.token_label.setVisible(required)
        self.token_controls.setVisible(required)
        self.note.setText(
            "在 Agent 主机设置 CMV_MCP_TOKEN 环境变量后使用配置。关闭软件将停止服务。"
            if required
            else "复制配置即可连接。关闭软件将停止服务。"
        )

    def refresh(self):
        active = self.controller.active
        self.status.setText(self.controller.message)
        self.start_button.setEnabled(not active)
        self.stop_button.setEnabled(active)
        self.rotate_button.setEnabled(not active)
        for field in (self.host, self.port, self.auto_start, self.subnets, self.require_token):
            field.setEnabled(not active)
        self.key.setText(self.controller.token() if self.require_token.isChecked() else "")
        self.preview()

    def start(self):
        try:
            self.controller.save(self.values())
            self.controller.start()
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    def copy_token(self):
        try:
            QApplication.clipboard().setText(self.controller.token(create=True))
            self.refresh()
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    def rotate(self):
        try:
            self.controller.save({"token_encrypted": protect_key(secrets.token_urlsafe(32))})
            self.refresh()
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

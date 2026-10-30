"""Same-user, per-portable-install activation over a bounded local named pipe."""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, QStandardPaths, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from catia_manual.runtime import application_directory

MAX_MESSAGE = 16384


def validate_request(value):
    if not isinstance(value, dict) or set(value) - {"open"}:
        raise ValueError("无效的窗口激活请求")
    for text in value.values():
        if not isinstance(text, str) or len(text) > 4096:
            raise ValueError("激活请求过长或类型不正确")
        if any(ord(c) < 32 for c in text):
            raise ValueError("激活请求包含控制字符")
    return value


class InstanceBroker(QObject):
    request = Signal(dict)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        config = settings.config_path
        config_scope = str(config) if config else str(application_directory())
        if config and config.name in {
            "config.local.toml",
            "config.default.toml",
            "config.example.toml",
        }:
            config_scope = str(config.parent)
        digest = hashlib.sha256((str(application_directory()) + config_scope).casefold().encode())
        self.name = "CatiaManualViewer-" + digest.hexdigest()[:24]
        self.lock = QLockFile(
            str(
                Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation))
                / (self.name + ".lock")
            )
        )
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.newConnection.connect(self.accept_connections)
        self.clients = set()

    def take_or_forward(self, request):
        validate_request(request)
        if self.lock.tryLock(0):
            QLocalServer.removeServer(self.name)
            if not self.server.listen(self.name):
                self.lock.unlock()
                raise RuntimeError(self.server.errorString())
            return True
        client = QLocalSocket()
        client.connectToServer(self.name)
        if not client.waitForConnected(3000):
            raise RuntimeError("查看器已在运行，但暂未响应激活请求。")
        deadline = time.monotonic() + 3
        while not client.canReadLine():
            if time.monotonic() > deadline or not client.waitForReadyRead(1000):
                raise RuntimeError("查看器激活握手未完成。")
        try:
            owner = int(bytes(client.readLine()).strip())
        except ValueError as exc:
            raise RuntimeError("查看器激活握手无效。") from exc
        if sys.platform == "win32" and owner > 0:
            import ctypes

            # The user-launched secondary process can grant foreground activation to the owner.
            ctypes.windll.user32.AllowSetForegroundWindow(owner)
        client.write(json.dumps(request).encode() + b"\n")
        client.flush()
        client.waitForBytesWritten(1500)
        if not (client.bytesAvailable() or client.waitForReadyRead(4000)):
            raise RuntimeError("现有查看器未接收激活请求。")
        if bytes(client.readAll()) != b"ok\n":
            raise RuntimeError("现有查看器未接收激活请求。")
        client.disconnectFromServer()
        return False

    def accept_connections(self):
        while self.server.hasPendingConnections():
            peer = self.server.nextPendingConnection()
            self.clients.add(peer)
            buffer = bytearray()

            def read(peer=peer, buffer=buffer):
                buffer.extend(bytes(peer.readAll()))
                if len(buffer) > MAX_MESSAGE:
                    peer.abort()
                    return
                if b"\n" not in buffer:
                    return
                try:
                    value = validate_request(json.loads(bytes(buffer)))
                except (ValueError, UnicodeDecodeError):
                    peer.abort()
                    return
                peer.write(b"ok\n")
                peer.flush()
                self.request.emit(value)
                peer.disconnectFromServer()

            peer.readyRead.connect(read)
            peer.disconnected.connect(
                lambda peer=peer: (self.clients.discard(peer), peer.deleteLater())
            )
            QTimer.singleShot(5000, peer, peer.abort)
            peer.write(str(os.getpid()).encode() + b"\n")
            peer.flush()
            if peer.bytesAvailable():
                read()

    def close(self):
        self.server.close()
        self.lock.unlock()

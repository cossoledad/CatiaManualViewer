"""Window visibility is independent of the viewer-owned MCP process lifetime."""

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QCheckBox, QMenu, QMessageBox, QSystemTrayIcon

from catia_manual.config import save_settings
from catia_manual.gui.style import APP_NAME, app_icon, icon


class TrayController(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.quitting = False
        self.restore_maximized = False
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.menu = QMenu(window)
        self.menu.addAction(icon("home"), "显示窗口", self.restore)
        self.menu.addAction(icon("agent"), "Agent 接入…", self.open_agent)
        self.menu.addSeparator()
        self.menu.addAction(icon("close"), "退出", self.quit)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.activated)
        window.agent_controller.changed.connect(self.update_status)
        self.tray.show()
        self.update_status()

    def available(self):
        return self.tray.isVisible() and QSystemTrayIcon.isSystemTrayAvailable()

    def update_status(self):
        controller = self.window.agent_controller
        self.tray.setToolTip(APP_NAME + "\nMCP · " + controller.message)
        self.window.tray_action.setEnabled(self.available())

    def hide_window(self):
        if self.quitting or not self.available():
            return
        w = self.window
        if not w.isMinimized():
            self.restore_maximized = w.isMaximized()
        w.save_location_scroll()
        w.flush_preferences()
        w.hide()

    def on_minimized(self, old_state):
        self.restore_maximized = bool(old_state & Qt.WindowState.WindowMaximized)

    def request_close(self):
        if self.quitting:
            return True
        behavior = self.window.settings.appearance.get("close_behavior", "ask")
        if not self.available():
            # A missing tray must never leave an invisible, inaccessible process.
            return True
        if behavior not in {"tray", "quit"}:
            dialog = QMessageBox(self.window)
            dialog.setWindowTitle(APP_NAME)
            dialog.setText("关闭窗口后")
            tray = dialog.addButton("留在系统托盘", QMessageBox.ButtonRole.AcceptRole)
            quit_button = dialog.addButton("退出应用", QMessageBox.ButtonRole.DestructiveRole)
            dialog.addButton(QMessageBox.StandardButton.Cancel)
            remember = QCheckBox("记住选择")
            dialog.setCheckBox(remember)
            dialog.setDefaultButton(tray)
            dialog.exec()
            if dialog.clickedButton() not in {tray, quit_button}:
                return False
            behavior = "tray" if dialog.clickedButton() == tray else "quit"
            if remember.isChecked():
                try:
                    self.window.settings = save_settings(
                        self.window.settings, {"appearance": {"close_behavior": behavior}}
                    )
                except OSError:
                    self.window.statusBar().showMessage("未能保存关闭偏好", 4000)
        if behavior == "tray":
            self.hide_window()
            return False
        return True

    def restore(self):
        if self.quitting:
            return
        if not self.window.isVisible() or self.window.isMinimized():
            if self.restore_maximized:
                self.window.showMaximized()
            else:
                self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def activated(self, reason):
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self.restore()

    def open_agent(self):
        self.restore()
        if not self.quitting:
            self.window.manage_agent()

    def quit(self):
        self.quitting = True
        self.window.close()

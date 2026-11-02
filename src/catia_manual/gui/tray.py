"""Window visibility is independent of the viewer-owned MCP process lifetime."""

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

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
        if self.window.settings.appearance.get("minimize_to_tray", True) and self.available():
            QTimer.singleShot(0, self.hide_if_minimized)

    def hide_if_minimized(self):
        if self.window.isMinimized():
            self.hide_window()

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

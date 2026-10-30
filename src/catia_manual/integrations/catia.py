import os
import subprocess
from pathlib import Path

from catia_manual.config import Settings
from catia_manual.core.paths import SAMPLE_EXTENSIONS, within_root


class CatiaLauncher:
    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def session_available():
        """Attach-only probe, suitable for a background task; never creates a session."""
        if os.name != "nt":
            return False
        try:
            import pythoncom
            import win32com.client
        except ImportError:
            return False
        pythoncom.CoInitialize()
        try:
            try:
                win32com.client.GetActiveObject("CATIA.Application")
                return True
            except pythoncom.com_error:
                return False
        finally:
            pythoncom.CoUninitialize()

    def sample(self, relative: str) -> Path:
        path = within_root(self.settings.root, self.settings.root / relative)
        if path.suffix.lower() not in SAMPLE_EXTENSIONS:
            raise ValueError(f"不支持打开此类型的示例: {path.suffix}")
        if not path.is_file():
            raise FileNotFoundError(f"示例不存在: {path}")
        return path

    def command(self, relative: str) -> list[str]:
        path = self.sample(relative)
        return [
            str(self.settings.launcher),
            "-run",
            "CNEXT.exe",
            "-env",
            self.settings.environment,
            "-direnv",
            str(self.settings.environment_dir),
            "-nowindow",
            "-object",
            str(path),
        ]

    def open(self, relative: str) -> dict:
        if os.name != "nt":
            raise OSError("CATIA 集成仅支持 Windows")
        path = self.sample(relative)
        try:
            import pythoncom
            import win32com.client
        except ImportError:
            pythoncom = None
        if pythoncom is not None:
            pythoncom.CoInitialize()
            try:
                try:
                    application = win32com.client.GetActiveObject("CATIA.Application")
                except pythoncom.com_error:
                    application = None
                if application is not None:
                    # An error opening in an existing session must not start a second session.
                    application.Documents.Open(str(path))
                    application.Visible = True
                    return {"method": "com", "path": str(path)}
            finally:
                pythoncom.CoUninitialize()
        if not self.settings.launcher.is_file():
            raise FileNotFoundError(f"CATSTART 不存在: {self.settings.launcher}")
        if not self.settings.environment_dir.is_dir():
            raise FileNotFoundError(f"CATEnv 不存在: {self.settings.environment_dir}")
        if not self.settings.environment.strip():
            raise ValueError("请配置 CATIA 环境名称")
        command = self.command(relative)
        process = subprocess.Popen(
            command, shell=False, cwd=path.parent, creationflags=subprocess.CREATE_NO_WINDOW
        )
        return {"method": "catstart", "path": str(path), "pid": process.pid}

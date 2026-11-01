"""Developer release verification. Does not contact remote services or launch CATIA."""

import importlib
import json
import sqlite3
from pathlib import Path

from catia_manual.config import Settings
from catia_manual.runtime import application_directory, is_compiled


def verify_runtime(settings: Settings, output: Path) -> int:
    from catia_manual.gui.style import ASSETS

    checks = {}
    try:
        import certifi
        import httpx

        checks["certificate_bundle"] = Path(certifi.where()).is_file()
        # Constructing the client checks TLS resources, without making a request.
        with httpx.Client():
            checks["https_client"] = True
    except Exception as exc:
        checks["https_client"] = f"{type(exc).__name__}: {exc}"
    try:
        from catia_manual.core.terminology import vocabulary

        checks["command_vocabulary"] = bool(vocabulary()["commands"])
    except Exception as exc:
        checks["command_vocabulary"] = str(exc)
    for name in (
        "httpx",
        "mcp.server.fastmcp",
        "uvicorn.protocols.http.h11_impl",
        "pythoncom",
        "win32com.client",
        "ctranslate2",
        "sentencepiece",
        "onnxruntime",
        "tokenizers",
        "numpy",
    ):
        try:
            importlib.import_module(name)
            checks[name] = True
        except Exception as exc:
            checks[name] = str(exc)
    try:
        with sqlite3.connect(":memory:") as connection:
            connection.execute("CREATE VIRTUAL TABLE probe USING fts5(text)")
        checks["fts5"] = True
    except Exception as exc:
        checks["fts5"] = str(exc)
    checks["icons"] = all((ASSETS / name).is_file() for name in ("app.ico", "app.svg"))
    checks["reader_focus"] = (ASSETS / "reader_focus.js").is_file()
    try:
        from PySide6.QtCore import QFile
        from PySide6.QtWebChannel import QWebChannel

        checks["resource_bridge"] = (
            bool(QWebChannel)
            and QFile(":/qtwebchannel/qwebchannel.js").exists()
            and (ASSETS / "reader_resources.js").is_file()
        )
    except Exception as exc:
        checks["resource_bridge"] = str(exc)
    if is_compiled():
        checks["webengine_process"] = any(application_directory().rglob("QtWebEngineProcess.exe"))
        checks["msvc_runtime"] = (application_directory() / "msvcp140.dll").is_file()
    model_dir = Path(settings.translation["model_dir"])
    if (model_dir / "manifest.json").is_file():
        from catia_manual.core.offline_translation import LocalTranslationProvider

        try:
            provider = LocalTranslationProvider(model_dir)
            chinese = provider.translate("Create a pad from a sketch.", "zh-CN")
            english = provider.translate("创建一个凸台。", "en")
            checks["offline_translation"] = bool(chinese and english)
        except Exception as exc:
            checks["offline_translation"] = str(exc)
    report = {
        "compiled": is_compiled(),
        "application_directory": str(application_directory()),
        "config": str(settings.config_path),
        "database": str(settings.database),
        "checks": checks,
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if all(value is True for value in checks.values()) else 1


def verify_agent_controller(argv):
    """Developer-only check of the compiled GUI's real child-process launcher."""
    import argparse
    import time

    from PySide6.QtCore import QObject
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from catia_manual.config import load_settings
    from catia_manual.gui.agent import AgentController
    from catia_manual.runtime import process_executable

    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    app = QApplication.instance() or QApplication([])

    class Owner(QObject):
        def __init__(self):
            super().__init__()
            self.settings = load_settings(args.config)

        def flush_preferences(self):
            pass

    owner = Owner()
    controller = AgentController(owner)
    controller.start()
    deadline = time.monotonic() + 65
    while controller.state in {"starting", "stopping"} and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(30)
    report = {
        "compiled": is_compiled(),
        "executable": process_executable(),
        "state": controller.state,
        "message": controller.message,
        "failure": controller.failure,
        "process_error": controller.process.errorString(),
    }
    controller.stop()
    deadline = time.monotonic() + 8
    while controller.active and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(30)
    report["stopped"] = not controller.active
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    return 0 if report["state"] == "running" and report["stopped"] else 1

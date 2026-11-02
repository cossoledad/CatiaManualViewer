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
    https_trace = []
    try:
        import ssl

        import certifi
        import httpx

        checks["certificate_bundle"] = Path(certifi.where()).is_file()
        ssl.create_default_context(cafile=certifi.where())
        checks["ssl_context"] = True
        # Constructing the client checks TLS resources, without making a request.
        with httpx.Client():
            checks["https_client"] = True
    except Exception as exc:
        import traceback

        checks["https_client"] = f"{type(exc).__name__}: {exc}"
        https_trace = [
            {"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
            for frame in traceback.extract_tb(exc.__traceback__)
        ]
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
        "https_trace": https_trace,
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
    parser.add_argument("--article", help="also exercise the real reader, PDF and tray")
    parser.add_argument("--pdf", type=Path)
    args = parser.parse_args(argv)
    if bool(args.article) != bool(args.pdf):
        parser.error("--article and --pdf must be used together")
    app = QApplication.instance() or QApplication([])

    def pump_until(predicate, seconds):
        deadline = time.monotonic() + seconds
        while not predicate() and time.monotonic() < deadline:
            app.processEvents()
            QTest.qWait(30)
        return bool(predicate())

    class Owner(QObject):
        def __init__(self):
            super().__init__()
            self.settings = load_settings(args.config)

        def flush_preferences(self):
            pass

    if args.article:
        from catia_manual.gui.app import MainWindow

        owner = MainWindow(load_settings(args.config), args.article)
        owner.show()
        reader_ready = pump_until(
            lambda: owner.page_ready and not owner.page_loading and not owner.jobs, 30
        )
        controller = owner.agent_controller
    else:
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
    if args.article:
        import httpx

        args.pdf.parent.mkdir(parents=True, exist_ok=True)
        owner.pdf_exporter.start(args.pdf)
        pdf_ready = pump_until(lambda: not owner.pdf_exporter.busy, 65)
        owner.tray_controller.hide_window()
        hidden = not owner.isVisible()
        auth = (
            {"Authorization": "Bearer " + controller.token()}
            if owner.settings.agent.get("require_token")
            else {}
        )
        try:
            health = (
                httpx.get(
                    f"http://127.0.0.1:{owner.settings.agent.get('port', 8765)}/health",
                    headers=auth,
                    trust_env=False,
                    timeout=3,
                ).status_code
                == 200
            )
        except httpx.HTTPError:
            health = False
        owner.activate_request({})
        report["viewer"] = {
            "reader_ready": reader_ready,
            "pdf_generated": pdf_ready
            and args.pdf.is_file()
            and args.pdf.read_bytes().startswith(b"%PDF-"),
            "tray_hidden": hidden,
            "mcp_while_hidden": health,
            "restored": owner.isVisible() and not owner.isMinimized(),
        }
    controller.stop()
    deadline = time.monotonic() + 8
    while controller.active and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(30)
    report["stopped"] = not controller.active
    if args.article:
        pump_until(lambda: not owner.jobs, 10)
        owner.close()
        pump_until(lambda: not owner.isVisible(), 10)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
    return (
        0
        if (
            report["state"] == "running"
            and report["stopped"]
            and all(report.get("viewer", {}).values())
        )
        else 1
    )

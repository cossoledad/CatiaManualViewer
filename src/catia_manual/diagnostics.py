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
    for name in ("httpx", "pythoncom", "win32com.client", "ctranslate2", "sentencepiece"):
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

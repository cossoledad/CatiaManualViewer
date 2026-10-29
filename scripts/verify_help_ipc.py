"""Exercise the actual second-process entry point and same-window help delivery."""

import json
import os
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from catia_manual.config import load_settings, save_settings  # noqa: E402
from catia_manual.gui.app import MainWindow  # noqa: E402
from catia_manual.gui.single_instance import InstanceBroker, validate_request  # noqa: E402

project = Path(__file__).resolve().parents[1]
config = project / ".cache" / f"ipc-{time.time_ns()}.toml"
settings = replace(load_settings(), config_path=config)
settings = save_settings(settings, {"appearance": {"remember_window": False}})
app = QApplication([])
broker = InstanceBroker(settings, app)
assert broker.take_or_forward({})
window = MainWindow(settings, "online/prtug_C2/prtugbt0502.htm")
broker.request.connect(window.handle_help_request)
window.show()
report = {"passed": False}
child = None
deadline = time.monotonic() + 30


def poll():
    global child
    if time.monotonic() > deadline:
        report["error"] = "timeout"
        finish()
    elif child is None and window.page_ready:
        child = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "catia_manual.gui.app",
                "--config",
                str(config),
                "--help-workbench",
                "PrtCfg",
                "--help-feature",
                "Pad",
            ]
        )
        QTimer.singleShot(100, poll)
    elif child is not None and child.poll() is not None and window.page_ready:
        report["child_exit"] = child.returncode
        report["target"] = window.current_document.path
        report["passed"] = (
            child.returncode == 0 and report["target"] == "online/prtug_C2/prtugbt0501.htm"
        )
        try:
            validate_request({"open": "invalid\nrequest"})
            report["passed"] = False
        except ValueError:
            report["control_characters_rejected"] = True
        finish()
    else:
        QTimer.singleShot(100, poll)


def finish():
    broker.close()
    window.close()
    (project / "data/help-ipc.json").write_text(json.dumps(report, indent=2), "utf-8")
    print(json.dumps(report))
    app.exit(0 if report["passed"] else 1)


QTimer.singleShot(150, poll)
raise SystemExit(app.exec())

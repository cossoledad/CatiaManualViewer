"""Native viewer service controls, encrypted settings, mount pinning and process ownership."""

import json
import os
import runpy
import shutil
import socket
import sys
import time
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

import httpx  # noqa: E402
import tomlkit  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402

from catia_manual.config import load_settings  # noqa: E402
from catia_manual.core.index import ManualIndex  # noqa: E402
from catia_manual.gui.agent import AgentDialog  # noqa: E402
from catia_manual.gui.app import MainWindow  # noqa: E402
from catia_manual.integrations.catia import CatiaLauncher  # noqa: E402

project = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project))
folder = project / ".cache/agent-gui" / str(time.time_ns())
folder.mkdir(parents=True)
root = runpy.run_path(str(project / "tests/test_structure.py"))["manual_library"].__wrapped__(
    folder / "manual"
)
database = folder / "data/index.sqlite3"
with ManualIndex(database, root) as index:
    index.build()
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
config = folder / "config.toml"
config.write_text(
    tomlkit.dumps(
        {
            "documentation": {"root": str(root), "database": str(database)},
            "translation": {"provider": "glossary", "automatic": False},
            "agent": {"port": port},
            "appearance": {"remember_window": False},
        }
    ),
    "utf-8",
)
settings = load_settings(config)
CatiaLauncher.session_available = staticmethod(lambda: False)
app = QApplication([])
app.setFont(QFont("Microsoft YaHei UI", 10))
window = MainWindow(settings, "online/shared_v17/coincide.htm")
window.show()
report = {"checks": [], "failures": []}
dialog = None


def wait(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            QTest.qWait(40)
            return
        QTest.qWait(20)
    raise TimeoutError("agent GUI acceptance timed out: " + window.agent_controller.message)


def check(value, name):
    report["checks"].append({"name": name, "passed": bool(value)})
    if not value:
        report["failures"].append(name)
    print(("PASS " if value else "FAIL ") + name, flush=True)


try:
    wait(lambda: window.page_ready and not window.jobs and not window.page_loading)
    controller = window.agent_controller
    check(
        not controller.active and window.agent_action.text() == "Agent 接入…",
        "service is opt-in and available from the global menu",
    )
    dialog = AgentDialog(window)
    dialog.show()
    dialog.host.setCurrentIndex(dialog.host.findData("0.0.0.0"))
    check(
        not dialog.require_token.isChecked()
        and not dialog.token_controls.isVisible()
        and "CMV_MCP_TOKEN" not in dialog.config.toPlainText(),
        "default connection hides credentials and copies a URL-only Codex configuration",
    )
    dialog.start_button.click()
    wait(lambda: controller.state == "running")
    base = f"http://127.0.0.1:{port}"
    with httpx.Client(trust_env=False, timeout=3) as client:
        check(
            client.get(base + "/health").status_code == 200
            and not load_settings(config).agent["require_token"]
            and not load_settings(config).agent["token_encrypted"]
            and not dialog.require_token.isEnabled(),
            "viewer starts a token-free LAN service without generating or saving a credential",
        )
    output = project / "data"
    output.mkdir(exist_ok=True)
    dialog.grab().save(str(output / "agent-settings-no-token.png"))
    dialog.stop_button.click()
    wait(lambda: not controller.active)
    dialog.require_token.setChecked(True)
    check(
        dialog.token_controls.isVisible() and "CMV_MCP_TOKEN" in dialog.config.toPlainText(),
        "optional authentication reveals credentials and updates the copied configuration",
    )
    dialog.start_button.click()
    wait(lambda: controller.state == "running")
    check(
        controller.active and not dialog.host.isEnabled() and dialog.stop_button.isEnabled(),
        "viewer starts and owns the independent LAN backend",
    )
    token = controller.token()
    persisted = config.read_text("utf-8")
    check(
        token not in persisted
        and "dpapi:v1:" in persisted
        and load_settings(config).agent["port"] == port,
        "connection settings persist while the token remains encrypted",
    )
    check(
        dialog.key.echoMode() == QLineEdit.EchoMode.Password
        and token not in dialog.config.toPlainText(),
        "dialog masks the token and copied Codex configuration uses an environment variable",
    )
    with httpx.Client(trust_env=False, timeout=3) as client:
        check(
            client.get(base + "/health", headers={"Authorization": "Bearer " + token}).status_code
            == 200,
            "GUI-launched portable dispatcher serves authenticated HTTP",
        )
    output = project / "data"
    output.mkdir(exist_ok=True)
    dialog.grab().save(str(output / "agent-settings.png"))
    dialog.stop_button.click()
    wait(lambda: not controller.active)
    check(
        controller.state == "stopped" and not controller.runtime.exists(),
        "stop waits for graceful shutdown and removes runtime state",
    )
    dialog.rotate_button.click()
    fresh = controller.token()
    check(
        fresh != token and fresh not in config.read_text("utf-8"),
        "token rotation persists a fresh encrypted credential",
    )
    dialog.start_button.click()
    wait(lambda: controller.state == "running")
    with httpx.Client(trust_env=False, timeout=3) as client:
        check(
            client.get(base + "/health", headers={"Authorization": "Bearer " + token}).status_code
            == 401,
            "the restarted backend rejects the old token",
        )
    dialog.close()
    copied_root = folder / "other-manual"
    shutil.copytree(root, copied_root)
    copied_database = folder / "other-data/index.sqlite3"
    with ManualIndex(copied_database, copied_root) as index:
        index.build()
    window.apply_settings(replace(window.settings, root=copied_root, database=copied_database))
    wait(lambda: not controller.active and not window.jobs)
    controller.start(automatic=True)
    check(
        not controller.active and "授权" in controller.message,
        "switching libraries stops service and forbids automatic access to a new library",
    )
    controller.start()
    wait(lambda: controller.state == "running")
    check(
        controller.token() != fresh,
        "explicit authorization of another library also rotates the credential",
    )
    controller.stop()
    wait(lambda: not controller.active)
    dialog = AgentDialog(window)
    dialog.show()
    dialog.require_token.setChecked(False)
    dialog.start_button.click()
    wait(lambda: controller.state == "running")
    with httpx.Client(trust_env=False, timeout=3) as client:
        check(
            client.get(base + "/health").status_code == 200
            and "CMV_MCP_TOKEN" not in dialog.config.toPlainText()
            and not load_settings(config).agent["require_token"],
            "disabling authentication removes the environment dependency "
            "and persists across restart",
        )
    dialog.close()
    window.close()
    wait(lambda: not window.isVisible() and not controller.active and not window.jobs)
    check(
        not controller.runtime.exists(),
        "closing the viewer leaves no listening subprocess or runtime state",
    )
except Exception as exc:
    report["failures"].append(repr(exc))
finally:
    if dialog:
        dialog.close()
    window.agent_controller.stop()
    window.close()
    wait(lambda: not window.isVisible() and not window.agent_controller.active and not window.jobs)
    output = project / "data"
    output.mkdir(exist_ok=True)
    report["passed"] = not report["failures"]
    (output / "agent-gui.json").write_text(json.dumps(report, indent=2), "utf-8")
raise SystemExit(0 if report["passed"] else 1)

import json

import pytest

from catia_manual.agent.lifecycle import failed_runtime


@pytest.mark.parametrize(
    "exc,code",
    [
        (OSError(98, "private-source"), "PORT_IN_USE"),
        (PermissionError(13, "private-token"), "ACCESS_DENIED"),
        (OSError(99, "private-address"), "ADDRESS_UNAVAILABLE"),
        (ImportError("private-path"), "DEPENDENCY_MISSING"),
        (RuntimeError("private-token"), "STARTUP_ERROR"),
    ],
)
def test_bootstrap_reports_failure_without_exception_messages(tmp_path, exc, code):
    report = tmp_path / "runtime.json"
    failed_runtime(["--runtime", str(report)], "abc", exc)
    data = json.loads(report.read_text("utf-8"))
    assert data["instance"] == "abc" and data["state"] == "failed"
    assert data["code"] == code and data["exception"] == type(exc).__name__
    assert "private-" not in report.read_text("utf-8")


def test_bootstrap_missing_or_unwritable_report_does_not_mask_failure(tmp_path):
    failed_runtime([], "abc", ValueError())
    failed_runtime(["--runtime", str(tmp_path)], "abc", ValueError())

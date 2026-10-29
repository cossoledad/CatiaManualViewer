from pathlib import Path
from types import SimpleNamespace

import pytest

from catia_manual.integrations.help_macro import export_help_macros, resolve_help_context, vb_string


def test_macro_export_quotes_unicode_path_and_never_modifies_catia(tmp_path):
    executable = tmp_path / "中文便携版" / "Catia Manual Viewer.exe"
    executable.parent.mkdir()
    executable.touch()
    files = export_help_macros(executable, tmp_path / "宏库")
    assert len(files) == 3
    general = files[0].read_text("ascii")
    assert vb_string(str(executable)) in general
    assert "ChrW(20013)" in general
    assert "TypeName(selection.Item2(1).Value)" in general
    assert "GetWorkbenchId()" in general
    assert "WScript.Shell" in general and "False" in general
    assert "Documents.Open" not in general and "StartCommand" not in general
    assert "--open online/prtug_C2/prtugbt0501.htm" in files[1].read_text("ascii")


def test_help_context_uses_existing_topic_or_scoped_search(corpus):
    catalog = SimpleNamespace(manuals={"prtug_C2": object()})
    assert resolve_help_context(corpus, catalog, "PrtCfg", "Unmapped") == {
        "manual": "prtug_C2",
        "search": "Unmapped",
    }
    assert resolve_help_context(corpus, catalog, "PrtCfg", "") == {
        "manual": "prtug_C2",
        "search": "",
    }
    target = corpus / "online/prtug_C2/prtugbt0501.htm"
    target.touch()
    assert resolve_help_context(corpus, catalog, feature="Pad") == {
        "open": "online/prtug_C2/prtugbt0501.htm"
    }
    assert resolve_help_context(corpus, catalog, "Unknown", "Unknown") == {"search": "Unknown"}


def test_export_rejects_non_executable(tmp_path):
    with pytest.raises(ValueError):
        export_help_macros(Path("not-an-executable.py"), tmp_path)

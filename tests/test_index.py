import json
import subprocess
import sys

import pytest

from catia_manual.core.index import ManualIndex


def test_index_search_increment_update_delete_and_bounded_prune(corpus):
    with ManualIndex(corpus / "index.sqlite3", corpus) as index:
        first = index.build()
        assert first["updated"] == 3 and first["errors"] == []
        assert index.build()["unchanged"] == 3
        hits = index.search("creating pads")
        assert hits[0].path.endswith("pad.htm")
        assert index.search("pock", module="prtug_C2")
        assert not index.search("pad", module="absent")
        assert index.search('" OR ((( : NEAR') == []
        assert index.search("!!!") == []
        assert index.get(hits[0].path)["blocks"]
        source = corpus / "online/prtug_C2/pad.htm"
        source.write_text("<h1>Lofting</h1><p>Unique loft tutorial.</p>")
        assert index.build()["updated"] == 1
        assert index.search("loft")
        assert not index.search("dimension")
        (corpus / "online/prtug_C2/other.htm").unlink()
        index.build(limit=1)
        assert index.stats()["documents"] == 3
        assert index.build()["deleted"] == 1
        assert not index.search("pocket")


def test_index_root_binding(corpus, tmp_path):
    database = corpus / "index.sqlite3"
    with ManualIndex(database, corpus):
        pass
    with pytest.raises(ValueError, match="另一文档目录"), ManualIndex(database, tmp_path / "other"):
        pass


def test_failed_scan_retains_previous_rows(corpus, monkeypatch):
    from catia_manual.core.parser import ManualParser

    with ManualIndex(corpus / "index.sqlite3", corpus) as index:
        index.build()
        (corpus / "online/prtug_C2/other.htm").unlink()

        def fail(*_):
            raise ValueError("fixture parse failure")

        monkeypatch.setattr(ManualParser, "parse", fail)
        report = index.build(force=True)
        assert report["errors"] and report["deleted"] == 0
        assert index.stats()["documents"] == 3


def test_agent_cli_is_json_and_does_not_import_qt(corpus):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys,json; from catia_manual.core.parser import ManualParser; "
            "print(json.dumps(any(x.startswith('PySide6') for x in sys.modules)))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) is False

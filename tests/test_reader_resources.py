import json
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from catia_manual.config import load_settings, save_settings
from catia_manual.core import capabilities as caps
from catia_manual.core.library_contract import DocumentRef, LibraryMount, SourceLocation
from catia_manual.core.parser import ManualParser
from catia_manual.core.reader_state import ReaderState
from catia_manual.core.resources import ResourceService


def test_global_reading_preference_survives_restart_and_other_settings(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("[appearance]\nreader_zoom=130\n[custom]\nvalue=42\n", "utf-8")
    settings = load_settings(config)
    state = ReaderState.from_settings(settings.appearance)
    state.select("bilingual")
    saved = save_settings(settings, {"appearance": {"reading_mode": state.mode}})
    assert (
        ReaderState.from_settings(load_settings(saved.config_path).appearance).mode == "bilingual"
    )
    assert saved.appearance["reader_zoom"] == 130
    assert "value = 42" in config.read_text("utf-8") or "value=42" in config.read_text("utf-8")
    assert ReaderState.from_settings({"reading_mode": "unknown"}).mode == "original"


def test_document_only_capabilities_do_not_require_catia_or_models(corpus, monkeypatch):
    settings = replace(
        load_settings(),
        root=corpus,
        launcher=corpus / "missing.exe",
        environment_dir=corpus / "missing-env",
        environment="",
        translation={"provider": "glossary"},
    )
    monkeypatch.setattr(caps, "module_available", lambda _: False)
    available = caps.detect_capabilities(settings)
    assert available.documents.available
    assert available.selection_translation.available
    assert not available.sample_launch.available and not available.page_translation.available
    assert "CATIA" in available.sample_launch.reason
    local = caps.detect_capabilities(
        replace(settings, translation={"provider": "local", "model_dir": str(corpus)})
    )
    assert not local.selection_translation.available
    # A launched CATIA session is independent of a configured launcher.
    monkeypatch.setattr(caps, "module_available", lambda _: True)
    assert caps.detect_capabilities(settings, catia_session=True).sample_launch.available


def test_resources_keep_root_boundary_and_allow_reveal_without_catia(corpus, monkeypatch):
    resources = ResourceService(corpus)
    relative = "online/prtug_C2/samples/Pad 1.CATPart"
    assert resources.path(relative).is_file()
    with pytest.raises(ValueError):
        resources.path("../outside.txt")
    with pytest.raises(FileNotFoundError):
        resources.path("missing.CATPart")
    with pytest.raises(ValueError):
        resources.image(relative)
    calls = []
    monkeypatch.setattr(
        "catia_manual.core.resources.subprocess.Popen",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )
    resources.reveal(relative)
    assert calls[0][0] == ["explorer.exe", "/select,", str(resources.path(relative))]
    assert calls[0][1]["shell"] is False


def test_renderer_marks_resource_gestures_but_retains_document_links(corpus):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(ManualParser(corpus).render("online/prtug_C2/pad.htm"), "lxml")
    assert soup.select_one('a[data-cmv-kind="sample"]')["href"].endswith("Pad%201.CATPart")
    assert soup.select_one('a[data-cmv-kind="document"]')
    assert soup.select_one("img[data-cmv-image]")
    assert not soup.find("script")


def test_source_contract_identity_is_independent_of_mount_and_revision(tmp_path):
    identity = str(uuid.uuid4())
    ref = DocumentRef(identity, "online\\prtug_C2\\pad.htm")
    assert ref == DocumentRef(identity, "online/prtug_C2/pad.htm")
    mounts = [
        LibraryMount(identity, tmp_path / "disk-a"),
        LibraryMount(identity, tmp_path / "disk-b"),
    ]
    assert mounts[0].root != mounts[1].root
    a = SourceLocation(
        ref,
        "a" * 64,
        fragment="step",
        heading_path=("Pad", "Limits"),
        text_quote="Select Sketch.1",
        offset_css_px=-12.5,
    )
    b = replace(a, content_hash="b" * 64)
    assert a.document.document_id == b.document.document_id
    value = json.loads(json.dumps(a.to_dict()))
    assert "root" not in value["document"]
    assert SourceLocation.from_dict(value) == a
    with pytest.raises(ValueError):
        SourceLocation.from_dict({**value, "schema_version": 99})
    with pytest.raises(ValueError):
        SourceLocation.from_dict({**value, "schema_version": True})
    with pytest.raises(ValueError):
        replace(a, offset_css_px=float("nan"))
    with pytest.raises(ValueError):
        replace(a, heading_path="not an array")
    with pytest.raises(ValueError):
        LibraryMount(identity, Path("relative"))


@pytest.mark.parametrize("path", ["../x.htm", "online/../../x.htm", "C:/x.htm", "/x.htm", ""])
def test_source_contract_rejects_non_library_paths(path):
    with pytest.raises(ValueError):
        DocumentRef(str(uuid.uuid4()), path)

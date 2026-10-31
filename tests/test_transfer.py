import json
import shutil
import tomllib
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from catia_manual.config import load_settings, save_settings
from catia_manual.core.index import ManualIndex
from catia_manual.core.transfer import (
    activation_changes,
    export_bundle,
    import_bundle,
    inspect_bundle,
    public_settings,
)


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "manual"
    page = root / "online/prtug_C2/pad.htm"
    page.parent.mkdir(parents=True)
    page.write_text('<h1>Creating Pads</h1><p id="step">Select a sketch.</p>', "utf-8")
    (page.parent / "pad.CATPart").write_bytes(b"sample fixture")
    (root / "CATIA_P3_homepage.htm").write_text(
        '<a href="online/prtug_C2/pad.htm">Pads</a>', "utf-8"
    )
    (tmp_path / "models/en_zh/model").mkdir(parents=True)
    (tmp_path / "models/en_zh/model/model.bin").write_bytes(b"model fixture")
    (tmp_path / "models/config.local.toml").write_text("private model-directory config", "utf-8")
    (tmp_path / "search-models/onnx").mkdir(parents=True)
    (tmp_path / "search-models/onnx/model_quantized.onnx").write_bytes(b"semantic model fixture")
    config = tmp_path / "config.toml"
    config.write_text(
        "[documentation]\nroot='manual'\ndatabase='source/library.sqlite3'\n"
        "[translation]\nprovider='chat-completions'\nendpoint='https://fixture.invalid/v1'\n"
        "model='fixture'\nmodel_dir='models'\napi_key_override='test-secret'\n"
        "[translation.profiles.deepl]\napi_key_encrypted='dpapi:v1:private'\n"
        "[search]\nmode='hybrid'\nmodel_dir='search-models'\n[appearance]\nreading_mode='bilingual'\n",
        "utf-8",
    )
    settings = load_settings(config)
    with ManualIndex(settings.database, settings.root) as index:
        index.build()
        index.connection.execute("CREATE TABLE user_payload(value BLOB)")
        index.connection.execute(
            "INSERT INTO user_payload VALUES(?)", (b"translations, vectors and views",)
        )
        index.connection.commit()  # Keep the WAL writer open throughout the export.
        yield settings, index


def test_roundtrip_preserves_committed_wal_data_and_remaps_paths_without_secrets(library, tmp_path):
    settings, original = library
    archive = tmp_path / "transfer.zip"
    result = export_bundle(settings, archive)
    assert result["bytes"] > 0
    manifest = inspect_bundle(archive)
    assert manifest["library_id"] == original.library_id
    with zipfile.ZipFile(archive) as package:
        assert not any(name.endswith("config.local.toml") for name in package.namelist())
        config = package.read("config.toml").decode()
        assert "test-secret" not in config and "dpapi:" not in config
        assert str(settings.root) not in config
    destination = tmp_path / "imported"
    imported = import_bundle(archive, destination)
    updated = load_settings(Path(imported["config"]))
    assert updated.root == destination / "manuals/English"
    assert updated.database == destination / "data/library.sqlite3"
    assert updated.translation["model"] == "fixture"
    assert updated.appearance["reading_mode"] == "bilingual"
    assert (
        Path(updated.translation["model_dir"]) / "en_zh/model/model.bin"
    ).read_bytes() == b"model fixture"
    assert (Path(updated.search["model_dir"]) / "onnx/model_quantized.onnx").is_file()
    with ManualIndex(updated.database, updated.root) as index:
        assert index.library_id == original.library_id
        assert (
            index.connection.execute("SELECT value FROM user_payload").fetchone()[0]
            == b"translations, vectors and views"
        )
        assert index.search("Creating Pads")
    assert original.meta("root") == str(settings.root)
    assert "test-secret" in settings.config_path.read_text("utf-8")


def test_external_document_copy_and_model_omission(library, tmp_path):
    settings, index = library
    archive = tmp_path / "small.zip"
    export_bundle(settings, archive, include_documents=False, include_models=False)
    with pytest.raises(ValueError, match="文档副本"):
        import_bundle(archive, tmp_path / "no-docs")
    copy = tmp_path / "copy"
    shutil.copytree(settings.root, copy)
    imported = import_bundle(archive, tmp_path / "external", root=copy)
    config = tomllib.loads(Path(imported["config"]).read_text("utf-8"))
    assert config["search"]["mode"] == "keyword"
    assert imported["library_id"] == index.library_id and imported["root"] == str(copy)
    (copy / "online/prtug_C2/pad.htm").write_text("different content", "utf-8")
    with pytest.raises(ValueError, match="副本不一致"):
        import_bundle(archive, tmp_path / "mismatched", root=copy)
    assert not (tmp_path / "mismatched").exists()


def test_activation_cannot_reuse_target_credentials_at_an_imported_endpoint(library):
    settings, _ = library
    current = replace(
        settings, translation={**settings.translation, "api_key_encrypted": "dpapi:v1:target-only"}
    )
    incoming = public_settings(settings)
    incoming["translation"].update(
        provider="libretranslate", endpoint="https://new-endpoint.invalid"
    )
    updated = save_settings(current, activation_changes(current, incoming))
    assert not updated.translation.get("api_key_encrypted")
    assert updated.launcher == current.launcher
    assert "target-only" not in updated.config_path.read_text("utf-8")


def test_failed_and_cancelled_operations_preserve_original_and_roll_back_new_paths(
    library, tmp_path
):
    settings, index = library
    archive = tmp_path / "transfer.zip"
    export_bundle(settings, archive)
    with pytest.raises(FileExistsError):
        export_bundle(settings, archive)
    with pytest.raises(FileExistsError):
        import_bundle(archive, settings.database.parent)

    def cancel(_):
        raise ValueError("cancelled")

    with pytest.raises(ValueError, match="cancelled"):
        import_bundle(archive, tmp_path / "cancelled", progress=cancel)
    assert not (tmp_path / "cancelled").exists()
    with pytest.raises(ValueError, match="cancelled"):
        export_bundle(settings, tmp_path / "cancelled.zip", progress=cancel)
    assert not (tmp_path / "cancelled.zip").exists()
    assert index.meta("root") == str(settings.root)


def test_modified_archive_content_is_rejected_before_activation(library, tmp_path):
    settings, _ = library
    archive, corrupt = tmp_path / "data.zip", tmp_path / "corrupt.zip"
    export_bundle(settings, archive)
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(corrupt, "w") as target:
        for name in source.namelist():
            content = source.read(name)
            if name.endswith("pad.htm"):
                content = b"X" + content[1:]
            target.writestr(name, content)
    with pytest.raises(ValueError, match="校验失败"):
        import_bundle(corrupt, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()


@pytest.mark.parametrize(
    "name",
    ["../escape", "C:/escape", "data/models/CON.txt", "data/models/a.", "data/models/a:stream"],
)
def test_unsafe_paths_are_rejected(library, tmp_path, name):
    settings, _ = library
    archive, bad = tmp_path / "data.zip", tmp_path / "bad.zip"
    export_bundle(settings, archive, include_documents=False, include_models=False)
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(bad, "w") as target:
        for item in source.infolist():
            content = source.read(item)
            if item.filename == "transfer.json":
                manifest = json.loads(content)
                manifest["files"].append({"path": name, "size": 0, "sha256": "a" * 64})
                content = json.dumps(manifest).encode()
            target.writestr(item.filename, content)
    with pytest.raises(ValueError):
        import_bundle(bad, tmp_path / "rejected")
    assert not (tmp_path / "rejected").exists()

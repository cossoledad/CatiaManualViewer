import json
import subprocess
import zipfile

import pytest

from scripts import release


def make_archive(folder, **extra):
    info = release.metadata()
    archive = folder / (info["artifact"] + ".zip")
    files = {
        "CatiaManualViewer.exe": b"MZ-test-fixture",
        "config.default.toml": (release.PROJECT / "packaging/config.default.toml")
        .read_text("utf-8")
        .replace("provider = 'glossary'", "provider = 'local'"),
        "build-info.json": json.dumps({"version": info["version"], "offline_models": True}),
        "dependencies.json": "[]",
        "catia_manual/assets/reader_translation.js": "// fixture",
        "catia_manual/assets/reader_resources.js": "// fixture",
        "catia_manual/assets/command_terms.json": (
            release.PROJECT / "src/catia_manual/assets/command_terms.json"
        ).read_bytes(),
        "data/models/manifest.json": (release.PROJECT / "packaging/models.lock.json").read_bytes(),
    }
    for pair in ("en_zh", "zh_en"):
        for name in ("model/model.bin", "metadata.json", "sentencepiece.model"):
            files[f"data/models/{pair}/{name}"] = b"fixture"
    files.update(extra)
    with zipfile.ZipFile(archive, "w") as package:
        for name, value in files.items():
            package.writestr(archive.stem + "/" + name, value)
    return archive


def test_version_tag_and_notes_must_match(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion="1.2.3"', "utf-8")
    notes = tmp_path / "docs/releases/v1.2.3.md"
    notes.parent.mkdir(parents=True)
    notes.write_text("Release notes", "utf-8")
    assert release.metadata(tmp_path, "v1.2.3")["tag"] == "v1.2.3"
    with pytest.raises(ValueError, match="不一致"):
        release.metadata(tmp_path, "v9.9.9")
    notes.unlink()
    with pytest.raises(ValueError, match="发布说明"):
        release.metadata(tmp_path)


def test_archive_and_checksum_accept_only_clean_matching_bundle(tmp_path):
    archive = make_archive(tmp_path)
    release.verify_archive(archive, release.metadata()["version"])
    release.write_checksum(archive)
    release.verify_checksum(archive)
    assert b"\r" not in release.checksum_path(archive).read_bytes()
    with pytest.raises(ValueError, match="版本"):
        release.verify_archive(archive, "9.9.9")
    release.checksum_path(archive).write_text("wrong hash", "ascii")
    with pytest.raises(ValueError, match="SHA-256"):
        release.verify_checksum(archive)


@pytest.mark.parametrize(
    "name",
    [
        "config.local.toml",
        "Config.Local.Toml",
        "data/user.sqlite3",
        "online/manual.htm",
        "../escape.txt",
        "integration/obsolete.catvbs",
    ],
)
def test_archive_rejects_private_data_and_unsafe_paths(tmp_path, name):
    archive = make_archive(tmp_path, **{name: "private"})
    with pytest.raises(ValueError):
        release.verify_archive(archive, release.metadata()["version"])


def test_publish_does_not_overwrite_public_release(tmp_path, monkeypatch):
    archive = make_archive(tmp_path)
    release.write_checksum(archive)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, '{"isDraft":false}')

    monkeypatch.setattr(release.subprocess, "run", run)
    with pytest.raises(ValueError, match="公开"):
        release.publish("owner/repo", release.metadata()["tag"], tmp_path)
    assert len(calls) == 1 and calls[0][2] == "view"


@pytest.mark.parametrize("invalid_field", ["path", "credential"])
def test_archive_rejects_machine_paths_or_credentials(tmp_path, invalid_field):
    bad_config = (release.PROJECT / "packaging/config.default.toml").read_text("utf-8")
    bad_config = bad_config.replace("provider = 'glossary'", "provider = 'local'")
    if invalid_field == "path":
        bad_config = bad_config.replace("root = 'manuals/English'", "root = 'C:\\private'")
    else:
        bad_config = bad_config.replace(
            "provider = 'local'", "provider = 'local'\napi_key='test-secret'"
        )
    archive = make_archive(tmp_path, **{"config.default.toml": bad_config})
    with pytest.raises(ValueError, match="绝对路径|密钥"):
        release.verify_archive(archive, release.metadata()["version"])


def test_failed_upload_leaves_draft_unpublished(tmp_path, monkeypatch):
    archive = make_archive(tmp_path)
    release.write_checksum(archive)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[2] == "view":
            return subprocess.CompletedProcess(command, 1, "")
        if command[2] == "upload":
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(release.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        release.publish("owner/repo", release.metadata()["tag"], tmp_path)
    assert [call[2] for call in calls] == ["view", "create", "upload"]
    assert "--verify-tag" in calls[1] and "--draft" in calls[1]


def test_rerun_can_complete_existing_draft(tmp_path, monkeypatch):
    archive = make_archive(tmp_path)
    release.write_checksum(archive)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, '{"isDraft":true}')

    monkeypatch.setattr(release.subprocess, "run", run)
    release.publish("owner/repo", release.metadata()["tag"], tmp_path)
    assert [call[2] for call in calls] == ["view", "upload", "edit"]
    assert "--clobber" in calls[1] and "--draft=false" in calls[2]
    assert str(archive) in calls[1] and str(release.checksum_path(archive)) in calls[1]

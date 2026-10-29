import hashlib
import io
import json
import zipfile

import httpx
import pytest

from scripts import setup_translation as installer


def fixture_package(extra=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in installer.REQUIRED_FILES:
            archive.writestr("model-package/" + name, b"fixture")
        if extra:
            archive.writestr("model-package/" + extra, b"unsafe")
    return buffer.getvalue()


def mock_download(tmp_path, monkeypatch, content):
    lock = tmp_path / "models.lock.json"
    specs = {
        pair: {
            "url": f"https://models.invalid/{pair}.argosmodel",
            "version": "1.9",
            "sha256": hashlib.sha256(content).hexdigest(),
            "archive_bytes": len(content),
        }
        for pair in ("en_zh", "zh_en")
    }
    lock.write_text(json.dumps(specs), "utf-8")
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(200, content=content)

    client = httpx.Client
    monkeypatch.setattr(
        installer.httpx,
        "Client",
        lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs),
    )
    return lock, specs, calls


def test_pinned_models_install_and_cache_without_floating_index(tmp_path, monkeypatch):
    lock, specs, calls = mock_download(tmp_path, monkeypatch, fixture_package())
    models = tmp_path / "models"
    installer.install(models, lock)
    installer.install(models, lock)
    assert len(calls) == 2
    assert json.loads((models / "manifest.json").read_text("utf-8")) == specs
    for pair in specs:
        assert all((models / pair / name).is_file() for name in installer.REQUIRED_FILES)


def test_bad_download_hash_never_reaches_extraction(tmp_path, monkeypatch):
    lock, specs, _ = mock_download(tmp_path, monkeypatch, fixture_package())
    specs["en_zh"]["sha256"] = "0" * 64
    lock.write_text(json.dumps(specs), "utf-8")
    models = tmp_path / "models"
    with pytest.raises(ValueError, match="校验失败"):
        installer.install(models, lock)
    assert not (models / "manifest.json").exists()
    assert not (models / "en_zh").exists()


def test_checked_archive_still_rejects_path_traversal(tmp_path, monkeypatch):
    lock, _, _ = mock_download(tmp_path, monkeypatch, fixture_package("../../escape"))
    with pytest.raises(ValueError, match="路径无效"):
        installer.install(tmp_path / "models", lock)
    assert not (tmp_path / "escape").exists()

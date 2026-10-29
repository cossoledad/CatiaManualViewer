import shutil
from pathlib import Path

from catia_manual import config, runtime


def simulate_portable(tmp_path, monkeypatch):
    portable = tmp_path / "软件便携版"
    portable.mkdir()
    shutil.copy2(Path(__file__).parents[1] / "packaging/config.default.toml", portable)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    (unrelated / "config.local.toml").write_text("[translation]\nprovider='invalid'", "utf-8")
    monkeypatch.chdir(unrelated)
    monkeypatch.setattr(runtime, "__compiled__", object(), raising=False)
    monkeypatch.setattr(runtime.sys, "executable", str(portable / "CatiaManualViewer.exe"))
    return portable


def test_portable_ignores_cwd_and_saves_beside_executable(tmp_path, monkeypatch):
    portable = simulate_portable(tmp_path, monkeypatch)
    settings = config.load_settings()
    assert settings.root == portable / "manuals/English"
    assert settings.database == portable / "data/manuals.sqlite3"
    assert settings.translation["provider"] == "glossary"
    updated = config.save_settings(settings, {"appearance": {"font_size": 12}})
    assert updated.config_path == portable / "config.local.toml"
    assert "font_size = 12" in updated.config_path.read_text("utf-8")
    assert "font_size = 10" in (portable / "config.default.toml").read_text("utf-8")
    assert updated.root == settings.root


def test_portable_moves_relative_paths_and_prefers_personal_config(tmp_path, monkeypatch):
    portable = simulate_portable(tmp_path, monkeypatch)
    settings = config.load_settings()
    config.save_settings(settings, {"documentation": {"root": portable / "docs/English"}})
    moved = tmp_path / "moved"
    shutil.copytree(portable, moved)
    monkeypatch.setattr(runtime.sys, "executable", str(moved / "CatiaManualViewer.exe"))
    updated = config.load_settings()
    assert updated.root == moved / "docs/English"
    assert updated.database == moved / "data/manuals.sqlite3"
    assert Path(updated.translation["model_dir"]) == moved / "data/models"

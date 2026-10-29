from catia_manual import config


def test_config_relative_paths_follow_config_location_not_cwd(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    settings_file = project / "custom.toml"
    settings_file.write_text(
        """[documentation]
root = 'manuals'
database = 'data/index.sqlite3'
[catia]
launcher = 'bin/CATSTART.exe'
environment = 'custom-environment'
environment_dir = 'CATEnv'
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    settings = config.load_settings(settings_file)
    assert settings.root == project / "manuals"
    assert settings.database == project / "data/index.sqlite3"


def test_installed_wheel_can_fall_back_without_checkout_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "__file__", str(tmp_path / "site/catia_manual/config.py"))
    settings = config.load_settings()
    assert settings.database == tmp_path / "data/manuals.sqlite3"
    assert settings.environment == "CATIA_P3.V5-6R2023.B33"

"""Persistence and provider isolation; only synthetic credentials are used."""

import json
import subprocess
import sys
from dataclasses import replace

import pytest

from catia_manual.config import load_settings, save_settings
from catia_manual.core.capabilities import detect_capabilities
from catia_manual.core.credentials import protect_key, resolve_api_key, unprotect_key
from catia_manual.core.providers import provider_profile
from catia_manual.core.translation import make_provider

windows = pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI")


@windows
def test_persisted_profiles_survive_fresh_process_without_plaintext(tmp_path):
    config = tmp_path / "config.local.toml"
    config.write_text("", "utf-8")
    settings = load_settings(config)
    keys = {"deepl": "fixture-deepl-测试", "chat-completions": "fixture-chat"}
    profiles = {
        name: {"api_key_encrypted": protect_key(key), "api_key_env": "SHARED_TEST_ENV"}
        for name, key in keys.items()
    }
    profiles["chat-completions"].update(endpoint="https://fixture.invalid/v1", model="fixture")
    settings = save_settings(settings, {"translation": {"provider": "deepl", "profiles": profiles}})
    # A reading preference save must preserve keys belonging to every service.
    save_settings(settings, {"appearance": {"reader_zoom": 130}})
    assert all(key not in config.read_text("utf-8") for key in keys.values())
    code = """
import json, sys
from pathlib import Path
from catia_manual.config import load_settings
from catia_manual.core.credentials import resolve_api_key
from catia_manual.core.providers import provider_profile
settings = load_settings(Path(sys.argv[1]))
profiles = {'profiles': settings.translation['profiles']}
result = {name: resolve_api_key(provider_profile(profiles, name))
          for name in ('deepl', 'chat-completions')}
print(json.dumps(result))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(config)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == keys
    assert load_settings(config).appearance["reader_zoom"] == 130


@windows
def test_saved_key_drives_provider_and_capabilities(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPL_TEST_UNUSED", raising=False)
    config = {
        "provider": "deepl",
        "api_key_env": "DEEPL_TEST_UNUSED",
        "api_key_encrypted": protect_key("fixture-key"),
    }
    path = tmp_path / "config.local.toml"
    path.write_text("", "utf-8")
    settings = replace(load_settings(path), translation=config)
    assert detect_capabilities(settings).selection_translation.available
    assert make_provider(config).api_key_override == "fixture-key"
    assert "fixture-key" not in make_provider(config).name


def test_override_and_environment_fallback_are_explicit(monkeypatch):
    monkeypatch.setenv("KEY_TEST_FALLBACK", "fixture-env")
    config = {"api_key_env": "KEY_TEST_FALLBACK", "api_key_encrypted": "damaged"}
    assert resolve_api_key(config) == "fixture-env"
    assert resolve_api_key({**config, "api_key_override": "fixture-draft"}) == "fixture-draft"
    assert resolve_api_key({**config, "api_key_override": ""}) == "fixture-env"
    monkeypatch.delenv("KEY_TEST_FALLBACK")
    with pytest.raises(ValueError, match="重新填写"):
        resolve_api_key(config)


@windows
def test_tampered_ciphertext_is_rejected():
    sealed = protect_key("fixture-secret")
    with pytest.raises(ValueError, match="重新填写"):
        unprotect_key(sealed[:20] + "!" + sealed[21:])
    assert protect_key("") == unprotect_key("") == ""


def test_session_override_is_not_written_by_any_save(tmp_path):
    path = tmp_path / "config.local.toml"
    path.write_text("", "utf-8")
    settings = load_settings(path)
    save_settings(
        settings,
        {
            "translation": {
                "api_key_override": "fixture-transient",
                "profiles": {"deepl": {"api_key_override": "fixture-nested"}},
            }
        },
    )
    text = path.read_text("utf-8")
    assert "fixture-transient" not in text and "fixture-nested" not in text


def test_explicit_empty_profile_fields_stay_empty():
    profile = provider_profile(
        {"provider": "deepl", "endpoint": "", "api_key_encrypted": ""}, "deepl"
    )
    assert profile["endpoint"] == profile["api_key_encrypted"] == ""

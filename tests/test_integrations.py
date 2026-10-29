from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from catia_manual.config import Settings
from catia_manual.core.index import ManualIndex
from catia_manual.core.translation import (
    GlossaryProvider,
    LibreTranslateProvider,
    TranslationService,
)
from catia_manual.integrations.catia import CatiaLauncher


def test_translation_cache_is_provider_target_and_content_specific(corpus):
    provider = MagicMock(name="test translator")
    provider.name = "test-v1"
    provider.translate.return_value = "创建凸台"
    with ManualIndex(corpus / "index.sqlite3", corpus) as index:
        service = TranslationService(index, provider)
        assert service.translate("Create a pad").cached is False
        assert service.translate("Create a pad").cached is True
        assert provider.translate.call_count == 1
        service.translate("Create another pad")
        service.translate("Create a pad", "ja")
        assert provider.translate.call_count == 3
        with pytest.raises(ValueError):
            service.translate(" ")


def test_offline_glossary_is_explicit_about_limitations():
    result = GlossaryProvider().translate("Creating pads from a sketch", "zh-CN")
    assert "非整句翻译" in result and "凸台" in result and "草图" in result


def test_libretranslate_contract_without_external_request(corpus, monkeypatch):
    httpx = pytest.importorskip("httpx")

    def respond(request):
        import json

        payload = json.loads(request.content)
        assert payload == {"q": "Create a pad", "source": "en", "target": "zh", "format": "text"}
        return httpx.Response(200, json={"translatedText": "创建凸台"})

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs)
    )
    provider = LibreTranslateProvider("http://127.0.0.1:5000/translate", "TEST_UNUSED_KEY")
    assert provider.translate("Create a pad", "zh-CN") == "创建凸台"


def test_launch_argv_preserves_spaces_and_blocks_scripts(corpus):
    settings = Settings(
        corpus,
        corpus / "db.sqlite3",
        corpus / "Program Files/CATSTART.exe",
        "CATIA_P3.V5-6R2023.B33",
        corpus / "CATEnv",
    )
    launcher = CatiaLauncher(settings)
    command = launcher.command("online/prtug_C2/samples/Pad 1.CATPart")
    assert command[-2] == "-object"
    assert command[-1] == str(corpus / "online/prtug_C2/samples/Pad 1.CATPart")
    assert command[2] == "CNEXT.exe" and command[4] == settings.environment
    with pytest.raises(ValueError):
        launcher.command("online/prtug_C2/pad.htm")
    with pytest.raises(ValueError):
        CatiaLauncher(replace(settings)).sample("../escape.CATPart")


def test_existing_catia_session_opens_document_without_spawning(corpus, monkeypatch):
    pythoncom = pytest.importorskip("pythoncom")
    client = pytest.importorskip("win32com.client")

    application = MagicMock()
    monkeypatch.setattr(client, "GetActiveObject", lambda _: application)
    monkeypatch.setattr(pythoncom, "CoInitialize", lambda: None)
    monkeypatch.setattr(pythoncom, "CoUninitialize", lambda: None)
    launcher = CatiaLauncher(
        Settings(corpus, corpus / "db", corpus / "CATSTART.exe", "test", corpus / "CATEnv")
    )
    result = launcher.open("online/prtug_C2/samples/Pad 1.CATPart")
    assert result["method"] == "com"
    application.Documents.Open.assert_called_once_with(result["path"])

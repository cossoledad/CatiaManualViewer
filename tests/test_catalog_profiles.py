from pathlib import Path

import pytest

from catia_manual.config import load_settings, save_settings
from catia_manual.core.catalog import ManualCatalog
from catia_manual.core.index import ManualIndex
from catia_manual.core.providers import PROVIDERS, provider_profile
from catia_manual.core.search import SearchService
from catia_manual.core.translation import make_provider


@pytest.fixture
def functional_corpus(corpus):
    other = corpus / "online/asmug_C2"
    other.mkdir()
    (other / "asmugpr01.htm").write_text(
        "<h1>Assembly Design</h1><p>Create a pad in an assembly.</p>"
    )
    (corpus / "online/CATIA_P3_TOC.xml").write_text("""<Root><ITEM name="CATIA P3">
    <ITEM name="Mechanical Design"><DITEM dir="prtug_C2" name="Part Design"/>
      <DITEM dir="missing_C2" name="Missing manual"/>
      <DITEM dir="../../outside" name="Invalid"/></ITEM>
    <ITEM name="Product Synthesis"><DITEM dir="asmug_C2" name="Assembly Design"/></ITEM>
    </ITEM></Root>""")
    return corpus


def test_catalog_uses_official_labels_order_and_available_manuals(functional_corpus):
    catalog = ManualCatalog(functional_corpus)
    assert [group.id for group in catalog.categories] == ["mechanical-design", "product-synthesis"]
    category = catalog.category("机械设计")
    assert category.manuals[0].label == "零件设计"
    assert category.modules == ["prtug_C2"]
    assert "missing_C2" not in catalog.manuals
    assert catalog.manuals["asmug_C2"].entry.endswith("asmugpr01.htm")


def test_category_scope_is_applied_before_result_limit(functional_corpus):
    with ManualIndex(functional_corpus / "index.sqlite3", functional_corpus) as index:
        index.build()
        service = SearchService(index, {"provider": "glossary"})
        response = service.search("创建凸台", modules=["prtug_C2"])
        assert response.hits and {hit.module for hit in response.hits} == {"prtug_C2"}
        assert {hit.module for hit in service.search("pad", modules=["asmug_C2"]).hits} == {
            "asmug_C2"
        }
        assert not service.search("pad", modules=[]).hits


def test_new_library_gets_new_index_without_rebinding_old_database(tmp_path):
    original = tmp_path / "original"
    copied = tmp_path / "copied"
    original.mkdir()
    copied.mkdir()
    database = tmp_path / "data/manuals.sqlite3"
    with ManualIndex(database, original):
        pass
    config = tmp_path / "config.local.toml"
    config.write_text(f"[documentation]\nroot = '{original}'\ndatabase = 'data/manuals.sqlite3'\n")
    initial = load_settings(config)
    updated = save_settings(initial, {"documentation": {"root": str(copied)}})
    assert updated.database != initial.database
    assert not updated.database.exists()
    with ManualIndex(initial.database, original) as old:
        assert (
            Path(
                old.connection.execute("SELECT value FROM metadata WHERE key='root'").fetchone()[0]
            )
            == original
        )
    with ManualIndex(updated.database, copied):
        pass
    assert load_settings(config).database == updated.database


def test_portable_paths_profiles_and_unknown_sections_survive_save(tmp_path):
    path = tmp_path / "config.local.toml"
    path.write_text("[documentation]\nroot = 'manuals'\n[user]\nnote = 'keep me'\n")
    settings = load_settings(path)
    profiles = {
        "libretranslate": {
            "endpoint": "http://localhost:5001/translate",
            "api_key_env": "LIBRE_TEST",
        },
        "chat-completions": {
            "endpoint": "https://example.invalid/v1",
            "model": "mine",
            "api_key_env": "LLM_TEST",
        },
    }
    saved = save_settings(
        settings,
        {
            "translation": {"provider": "local", "profiles": profiles},
            "appearance": {"reader_zoom": 120},
        },
    )
    assert saved.translation["profiles"]["chat-completions"]["model"] == "mine"
    assert saved.appearance["reader_zoom"] == 120
    text = path.read_text("utf-8")
    assert "keep me" in text and 'model_dir = "data/models"' in text
    assert str(tmp_path) not in text
    assert (
        provider_profile(saved.translation, "libretranslate")["endpoint"]
        == "http://localhost:5001/translate"
    )


def test_provider_defaults_do_not_invent_an_llm_service():
    assert not provider_profile({}, "chat-completions")["endpoint"]
    assert not provider_profile({}, "chat-completions")["model"]
    for provider in ("mymemory", "deepl", "libretranslate"):
        assert PROVIDERS[provider]["endpoint"].startswith("http")
        assert make_provider({"provider": provider})


def test_mymemory_protocol_enforces_byte_limit_and_decodes_entities(monkeypatch):
    httpx = pytest.importorskip("httpx")
    requests = []

    def respond(request):
        assert len(request.url.params["q"].encode("utf-8")) <= 500
        assert request.url.params["langpair"] == "zh-CN|en"
        requests.append(request)
        return httpx.Response(
            200, json={"responseStatus": 200, "responseData": {"translatedText": "a &amp; b"}}
        )

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs)
    )
    result = make_provider({"provider": "mymemory"}).translate("测试" * 150, "en")
    assert len(requests) == 2 and result == "a & b a & b"


def test_deepl_uses_header_auth_and_official_default_endpoint(monkeypatch):
    httpx = pytest.importorskip("httpx")

    def respond(request):
        import json

        assert str(request.url) == "https://api-free.deepl.com/v2/translate"
        assert request.headers["Authorization"] == "DeepL-Auth-Key test-key"
        assert json.loads(request.content) == {"text": ["Click Pad."], "target_lang": "ZH"}
        return httpx.Response(200, json={"translations": [{"text": "点击凸台。"}]})

    original = httpx.Client
    monkeypatch.setenv("DEEPL_API_KEY", "test-key")
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs)
    )
    assert make_provider({"provider": "deepl"}).translate("Click Pad.", "zh-CN") == "点击凸台。"

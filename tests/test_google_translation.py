import json
from urllib.parse import quote

import httpx
import pytest

from catia_manual.core import google_translation as google
from catia_manual.core.translation import make_provider


@pytest.fixture
def mock_google(monkeypatch):
    monkeypatch.setattr(google, "wait_turn", lambda: None)
    original = httpx.Client

    def install(respond):
        monkeypatch.setattr(
            httpx,
            "Client",
            lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
        )

    return install


def test_google_joins_all_segments_without_keys(mock_google):
    def respond(request):
        assert request.url.params["client"] == "gtx"
        assert request.url.params["sl"] == "auto"
        assert request.url.params["tl"] == "zh-CN"
        assert request.url.params["q"] == "Create a constraint."
        assert "authorization" not in request.headers
        assert "key" not in request.url.params
        return httpx.Response(200, json=[[["创建", "Create"], ["约束。", "a constraint."]]])

    mock_google(respond)
    provider = make_provider({"provider": "google-gtx", "api_key_encrypted": "broken"})
    assert provider.translate("Create a constraint.", "zh-CN") == "创建约束。"


def test_google_chunking_preserves_text_and_bounds_encoded_urls(mock_google):
    text = ("Select two faces. 选择两个面。\n" * 180) + "last"
    requests = []

    def respond(request):
        value = request.url.params["q"]
        requests.append(value)
        assert len(value) <= 1000 and len(quote(value, safe="")) <= 4500
        return httpx.Response(200, json=[[[value, value]]])

    mock_google(respond)
    assert google.GoogleTranslateProvider().translate(text, "en") == text
    assert len(requests) > 1 and "".join(requests) == text


@pytest.mark.parametrize(
    "payload", [None, {}, [], [None], [[]], [[[None]]], [[[]]], [[[" "]]], [["bad"]]]
)
def test_google_rejects_invalid_or_empty_responses(mock_google, payload):
    mock_google(lambda _: httpx.Response(200, content=json.dumps(payload)))
    with pytest.raises(ValueError, match="格式异常"):
        google.GoogleTranslateProvider().translate("Select a face.", "zh-CN")


@pytest.mark.parametrize("status", [403, 429, 500, 302])
def test_google_http_errors_do_not_echo_source_or_retry_unbounded(mock_google, status):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status)

    mock_google(respond)
    with pytest.raises(ValueError) as error:
        google.GoogleTranslateProvider().translate("private-source", "zh-CN")
    assert "private-source" not in str(error.value)
    assert len(requests) == 1


def test_google_timeout_has_safe_error(mock_google):
    def respond(request):
        raise httpx.ReadTimeout("private-source", request=request)

    mock_google(respond)
    with pytest.raises(ValueError, match="连接失败") as error:
        google.GoogleTranslateProvider().translate("private-source", "zh-CN")
    assert "private-source" not in str(error.value)


def test_google_settings_survive_restart_without_requiring_a_key(tmp_path):
    from catia_manual.config import load_settings, save_settings
    from catia_manual.core.capabilities import detect_capabilities

    path = tmp_path / "config.toml"
    path.write_text("[translation]\nprovider='glossary'", "utf-8")
    settings = save_settings(
        load_settings(path),
        {
            "translation": {
                "provider": "google-gtx",
                "endpoint": google.DEFAULT_ENDPOINT,
                "api_key_encrypted": "dpapi:v1:unreadable-on-this-host",
            }
        },
    )
    restored = load_settings(settings.config_path)
    assert restored.translation["provider"] == "google-gtx"
    assert make_provider(restored.translation).endpoint == google.DEFAULT_ENDPOINT
    assert detect_capabilities(restored).page_translation.available


def test_google_persists_only_complete_successful_translations(mock_google, tmp_path):
    from catia_manual.core.index import ManualIndex
    from catia_manual.core.translation import TranslationService

    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=[[["约束", "constraint"]]])

    mock_google(respond)
    with ManualIndex(tmp_path / "index.sqlite3", tmp_path) as index:
        service = TranslationService(index, make_provider({"provider": "google-gtx"}))
        assert not service.translate("constraint").cached
        assert service.translate("constraint").cached
        assert len(requests) == 1
        mock_google(lambda _: httpx.Response(429))
        with pytest.raises(ValueError):
            service.translate("offset")
        assert index.connection.execute("SELECT count(*) FROM translations").fetchone()[0] == 1

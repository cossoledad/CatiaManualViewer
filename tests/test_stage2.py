from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from catia_manual.config import load_settings, save_translation
from catia_manual.core.index import ManualIndex
from catia_manual.core.navigation import Location, NavigationHistory
from catia_manual.core.parser import ManualParser
from catia_manual.core.paths import resolve_link
from catia_manual.core.query import plan_query
from catia_manual.core.search import SearchService
from catia_manual.core.translation import ChatCompletionsProvider


def test_history_tracks_anchors_scroll_forward_and_branching():
    history = NavigationHistory()
    history.visit(Location("a.htm", title="A"))
    history.save_scroll(0, 640)
    history.visit(Location("a.htm", "step", "A"))
    history.save_scroll(0, 990)
    history.visit(Location("b.htm", title="B"))
    assert history.can_back and not history.can_forward
    assert history.jump(1).fragment == "step"
    assert history.current.y == 990 and history.can_forward
    assert history.jump(0).y == 640
    history.visit(Location("c.htm", title="C"))
    assert [entry.path for entry in history.entries] == ["a.htm", "c.htm"]
    assert not history.can_forward
    history.visit(Location("c.htm", title="C"))
    assert len(history.entries) == 2


def test_offline_domain_words_preserve_catia_meaning_in_plural(tmp_path):
    from catia_manual.core.offline_translation import LocalTranslationProvider

    provider = LocalTranslationProvider(tmp_path)
    assert provider.translate("Pads", "zh-CN") == "凸台（拉伸）"
    assert provider.translate("Sketches", "zh-CN") == "草图"


@pytest.mark.parametrize(
    "query,expected",
    [
        ("创建凸台", ("create", "pad")),
        ("装配约束", ("assembly", "constraint")),
        ("曲面偏置", ("surface", "offset")),
        ("如何创建一个拉伸", ("pad", "extrusion")),
        ("Pad1.CATPart", ("Pad1", "CATPart")),
    ],
)
def test_chinese_query_expansion(query, expected):
    plan = plan_query(query)
    assert not plan.unknown
    assert all(word in plan.match for word in expected)


def test_chinese_search_uses_original_index_and_preserves_unknown_concepts(corpus):
    with ManualIndex(corpus / "index.sqlite3", corpus) as index:
        index.build()
        assert index.search("创建凸台")[0].path.endswith("pad.htm")
        assert index.search("凹槽")[0].title == "Pocket"
        response = SearchService(index, {"provider": "glossary"}).search("超级未知零件")
        assert not response.hits and response.plan.unknown


def test_search_translation_fallback_is_explicit_and_cached(corpus, monkeypatch):
    from catia_manual.core import search

    class Provider:
        name = "test-query-provider"
        calls = 0

        def translate(self, text, target):
            assert text == "超级未知" and target == "en"
            self.calls += 1
            return "Pocket"

    provider = Provider()
    monkeypatch.setattr(search, "make_provider", lambda _: provider)
    with ManualIndex(corpus / "index.sqlite3", corpus) as index:
        index.build()
        service = SearchService(index, {"provider": "test"})
        assert service.search("超级未知").hits[0].title == "Pocket"
        response = service.search("超级未知")
        assert provider.calls == 1 and response.plan.method == "terminology+translation"


def test_existing_readers_do_not_request_write_lock(corpus):
    with ManualIndex(corpus / "index.sqlite3", corpus) as writer:
        writer.build()
        writer.connection.execute("BEGIN IMMEDIATE")
        with ManualIndex(corpus / "index.sqlite3", corpus) as reader:
            assert reader.search("pad")
        writer.connection.rollback()


def test_legacy_build_time_path_and_server_links_stay_inside_root(corpus):
    source = corpus / "online/prtug_C2/pad.htm"
    link = resolve_link(corpus, source, "../../../../PrtEnglish/prtug.doc/src/other.htm#anchor")
    assert link.target == "online/prtug_C2/other.htm" and link.exists
    server = resolve_link(corpus, source, "http://dsdoc/doc21/online/prtug_C2/other.htm#anchor")
    assert server.target == link.target and server.fragment == "anchor"
    bad = resolve_link(corpus, source, "../../../prtug.doc/src/../../../../outside.htm")
    assert bad.kind == "blocked"


def test_static_generated_api_links_and_next_previous(corpus):
    interfaces = corpus / "online/interfaces"
    (interfaces / "_index").mkdir(parents=True)
    folder = interfaces / "InfInterfaces"
    folder.mkdir()
    (interfaces / "_index/jslink.js").write_text(
        'var objetIdx=[];objetIdx["Other"]="../InfInterfaces/other.htm";'
        'aliasTab["CATIAOther"]="Other";'
    )
    (folder / "jsFwLink.js").write_text(
        'objet["interface"]["Current"]=1;'
        'objet["interface"][0]="other.htm";objet["interface"][2]="next.htm";'
    )
    (folder / "current.htm").write_text("""<h1>Current</h1><script>
    function getCurrentObjectName() { return "Current"; }
    function getCurrentType() { return "interface"; }
    </script><script>activateLinkAnchor('CATIAOther','method','Other method')</script>
    <a href="javascript:openLinkNP(getCurrentObjectName(),getCurrentType(),'0')">Previous</a>
    <a href="javascript:openLinkNP(getCurrentObjectName(),getCurrentType(),'1')">Next</a>
    <script>arbitraryCall('must not execute')</script>""")
    parser = ManualParser(corpus)
    doc = parser.parse("online/interfaces/InfInterfaces/current.htm")
    links = {link.label: link for link in doc.links}
    assert links["Other method"].fragment == "method"
    assert links["Other method"].target.endswith("other.htm")
    assert links["Next"].target.endswith("next.htm")
    rendered = BeautifulSoup(parser.render(doc.path), "lxml")
    assert not rendered.find_all("script")
    assert rendered.find("a", string="Other method")["href"].endswith("other.htm#method")


def test_api_adapter_contract_and_key_not_in_body(monkeypatch):
    httpx = pytest.importorskip("httpx")

    def respond(request):
        import json

        payload = json.loads(request.content)
        assert str(request.url) == "https://translation.example/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-only-key"
        assert payload["model"] == "user-specified-model"
        assert payload["messages"][1] == {"role": "user", "content": "Click Pad."}
        assert "test-only-key" not in request.content.decode()
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": "点击 Pad。"}}]}
        )

    original = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs)
    )
    monkeypatch.setenv("TEST_TRANSLATION_KEY", "test-only-key")
    provider = ChatCompletionsProvider(
        "https://translation.example/v1", "user-specified-model", "TEST_TRANSLATION_KEY"
    )
    assert provider.translate("Click Pad.", "zh-CN") == "点击 Pad。"


def test_save_translation_preserves_other_settings_and_does_not_edit_example(tmp_path):
    example = tmp_path / "config.example.toml"
    template = Path(__file__).resolve().parents[1] / "config.example.toml"
    example.write_bytes(template.read_bytes())
    settings = load_settings(example)
    saved = save_translation(
        settings, {"provider": "chat-completions", "model": "test-model", "automatic": False}
    )
    assert example.read_bytes() == template.read_bytes()
    assert saved.config_path.name == "config.local.toml"
    reloaded = load_settings(saved.config_path)
    assert reloaded.root == settings.root and reloaded.environment == settings.environment
    assert reloaded.translation["model"] == "test-model"
    assert reloaded.translation["automatic"] is False

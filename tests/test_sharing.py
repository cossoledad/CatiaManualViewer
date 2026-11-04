import asyncio
from dataclasses import replace

import httpx
import pytest
from test_agent import agent_library as agent_library
from test_agent import manual_library as manual_library

from catia_manual.agent.server import create_app
from catia_manual.core.agent import AgentError, AgentKnowledge
from catia_manual.core.index import ManualIndex
from catia_manual.core.sharing import PageLink, browser_link


def test_share_survives_service_restart_and_independent_index_identity(agent_library):
    first = AgentKnowledge(agent_library)
    hit = first.search("相合约束")["results"][0]
    uri = hit["share_uri"]
    assert uri.startswith("catia-manual://page/v1?")
    assert str(agent_library.root) not in uri
    second = AgentKnowledge(agent_library)
    resolved = second.resolve_share(uri)
    assert resolved["same_library"]
    assert second.fetch(resolved["id"])["path"].endswith("coincide.htm")
    assert PageLink.parse(second.fetch(resolved["id"])["share_uri"]) == PageLink.parse(uri)
    document = second.parser.parse(hit["source"]["document"]["path"])
    block_link = replace(PageLink.parse(uri), block_id=document.blocks[0].id).to_uri()
    block_id = second.resolve_share(block_link)["id"]
    assert PageLink.parse(second.fetch(block_id)["share_uri"]).block_id == document.blocks[0].id
    http_link = browser_link("http://127.0.0.1:8765", uri)
    assert PageLink.parse(http_link) == PageLink.parse(uri)
    assert second.resolve_share(http_link)["title"] == resolved["title"]
    with ManualIndex(agent_library.database, agent_library.root) as index:
        index.set_meta("library_id", "00000000-0000-4000-8000-000000000005")
        index.connection.commit()
    third = AgentKnowledge(agent_library)
    copied = third.resolve_share(uri)
    assert not copied["same_library"]
    assert copied["source"]["document"]["library_id"] == third.library_id
    assert third.fetch(copied["id"])["blocks"]


def test_share_refuses_changed_documents_and_absent_positions(agent_library):
    service = AgentKnowledge(agent_library)
    link = PageLink.parse(service.search("相合约束")["results"][0]["share_uri"])
    for altered in (replace(link, fragment="missing"), replace(link, block_id="a" * 20)):
        with pytest.raises(AgentError, match="不存在"):
            service.resolve_share(altered.to_uri())
    path = agent_library.root / link.path
    path.write_bytes(path.read_bytes() + b"<p>Revised</p>")
    with pytest.raises(AgentError, match="变化"):
        service.resolve_share(link.to_uri())
    with ManualIndex(agent_library.database, agent_library.root) as index:
        index.build()
    with pytest.raises(AgentError, match="版本不一致"):
        AgentKnowledge(agent_library).resolve_share(link.to_uri())


@pytest.mark.parametrize(
    "uri",
    [
        "file:///C:/private.htm",
        "catia-manual://page/v2?x=y",
        "http://host/nope?uri=x",
        "catia-manual://page/v1?library=x&path=../../secret.htm&sha256=x",
        "catia-manual://page/v1?library=a&library=b&path=x.htm&sha256=x",
        "catia-manual://page/v1?" + "x" * 13000,
    ],
)
def test_invalid_shares_are_rejected(uri):
    with pytest.raises(ValueError):
        PageLink.parse(uri)


def test_protocol_entry_rejects_additional_switches(monkeypatch):
    from catia_manual import entry

    monkeypatch.setattr("sys.argv", ["viewer.exe", "--share-uri", "catia-manual://page/v1",
                                     "--diagnostics", "injected.json"])
    assert entry.main() == 1


@pytest.mark.parametrize("authenticated", [False, True])
def test_browser_reader_respects_network_and_token_and_logs_no_link(agent_library, authenticated):
    token = "test-" + "a" * 40
    settings = replace(agent_library, agent={"port": 18765, "require_token": authenticated})
    app, service = create_app(settings, token if authenticated else None)
    uri = service.search("相合约束")["results"][0]["share_uri"]

    async def run():
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 4000))
        async with app.app.router.lifespan_context(app.app):
            async with httpx.AsyncClient(
                transport=transport, base_url="http://127.0.0.1:18765"
            ) as c:
                response = await c.get("/share", params={"uri": uri})
                assert response.status_code == (401 if authenticated else 200)
                response = await c.get(
                    "/share", params={"uri": uri}, headers={"Authorization": "Bearer " + token}
                )
                assert response.status_code == 200
                assert "Managing Coincidence" in response.text
                assert "catia-manual://" in response.text
                assert "<script" not in response.text
                assert "file:///" not in response.text and str(settings.root) not in response.text
                assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
                assert (
                    await c.get(
                        "/share",
                        params={"uri": "../private.htm"},
                        headers={"Authorization": "Bearer " + token},
                    )
                ).status_code == 400

    asyncio.run(run())
    log = app.audit.path.read_text("utf-8")
    assert "catia-manual:" not in log and "coincide.htm" not in log and token not in log

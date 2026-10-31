import asyncio
import json
import sqlite3
import sys
from dataclasses import replace

import pytest
from test_structure import manual_library as manual_library

from catia_manual.config import load_settings
from catia_manual.core.agent import AgentError, AgentKnowledge
from catia_manual.core.index import ManualIndex
from catia_manual.core.readonly_index import ReadOnlyIndex


@pytest.fixture
def agent_library(manual_library, tmp_path):
    config = tmp_path / "config.toml"
    config.write_text("[documentation]\nroot='.'\ndatabase='data/index.sqlite3'", "utf-8")
    settings = replace(
        load_settings(config),
        translation={
            "provider": "chat-completions",
            "endpoint": "https://never-call.invalid",
            "api_key_override": "not-exported",
        },
        search={"mode": "semantic"},
    )
    with ManualIndex(settings.database, settings.root) as index:
        index.build()
    return settings


def test_agent_search_preserves_ranking_scope_and_sources_without_writes(
    agent_library, monkeypatch
):
    from catia_manual.core import translation

    def forbidden(*_):
        raise AssertionError("agent must not use a translation provider")

    monkeypatch.setattr(translation, "make_provider", forbidden)
    settings = agent_library
    before = settings.database.read_bytes()
    service = AgentKnowledge(settings)
    library = service.describe_library()
    assert library["index_ready"] and library["capabilities"]["readonly"]
    assert str(settings.root) not in json.dumps(library)
    for query, name in [
        ("相合约束", "coincide.htm"),
        ("偏移约束", "offset.htm"),
        ("管理约束", "overview.htm"),
        ("Managing Constraints", "overview.htm"),
        ("Coincidence Constraint", "coincide.htm"),
    ]:
        result = service.search(query, manual="asmug_C2")
        hit = result["results"][0]
        assert hit["source"]["document"]["path"].endswith(name)
        assert hit["placement"]["manual"] == "asmug_C2"
        page = service.fetch(hit["id"])
        assert page["blocks"] and page["content_hash"] == hit["source"]["content_hash"]
        assert page["blocks"][0]["source"]["document"]["library_id"] == library["library_id"]
    assert not service.search("Unrelated", manual="asmug_C2")["results"]
    assert not service.search("未登记的某种特殊概念")["results"]
    assert settings.database.read_bytes() == before


def test_readonly_index_never_creates_or_upgrades_and_rejects_writes(agent_library, tmp_path):
    with ReadOnlyIndex(agent_library.database, agent_library.root) as index:
        with pytest.raises(sqlite3.OperationalError):
            index.set_meta("test", "forbidden")
        with pytest.raises(sqlite3.OperationalError):
            index.connection.execute("CREATE TEMP TABLE forbidden(value)")
        with pytest.raises(sqlite3.OperationalError):
            index.set_alias("任意", "arbitrary")
    missing = tmp_path / "absent/data.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        ReadOnlyIndex(missing, agent_library.root)
    assert not missing.parent.exists()


def test_browse_preserves_hierarchy_and_exposes_article_outlines(agent_library):
    service = AgentKnowledge(agent_library)
    category = service.browse()["items"][0]
    manuals = service.browse(category["id"])["items"]
    assembly = next(m for m in manuals if m["manual"] == "asmug_C2")
    chapters = service.browse(assembly["id"])["items"]
    assert chapters and any(c["article_id"] for c in chapters)
    hit = service.search("相合约束")["results"][0]
    headings = service.browse(hit["article_id"])["items"]
    assert headings and headings[0]["source"]
    assert service.fetch(headings[0]["id"])["scope"] == "section"


def test_paging_reconstructs_long_blocks_without_loss_and_cursors_are_bound(agent_library):
    settings = agent_library
    path = settings.root / "online/shared_v17/coincide.htm"
    path.write_text(
        path.read_text("utf-8")
        + "<h2>Details</h2><p>"
        + "Detail text. " * 700
        + "</p><h2>Finish</h2><p>Confirm.</p>",
        "utf-8",
    )
    with ManualIndex(settings.database, settings.root) as index:
        index.build()
    service = AgentKnowledge(settings)
    hit = service.search("相合约束")["results"][0]
    page, blocks, cursor = service.fetch(hit["article_id"], max_chars=1000), [], None
    while True:
        assert len("".join(b["text"] for b in page["blocks"])) <= 1000
        blocks.extend(page["blocks"])
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
        page = service.fetch(hit["article_id"], cursor, max_chars=1000)
    original = service.parser.parse("online/shared_v17/coincide.htm")
    assert "".join(b["text"] for b in blocks) == "".join(b.text for b in original.blocks)
    assert any(b["continued"] for b in blocks)
    with pytest.raises(AgentError, match="不属于"):
        service.fetch(hit["id"], cursor, max_chars=1000)


def test_stale_source_revision_and_reconnect_are_explicit(agent_library):
    settings = agent_library
    service = AgentKnowledge(settings)
    hit = service.search("相合约束")["results"][0]
    reconnected = AgentKnowledge(settings)
    with pytest.raises(AgentError) as error:
        reconnected.fetch(hit["id"])
    assert error.value.code == "INVALID_REFERENCE"
    resolved = reconnected.resolve_source(hit["source"])
    assert resolved["status"] in {"exact", "document", "anchor"}
    assert reconnected.fetch(resolved["id"])["blocks"]
    path = settings.root / hit["source"]["document"]["path"]
    path.write_text(path.read_text("utf-8") + "<p>Changed.</p>", "utf-8")
    with pytest.raises(AgentError) as error:
        service.fetch(hit["id"])
    assert error.value.code == "STALE_SOURCE"
    with ManualIndex(settings.database, settings.root) as index:
        index.build()
    with pytest.raises(AgentError) as error:
        service.fetch(hit["id"])
    assert error.value.code == "STALE_REFERENCE"


def test_mount_and_request_boundaries(agent_library):
    service = AgentKnowledge(agent_library)
    for kwargs in ({"limit": 1000}, {"manual": "missing"}, {"manual": "a", "category": "b"}):
        with pytest.raises(AgentError):
            service.search("constraint", **kwargs)
    for value in ("../../config.local.toml", "C:/private", "file:///secret", "invalid" * 3000):
        with pytest.raises(AgentError):
            service.fetch(value)
    source = service.search("相合约束")["results"][0]["source"]
    source["document"]["path"] = "../../private.htm"
    with pytest.raises(AgentError):
        service.resolve_source(source)
    with ManualIndex(agent_library.database, agent_library.root) as index:
        index.set_meta("library_id", "00000000-0000-4000-8000-000000000001")
        index.connection.commit()
    with pytest.raises(AgentError) as error:
        service.describe_library()
    assert error.value.code == "LIBRARY_CHANGED"


@pytest.mark.parametrize("require_token", [False, True])
def test_mcp_http_handshake_tools_and_guard(agent_library, require_token):
    pytest.importorskip("mcp")
    import httpx
    from mcp.shared.memory import create_connected_server_and_client_session

    from catia_manual.agent.server import create_app

    token = "test-" + "a" * 40
    settings = replace(agent_library, agent={"port": 18765, "require_token": require_token})
    app, _ = create_app(settings, token if require_token else None)

    async def run():
        transport = httpx.ASGITransport(app=app, client=("192.168.1.5", 4000))
        base = "http://192.168.1.20:18765"
        async with app.app.router.lifespan_context(app.app):
            async with httpx.AsyncClient(transport=transport, base_url=base) as client:
                assert (await client.get("/health")).status_code == (401 if require_token else 200)
                auth = {"Authorization": "Bearer " + token}
                assert (await client.get("/health", headers=auth)).status_code == 200
                assert (
                    await client.get("/health", headers={"Authorization": "Bearer stale-token"})
                ).status_code == (401 if require_token else 200)
                assert (
                    await client.get("/health", headers={**auth, "Host": "attacker.invalid:18765"})
                ).status_code == 403
                assert (
                    await client.get(
                        "/health", headers={**auth, "Origin": "https://attacker.invalid"}
                    )
                ).status_code == 403
                assert (
                    await client.post("/mcp", headers=auth, content=b"x" * 65537)
                ).status_code == 413
                assert (
                    await client.get(
                        "/health",
                        headers=[
                            ("Authorization", "Bearer " + token),
                            ("Authorization", "Bearer other"),
                        ],
                    )
                ).status_code == 400

            async with create_connected_server_and_client_session(app.mcp) as session:
                tools = (await session.list_tools()).tools
                assert {t.name for t in tools} == {
                    "describe_library",
                    "search",
                    "fetch",
                    "browse",
                    "resolve_source",
                }
                assert all(t.annotations.readOnlyHint for t in tools)
                result = await session.call_tool(
                    "search", {"query": "相合约束", "manual": "asmug_C2"}
                )
                assert not result.isError
                hit = result.structuredContent["results"][0]
                page = await session.call_tool("fetch", {"id": hit["id"]})
                assert not page.isError and page.structuredContent["blocks"]
                bad = await session.call_tool("fetch", {"id": "../private.htm"})
                assert bad.isError and bad.structuredContent["error"]["code"] == "INVALID_REFERENCE"
        public = httpx.ASGITransport(app=app, client=("8.8.8.8", 4000))
        async with httpx.AsyncClient(transport=public, base_url=base) as client:
            assert (
                await client.get(
                    "/health",
                    headers={"Authorization": "Bearer " + token, "X-Forwarded-For": "127.0.0.1"},
                )
            ).status_code == 403

    asyncio.run(asyncio.wait_for(run(), timeout=15))


def test_importing_core_agent_loads_neither_qt_nor_inference():
    import subprocess

    command = (
        "import sys; import catia_manual.core.agent; "
        "assert not any(n.startswith(('PySide6','onnxruntime','ctranslate2','tokenizers')) "
        "for n in sys.modules)"
    )
    subprocess.run([sys.executable, "-c", command], check=True)


def test_agent_configuration_persists_but_is_not_exported(agent_library):
    from catia_manual.agent.options import codex_config, options
    from catia_manual.config import save_settings
    from catia_manual.core.transfer import public_settings

    value = options(
        {
            "host": "0.0.0.0",
            "port": 18765,
            "token_encrypted": "dpapi:v1:private",
            "auto_start": True,
            "require_token": True,
        }
    )
    updated = save_settings(agent_library, {"agent": value})
    assert load_settings(updated.config_path).agent == value
    exported = public_settings(updated)
    assert "agent" not in exported and "dpapi:v1:private" not in json.dumps(exported)
    config = codex_config("192.168.1.20", 18765)
    assert 'url = "http://192.168.1.20:18765/mcp"' in config
    assert "CMV_MCP_TOKEN" not in config and "bearer_token_env_var" not in config
    protected = codex_config("192.168.1.20", 18765, require_token=True)
    assert "CMV_MCP_TOKEN" in protected and "private" not in protected
    for invalid in (
        {"port": 80},
        {"host": "8.8.8.8"},
        {"allow_subnets": ["0.0.0.0/0"]},
        {"auto_start": "true"},
        {"require_token": "false"},
    ):
        with pytest.raises(ValueError):
            options(invalid)

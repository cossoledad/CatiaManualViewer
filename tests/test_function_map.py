import hashlib
import json
import shutil

import pytest
from test_structure import manual_library as manual_library

from catia_manual.core.function_map import GraphService, map_request
from catia_manual.core.index import ManualIndex
from catia_manual.core.library import attach_copy
from catia_manual.core.library_contract import SourceLocation
from catia_manual.core.navigation import Location, NavigationHistory


@pytest.fixture
def indexed(manual_library):
    with ManualIndex(manual_library / "data/index.sqlite3", manual_library) as index:
        index.build()
        yield index


def request(manual="asmug_C2"):
    return {
        "scope": {"kind": "manual", "id": manual},
        "focus": {"path": "online/shared_v17/coincide.htm", "manual": manual},
    }


def test_focus_shows_authored_hierarchy_siblings_and_evidence(indexed):
    graph = GraphService(indexed).map(request())
    titles = {n["label_en"] for n in graph.nodes}
    assert {
        "Assembly Design",
        "User Tasks",
        "Managing Constraints",
        "Managing Coincidence Constraint",
        "Managing Offset Constraint",
    } <= titles
    assert "Unrelated" not in titles
    selected = next(n for n in graph.nodes if n["id"] == graph.selected)
    assert selected["label_zh"] == "管理相合约束"
    assert selected["placement_context"]["manual"] == "asmug_C2"
    assert SourceLocation.from_dict(selected["source"]).document.path.endswith("coincide.htm")
    for edge in graph.edges:
        assert edge["kind"] == "contains"
        source = SourceLocation.from_dict(edge["evidence"][0])
        assert (
            source.content_hash
            == hashlib.sha256((indexed.root / source.document.path).read_bytes()).hexdigest()
        )
    assert str(indexed.root) not in json.dumps(graph.to_dict())


def test_shared_article_has_one_identity_and_two_placement_contexts(indexed):
    a = GraphService(indexed).map(request())
    b = GraphService(indexed).map(request("wd1ug_C2"))
    na = next(n for n in a.nodes if n["id"] == a.selected)
    nb = next(n for n in b.nodes if n["id"] == b.selected)
    assert na["document_id"] == nb["document_id"]
    assert na["id"] != nb["id"]
    assert all(
        (n["placement_context"] or {}).get("manual", "asmug_C2") == "asmug_C2" for n in a.nodes
    )
    blocked = GraphService(indexed).map(
        {**request(), "scope": {"kind": "manual", "id": "wd1ug_C2"}}
    )
    assert blocked.notice and all(n["id"] != a.selected for n in blocked.nodes)


def test_category_edges_reference_the_catalog_not_a_manual(indexed):
    graph = GraphService(indexed).map({"scope": {"kind": "category", "id": "mechanical-design"}})
    assert len(graph.nodes) == 3
    assert all(
        SourceLocation.from_dict(e["evidence"][0]).document.path.endswith("TOC.xml")
        for e in graph.edges
    )


def test_wide_branches_page_before_the_node_limit(indexed):
    for number in range(250):
        indexed.connection.execute(
            "INSERT INTO manual_placements VALUES(?,?,?,?,?,?,?,?)",
            (
                f"wide-{number}",
                "asmug_C2",
                "",
                100 + number,
                f"Entry {number}",
                "online/shared_v17/coincide.htm",
                "",
                json.dumps([f"Entry {number}"]),
            ),
        )
    indexed.connection.commit()
    service = GraphService(indexed)
    graph = service.map({"scope": {"kind": "manual", "id": "asmug_C2"}, "limit": 12})
    assert len(graph.nodes) <= 12 and graph.truncated
    assert graph.nodes[0]["page_size"] == 11
    query = {**graph.request, "offsets": {graph.root: 20}}
    page = GraphService(indexed).map(query, reveal=False)
    assert {n["id"] for n in graph.nodes[1:]}.isdisjoint(n["id"] for n in page.nodes[1:])
    focus = {**request(), "limit": 12}
    focus["focus"]["trail"] = ["Entry 230"]
    focused = GraphService(indexed).map(focus)
    assert next(n for n in focused.nodes if n["id"] == focused.selected)["label_en"] == "Entry 230"
    assert len(focused.nodes) <= 12


def test_branch_focus_and_collapse_preserve_stable_document_context(indexed):
    graph = GraphService(indexed).map(request())
    group = next(n for n in graph.nodes if n["label_en"] == "Managing Constraints")
    query = dict(graph.request, branch=GraphService(indexed).descriptor(group))
    branch = GraphService(indexed).map(query)
    assert branch.root == group["id"] and len(branch.nodes) == 3
    assert branch.parent["trail"][-1] == "User Tasks"
    query = dict(graph.request, expanded=[n for n in graph.expanded if n != group["id"]])
    collapsed = GraphService(indexed).map(query, reveal=False)
    assert all(n["label_en"] != "Managing Coincidence Constraint" for n in collapsed.nodes)


def test_cycle_is_reported_without_inventing_a_parent_edge(indexed):
    node = next(
        n
        for n in GraphService(indexed).map(request()).nodes
        if n["label_en"] == "Managing Coincidence Constraint"
    )
    identity = node["placement_context"]["id"]
    indexed.connection.execute("UPDATE manual_placements SET parent=id WHERE id=?", (identity,))
    graph = GraphService(indexed).map(request())
    assert "循环" in graph.notice
    assert all(n["label_en"] != "Managing Coincidence Constraint" for n in graph.nodes)


def test_catalog_works_without_building_an_index(manual_library):
    with ManualIndex(manual_library / "data/index.sqlite3", manual_library) as index:
        service = GraphService(index)
        graph = service.map()
        assert graph.nodes and graph.notice == "目录待整理"
        assert not index.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='manual_placements'"
        ).fetchone()


def test_state_survives_rebuild_and_verified_migration(indexed, tmp_path):
    service = GraphService(indexed)
    graph = service.map(request())
    state = {
        "schema_version": 1,
        "library_id": indexed.library_id,
        "graph_revision": graph.graph_revision,
        "request": graph.request,
        "viewport": {"x": 50, "y": 120, "zoom": 0.8},
    }
    service.save_view(state)
    indexed.build()
    assert GraphService(indexed).load_view(graph.request["scope"]) == state
    target = tmp_path.parent / (tmp_path.name + "-copy")
    shutil.copytree(indexed.root, target, ignore=shutil.ignore_patterns("data"))
    destination = target.parent / (target.name + ".sqlite3")
    attach_copy(indexed.database, target, destination)
    with ManualIndex(destination, target) as moved:
        assert GraphService(moved).load_view(graph.request["scope"]) == state
        relocated = GraphService(moved).map(request())
        assert relocated.selected == graph.selected


def test_descriptor_relocates_after_toc_ordinals_change(indexed):
    graph = GraphService(indexed).map(request())
    selected = next(n for n in graph.nodes if n["id"] == graph.selected)
    descriptor = GraphService(indexed).descriptor(selected)
    path = indexed.root / "online/asmug_C2/asmug_C2sm.htm"
    path.write_text(
        '<p class="sm1"><a href="../shared_v17/reference.htm">New Section</a></p>'
        + path.read_text("utf-8"),
        "utf-8",
    )
    indexed.build()
    relocated = GraphService(indexed).map({**request(), "focus": descriptor})
    assert relocated.graph_revision != graph.graph_revision
    node = next(n for n in relocated.nodes if n["id"] == relocated.selected)
    assert node["id"] != selected["id"] and node["document_id"] == selected["document_id"]


@pytest.mark.parametrize(
    "value",
    [
        "invalid",
        {"limit": 201},
        {"limit": True},
        {"scope": {"kind": "directory"}},
        {"focus": {"path": "../outside.htm"}},
        {"expanded": "not-an-array"},
        {"offsets": {"node": -1}},
    ],
)
def test_requests_are_bounded_and_portable(value):
    with pytest.raises(ValueError):
        map_request(value)


def test_graph_navigation_ignores_zoom_but_records_distinct_targets():
    history = NavigationHistory()
    a = {"request": request(), "viewport": {"zoom": 1}}
    history.visit(Location("@map", map_state=a))
    history.visit(Location("@map", map_state={**a, "viewport": {"zoom": 0.5}}))
    assert len(history.entries) == 1
    history.visit(Location("@map", map_state={"request": request("wd1ug_C2")}))
    assert len(history.entries) == 2


@pytest.mark.parametrize(
    "extra",
    [
        {"viewport": {"x": float("nan")}},
        {"viewport": "bad"},
        {"positions": {"node": [0, float("inf")]}},
        {"positions": "bad"},
    ],
)
def test_view_state_rejects_invalid_geometry(indexed, extra):
    service = GraphService(indexed)
    state = {"schema_version": 1, "library_id": indexed.library_id, "request": request(), **extra}
    with pytest.raises(ValueError):
        service.save_view(state)


def test_developer_cli_exports_the_same_source_contract(indexed, capsys):
    from catia_manual.cli import main

    config = indexed.root / "map-cli.toml"
    config.write_text(
        "[documentation]\nroot='"
        + str(indexed.root)
        + "'\ndatabase='"
        + str(indexed.database)
        + "'\n",
        "utf-8",
    )
    assert (
        main(
            [
                "--config",
                str(config),
                "map",
                "--manual",
                "asmug_C2",
                "--focus",
                "online/shared_v17/coincide.htm",
            ]
        )
        == 0
    )
    graph = json.loads(capsys.readouterr().out)
    node = next(n for n in graph["nodes"] if n["id"] == graph["selected"])
    assert SourceLocation.from_dict(node["source"]).document.library_id == indexed.library_id

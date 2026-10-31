import json
from pathlib import Path

import pytest

from catia_manual.core.index import ManualIndex
from catia_manual.core.library_contract import SourceLocation
from catia_manual.core.navigation import Location, NavigationHistory
from catia_manual.core.parser import ManualParser
from catia_manual.core.relations import RelationService, relation_request
from catia_manual.core.source import resolve_source


@pytest.fixture
def relation_library(tmp_path):
    root = tmp_path / "manual"
    folder = root / "online/asmug_C2"
    folder.mkdir(parents=True)
    (folder / "asmugsm.htm").write_text(
        '<p class="sm1"><a href="main.htm">Managing Coincidence Constraint</a></p>', "utf-8"
    )
    (folder / "main.htm").write_text(
        '<h1>Managing Coincidence Constraint<a name="CATAssembly.CreateCoincid"></a></h1>'
        '<p id="ref-source">Read <a href="reference.htm#details">the reference</a> and '
        '<a href="reference.htm#other">other details</a>.</p>'
        '<p id="task-source">See <a href="next.htm">the next task</a>.</p>'
        '<p id="sample-source"><a href="sample 中文 1.CATPart">Example file</a></p>'
        '<p id="image-source"><img src="diagram.png" alt="Diagram"></p>'
        '<p><img src="diagram.png" alt="Again"></p>'
        '<a href="missing.CATPart">Missing example</a><img src="missing.png">'
        '<a href="https://example.invalid/no.htm">External</a>'
        '<a href="../../../outside.htm">Blocked</a><a href="javascript:bad()">Bad</a>'
        '<script><a href="fake.htm">Not a link</a></script>',
        "utf-8",
    )
    (folder / "reference.htm").write_text(
        '<h1>Coincidence Constraints</h1><h2 id="details">Details</h2><p>Permitted elements.</p>'
        '<p><a href="main.htm">Back to tutorial</a></p>',
        "utf-8",
    )
    (folder / "next.htm").write_text(
        '<h1>Using Coincidence Constraint<a name="CATAssembly.CreateCoincid"></a></h1>'
        '<p><a href="reference.htm#details">Reference</a></p>',
        "utf-8",
    )
    (folder / "sample 中文 1.CATPart").write_bytes(b"fixture only")
    (folder / "diagram.png").write_bytes(
        (Path(__file__).parents[1] / "src/catia_manual/assets/app.png").read_bytes()
    )
    with ManualIndex(tmp_path / "data/index.sqlite3", root) as index:
        index.build()
        yield index


def request(**changes):
    return {"path": "online/asmug_C2/main.htm", "manual": "asmug_C2", **changes}


def test_all_relations_are_explicit_and_have_resolvable_evidence(relation_library):
    index = relation_library
    graph = RelationService(index).map(request())
    assert {"references", "links_to", "documents_command", "has_sample", "has_image"} == {
        e["kind"] for e in graph.edges
    }
    assert {"article", "command", "sample", "image"} == {n["kind"] for n in graph.nodes}
    assert all("outside" not in (n["document_ref"] or {}).get("path", "") for n in graph.nodes)
    assert not any("fake.htm" in json.dumps(n) for n in graph.nodes)
    for edge in graph.edges:
        assert edge["provenance"] == "document"
        for proof in edge["evidence"]:
            source = SourceLocation.from_dict(proof["source"])
            assert resolve_source(ManualParser(index.root), source, index.library_id).source
    assert str(index.root) not in json.dumps(graph.to_dict())


def test_shared_targets_merge_but_keep_each_source_and_anchor(relation_library):
    graph = RelationService(relation_library).map(request())
    reference = [
        n
        for n in graph.nodes
        if (n["document_ref"] or {}).get("path", "").endswith("reference.htm")
    ]
    assert len(reference) == 1
    edge = next(e for e in graph.edges if e["target"] == reference[0]["id"])
    assert {p["fragment"] for p in edge["evidence"]} == {"details", "other"}
    assert all(
        p["source"]["fragment"] == "ref-source" and p["source"]["block_id"]
        for p in edge["evidence"]
    )
    images = [n for n in graph.nodes if n["kind"] == "image" and n["exists"]]
    assert len(images) == 1
    edge = next(e for e in graph.edges if e["target"] == images[0]["id"])
    assert [p["element"]["occurrence"] for p in edge["evidence"]] == [0, 1]


def test_expansion_preserves_cycles_and_semantic_edge_direction(relation_library):
    first = RelationService(relation_library).map(request())
    reference = next(n for n in first.nodes if n["role"] == "reference")
    command = next(n for n in first.nodes if n["kind"] == "command")
    graph = RelationService(relation_library).map(
        request(expanded=[reference["id"], command["id"]])
    )
    assert any(
        e["source"] == reference["id"] and e["target"] == graph.root and not e["layout"]
        for e in graph.edges
    )
    assert len({n["id"] for n in graph.nodes}) == len(graph.nodes)
    assert all(
        next(n for n in graph.nodes if n["id"] == e["source"])["kind"] == "article"
        for e in graph.edges
        if e["kind"] == "documents_command"
    )
    assert all(e["layout_target"] != graph.root for e in graph.edges if e["layout"])


def test_filters_missing_resources_and_pagination(relation_library):
    graph = RelationService(relation_library).map(request(types=["has_image"]))
    assert len(graph.nodes) == 3 and {e["kind"] for e in graph.edges} == {"has_image"}
    assert sum(not n["exists"] for n in graph.nodes) == 1
    empty = RelationService(relation_library).map(request(types=[]))
    assert len(empty.nodes) == 1 and not empty.edges
    path = relation_library.root / request()["path"]
    path.write_text(
        path.read_text("utf-8") + "".join(f'<img src="extra{i}.png">' for i in range(50)), "utf-8"
    )
    first = RelationService(relation_library).map(request(types=["has_image"], limit=12))
    assert len(first.nodes) == 12 and first.truncated
    size = first.nodes[0]["page_size"]
    second = RelationService(relation_library).map(
        request(types=["has_image"], limit=12, offsets={first.root: size})
    )
    assert {n["id"] for n in first.nodes[1:]}.isdisjoint(n["id"] for n in second.nodes[1:])


def test_revision_and_user_state_survive_index_rebuild(relation_library):
    service = RelationService(relation_library)
    graph = service.map(request())
    state = {
        "schema_version": 1,
        "library_id": graph.library_id,
        "graph_revision": graph.graph_revision,
        "request": graph.request,
        "selected": graph.root,
        "viewport": {"x": 0, "y": 20, "zoom": 0.8},
    }
    service.save_view(state)
    relation_library.build()
    assert RelationService(relation_library).load_view(request()["path"]) == state
    path = relation_library.root / request()["path"]
    path.write_text(path.read_text("utf-8") + "<p>A revised paragraph.</p>", "utf-8")
    assert RelationService(relation_library).map(request()).graph_revision != graph.graph_revision
    with pytest.raises(ValueError):
        service.save_view({**state, "library_id": "other"})


@pytest.mark.parametrize(
    "value",
    [
        None,
        {"path": "../outside.htm"},
        {"path": "file.png"},
        request(limit=201),
        request(types=["suggested_related"]),
        request(expanded="bad"),
        request(offsets={"node": -1}),
    ],
)
def test_request_boundaries(value):
    with pytest.raises(ValueError):
        relation_request(value)


def test_relationship_history_keeps_roots_separate_from_view_changes():
    history = NavigationHistory()
    history.visit(Location("@relations", relation_state={"request": request()}))
    history.visit(Location("@relations", relation_state={"request": request(types=[])}))
    assert len(history.entries) == 1
    history.visit(
        Location("@relations", relation_state={"request": {"path": "online/asmug_C2/next.htm"}})
    )
    assert len(history.entries) == 2


def test_developer_relation_cli_keeps_the_evidence_contract(relation_library, capsys):
    from catia_manual.cli import main

    index = relation_library
    config = index.database.parent / "config.toml"
    config.write_text(
        f"[documentation]\nroot='{index.root}'\ndatabase='{index.database}'\n", "utf-8"
    )
    assert (
        main(["--config", str(config), "relations", request()["path"], "--type", "has_image"]) == 0
    )
    graph = json.loads(capsys.readouterr().out)
    assert {e["kind"] for e in graph["edges"]} == {"has_image"}
    assert all(
        SourceLocation.from_dict(p["source"]).document.library_id == index.library_id
        for e in graph["edges"]
        for p in e["evidence"]
    )

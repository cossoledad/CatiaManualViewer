import json

import pytest

from catia_manual.core.catalog import ManualCatalog
from catia_manual.core.index import ManualIndex
from catia_manual.core.search import SearchService
from catia_manual.core.structure import StructureIndex
from catia_manual.core.terminology import lookup, vocabulary
from scripts.build_terminology import catalog_strings


@pytest.fixture
def manual_library(tmp_path):
    online = tmp_path / "online"
    shared = online / "shared_v17"
    shared.mkdir(parents=True)
    toc = '<p class="sm1"><a href="../shared_v17/overview.htm">User Tasks</a></p>'
    toc += (
        '<p class="sm2" style="margin-left:25"><a href="../shared_v17/overview.htm">'
        "Managing Constraints</a></p>"
    )
    for name, title in (
        ("coincide", "Managing Coincidence Constraint"),
        ("offset", "Managing Offset Constraint"),
    ):
        toc += (
            f'<p class="sm3" style="margin-left:50"><a href="../shared_v17/{name}.htm">'
            f"{title}</a></p>"
        )
    toc += '<p class="sm1"><a href="../shared_v17/reference.htm">Reference Information</a></p>'
    toc += (
        '<p class="sm2" style="margin-left:25"><a href="../shared_v17/reference.htm">'
        "Coincidence Constraints</a></p>"
    )
    for name in ("asmug_C2", "wd1ug_C2"):
        folder = online / name
        folder.mkdir()
        (folder / f"{name}sm.htm").write_text(toc, "utf-8")
        (folder / f"{name}ix.htm").write_text(
            '<p class="ix1">commands</p><p class="ix2">Coincidence Constraint'
            '<a href="../shared_v17/coincide.htm#ix-commands::Coincidence Constraint">'
            "index</a></p>",
            "utf-8",
        )
    (online / "CATIA_P3_TOC.xml").write_text(
        '<Root><ITEM name="CATIA"><ITEM name="Mechanical Design">'
        '<DITEM dir="asmug_C2" name="Assembly Design"/>'
        '<DITEM dir="wd1ug_C2" name="Weld Design"/></ITEM></ITEM></Root>',
        "utf-8",
    )
    (shared / "overview.htm").write_text(
        "<h1>Managing Constraints</h1><p>This section describes assembly constraints.</p>"
        '<a href="coincide.htm">Coincidence</a><a href="offset.htm">Offset</a>'
        '<a href="reference.htm">Reference</a>',
        "utf-8",
    )
    for name, identity, title, term in (
        (
            "coincide",
            "CATAssembly.CreateCoincid",
            "Managing Coincidence Constraint",
            "Coincidence Constraint",
        ),
        ("offset", "CATAssembly.CreateOffset", "Managing Offset Constraint", "Offset Constraint"),
    ):
        (shared / f"{name}.htm").write_text(
            f'<h1>{title}<a name="{identity}"></a></h1>'
            '<table><tr><td><img src="../icons/common/atarget.gif"></td>'
            f"<td>This task consists of applying a {term} between two faces.</td></tr></table>"
            f'<ol class="task"><li>Click {term}.</li></ol>'
            '<a href="reference.htm">Coincidence Constraints reference</a>',
            "utf-8",
        )
    (shared / "reference.htm").write_text(
        "<h1>Coincidence Constraints</h1><p>Permitted elements.</p>", "utf-8"
    )
    (shared / "unrelated.htm").write_text(
        "<h1>Unrelated</h1><p>Coincidence Constraint.</p>", "utf-8"
    )
    return tmp_path


def test_shared_membership_is_explicit_not_the_whole_shared_directory(manual_library):
    catalog = ManualCatalog(manual_library)
    with ManualIndex(manual_library / "data/index.sqlite3", manual_library) as index:
        index.build()
        scope = catalog.scope(["asmug_C2"])
        assert index.search("Coincidence Constraint", modules=scope, limit=1)[0].path.endswith(
            "coincide.htm"
        )
        assert not index.search("Unrelated", modules=scope)
        assert index.search("Unrelated", modules=["shared_v17"])
        placements = StructureIndex(index).placements("online/shared_v17/coincide.htm")
        assert {row["manual"] for row in placements} == {"asmug_C2", "wd1ug_C2"}
        assert (
            StructureIndex(index).placements("online/shared_v17/coincide.htm", ["wd1ug_C2"])[0][
                "manual"
            ]
            == "wd1ug_C2"
        )


@pytest.mark.parametrize(
    "query,page",
    [
        ("相合约束", "coincide.htm"),
        ("偏移约束", "offset.htm"),
        ("管理约束", "overview.htm"),
        ("Managing Constraints", "overview.htm"),
        ("Coincidence Constraint", "coincide.htm"),
        ("Coincidence Constraints", "reference.htm"),
    ],
)
def test_real_command_names_without_a_catia_installation(manual_library, query, page):
    with ManualIndex(manual_library / "data/index.sqlite3", manual_library) as index:
        index.build()
        service = SearchService(index, {"provider": "glossary"})
        for scope in (None, ManualCatalog(manual_library).scope(["asmug_C2"])):
            hits = service.search(query, modules=scope).hits
            assert hits[0].path.endswith(page)
            assert hits[0].placement
            assert hits[0].source["document"]["library_id"] == index.library_id


def test_keyword_evidence_survives_changed_command_anchors(manual_library):
    path = manual_library / "online/shared_v17/coincide.htm"
    path.write_text(
        path.read_text("utf-8").replace("CATAssembly.CreateCoincid", "NewRelease.Command"), "utf-8"
    )
    with ManualIndex(manual_library / "data/index.sqlite3", manual_library) as index:
        index.build()
        hit = index.search("相合约束", limit=1)[0]
        assert hit.path.endswith("coincide.htm")
        assert hit.signals["rank_tier"] == 1


def test_index_rebuild_preserves_user_data_and_reuses_article_metadata(manual_library):
    with ManualIndex(manual_library / "data/index.sqlite3", manual_library) as index:
        index.build()
        index.set_alias("我的约束", "coincidence constraint")
        report = StructureIndex(index).build()
        assert report["updated"] == 0 and report["unchanged"] > 0
        index.build(force=True)
        assert index.aliases()["我的约束"]
        index.connection.execute(
            "INSERT INTO articles_fts(articles_fts,rank) VALUES('integrity-check',1)"
        )


@pytest.mark.parametrize("mode", ["keyword", "hybrid", "semantic"])
def test_command_resolution_precedes_optional_models(manual_library, monkeypatch, mode):
    from catia_manual.core import embedding

    def unexpected(_):
        raise AssertionError("known command must not load inference")

    monkeypatch.setattr(embedding, "make_embedder", unexpected)
    with ManualIndex(manual_library / "data/index.sqlite3", manual_library) as index:
        index.build()
        response = SearchService(index, {"provider": "glossary"}, {"mode": mode}).search("相合约束")
        assert response.hits[0].path.endswith("coincide.htm")
        assert not any("未就绪" in note for note in response.plan.notes)
        assert response.hits[0].related[0]["path"].endswith("reference.htm")


def test_resource_extractor_handles_legacy_comments_escaping_and_multiline_values():
    raw = (
        b'// invalid legacy comment \xe9\nName.ShortHelp="' + "相合约束".encode("gb18030") + b'";\n'
    )
    raw += b'Name.Help="line1\\nline2; \\"quoted\\""; /* ignored */'
    parsed = catalog_strings(raw, True)
    assert parsed["Name.ShortHelp"] == "相合约束"
    assert parsed["Name.Help"] == 'line1\nline2; "quoted"'
    assert lookup("相合约束") and lookup("Offset Constraint")
    assert vocabulary()["provenance"]["version_policy"] == "informational-only"
    content = json.dumps(vocabulary())
    assert "C:\\\\UserApp" not in content


def test_existing_library_upgrade_backs_up_before_adding_structure(manual_library):

    database = manual_library / "data/index.sqlite3"
    with ManualIndex(database, manual_library) as index:
        StructureIndex(index).build(backup=True)
        assert index.meta("structure_backup")
        assert (database.parent / "backups" / index.meta("structure_backup")).is_file()

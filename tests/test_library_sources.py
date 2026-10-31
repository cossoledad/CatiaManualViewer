import json
import os
import shutil
import sqlite3
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from catia_manual.core.index import SCHEMA, ManualIndex
from catia_manual.core.library import attach_copy, connect_readonly, snapshot, verify_copy
from catia_manual.core.library_contract import SourceLocation
from catia_manual.core.page_translation import PageTranslationService
from catia_manual.core.parser import ManualParser
from catia_manual.core.search import SearchService
from catia_manual.core.source import resolve_source, source_for_block
from catia_manual.core.translation import TranslationService


def copied(corpus):
    target = corpus.with_name(corpus.name + "-copy")
    shutil.copytree(corpus, target)
    return target


def test_cli_sources_manifest_and_copy_verification(corpus, monkeypatch, capsys):
    from catia_manual import cli
    from catia_manual.config import Settings

    db = corpus / "data/index.sqlite3"
    settings = Settings(corpus, db, corpus / "absent.exe", "", corpus / "absent")
    monkeypatch.setattr(cli, "load_settings", lambda _: settings)
    with ManualIndex(db, corpus) as index:
        index.build()
    assert cli.main(["read", "online/prtug_C2/pad.htm", "--with-source"]) == 0
    article = json.loads(capsys.readouterr().out)
    location = SourceLocation.from_dict(article["sources"][0])
    assert location.document.library_id == article["library_id"]
    source_file = corpus / "source.json"
    source_file.write_text(json.dumps(location.to_dict()), "utf-8")
    assert cli.main(["resolve-source", str(source_file)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "exact"
    source_file.unlink()
    assert cli.main(["library", "manifest"]) == 0
    manifest = json.loads(capsys.readouterr().out)
    assert len(manifest["files"]) == 5 and "root" not in manifest
    target = copied(corpus)
    assert cli.main(["library", "verify", "--root", str(target)]) == 0
    assert json.loads(capsys.readouterr().out)["matched"]
    (target / "online/prtug_C2/other.htm").write_text("Changed", "utf-8")
    assert cli.main(["library", "verify", "--root", str(target)]) == 2
    assert not json.loads(capsys.readouterr().out)["matched"]


def test_alias_rejects_empty_search_concepts(corpus):
    with ManualIndex(corpus / "index.sqlite3", corpus) as index:
        for invalid in ("...", "pad | -", "|pad", "pad|", "圆角"):
            with pytest.raises(ValueError):
                index.set_alias("示例", invalid)


def test_attached_database_inside_copy_does_not_pollute_its_manifest(corpus):
    database = corpus / "data/index.sqlite3"
    with ManualIndex(database, corpus) as index:
        index.build()
    target = copied(corpus)
    destination = target / "data/reused.sqlite3"
    attach_copy(database, target, destination)
    assert verify_copy(destination, target)["matched"]


def test_manifest_and_incremental_hash_do_not_depend_on_mtime(corpus):
    db = corpus / "data/index.sqlite3"
    with ManualIndex(db, corpus) as index:
        report = index.build()
        identity, original_hash = index.library_id, index.meta("manifest_hash")
        assert report["manifest_verified"] and index.stats()["files"] == 5
        path = corpus / "online/prtug_C2/pad.htm"
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1000000000))
        assert index.build()["updated"] == 0
        assert index.meta("manifest_hash") == original_hash
        before = path.stat()
        path.write_bytes(path.read_bytes().replace(b"dimension", b"diameters"))
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert index.build()["updated"] == 1
        assert index.meta("manifest_hash") != original_hash
        assert index.library_id == identity


def test_exact_copy_preserves_ids_locations_aliases_and_wal_data(corpus):
    db = corpus / "data/index.sqlite3"
    with ManualIndex(db, corpus) as original:
        original.build()
        hit = original.search("dimension")[0]
        source = replace(SourceLocation.from_dict(hit.source), offset_css_px=-17)
        original.save_location(source)
        original.set_alias("设计凸起", "pad")
        original.connection.execute("CREATE TABLE user_notes(text)")
        original.connection.execute("INSERT INTO user_notes VALUES('keep this note')")
        original.connection.commit()
        target = copied(corpus)
        assert verify_copy(db, target)["matched"]
        destination = corpus / "data/attached.sqlite3"
        attach_copy(db, target, destination)
        with ManualIndex(destination, target) as attached:
            assert attached.library_id == original.library_id
            assert attached.search("dimension")[0].document_id == hit.document_id
            assert attached.load_location(hit.path) == source
            assert attached.aliases()["设计凸起"] == ("pad",)
            assert (
                attached.connection.execute("SELECT text FROM user_notes").fetchone()[0]
                == "keep this note"
            )
            attached.build(force=True)
            assert attached.load_location(hit.path) == source
        assert original.meta("root") == str(corpus)
        with pytest.raises(FileExistsError):
            snapshot(db, destination)


def test_changed_copy_requires_explicit_revision_acceptance(corpus):
    db = corpus / "index.sqlite3"
    with ManualIndex(db, corpus) as original:
        original.build()
        library_id = original.library_id
    target = copied(corpus)
    (target / "online/prtug_C2/other.htm").write_text(
        "<h1>New Pocket</h1><p>New depth setting.</p>", "utf-8"
    )
    verification = verify_copy(db, target)
    assert not verification["matched"] and verification["modified"] == ["online/prtug_C2/other.htm"]
    destination = corpus / "attached.sqlite3"
    with pytest.raises(ValueError, match="不一致"):
        attach_copy(db, target, destination)
    assert not destination.exists()
    result = attach_copy(db, target, destination, accept_revision=True)
    assert result["requires_update"]
    with ManualIndex(destination, target) as attached:
        assert attached.library_id == library_id and not attached.stats()["manifest_verified"]
        assert attached.build()["updated"] == 1
        assert attached.stats()["manifest_verified"]


def test_partial_or_failed_scan_is_not_a_verified_library(corpus, monkeypatch):
    db = corpus / "index.sqlite3"
    with ManualIndex(db, corpus) as index:
        index.build(module="prtug_C2")
        with pytest.raises(ValueError, match="完整"):
            verify_copy(db, corpus)
        index.build()

        def fail(*args, **kwargs):
            raise OSError("unreadable file")

        monkeypatch.setattr("catia_manual.core.index.scan_manifest", fail)
        assert index.build()["errors"]
        assert not index.stats()["manifest_verified"]
        assert index.stats()["documents"] == 3


def test_schema1_upgrade_backs_up_and_keeps_translation_cache(corpus):
    db = corpus / "legacy.sqlite3"
    parser = ManualParser(corpus)
    doc = parser.parse("online/prtug_C2/pad.htm")
    page = parser.render_page(doc.path)
    name = "local-argos-v3:C:/old-machine/models:0123456789abcdef"
    provider = SimpleNamespace(name=name, translate=lambda text, target: "缓存译文")
    with sqlite3.connect(db) as connection:
        connection.executescript(SCHEMA)
        connection.execute("PRAGMA user_version=1")
        connection.execute("INSERT INTO metadata VALUES('root',?)", (str(corpus),))
        connection.execute(
            "INSERT INTO documents(path,title,module,kind,encoding,content_hash,mtime_ns,"
            "size,text,blocks,links,headings) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                doc.path,
                doc.title,
                doc.module,
                doc.kind,
                doc.encoding,
                doc.content_hash,
                0,
                1,
                doc.text,
                json.dumps([asdict(b) for b in doc.blocks]),
                "[]",
                json.dumps(doc.headings),
            ),
        )
        connection.commit()
        adapter = SimpleNamespace(connection=connection)
        TranslationService(adapter, provider).translate("unique selected sentence")
        PageTranslationService(adapter, provider, page).translate(page.units[0])

    def no_request(*args):
        raise AssertionError("cache must be reusable without inference")

    portable = SimpleNamespace(name="local-argos-v3:0123456789abcdef", translate=no_request)
    with ManualIndex(db, corpus) as upgraded:
        assert upgraded.stats()["schema_version"] == 2
        assert upgraded.search("pad")
        assert TranslationService(upgraded, portable).translate("unique selected sentence").cached
        assert PageTranslationService(upgraded, portable, page).translate(page.units[0]).cached
    backups = list((corpus / "backups").glob("*.sqlite3"))
    assert len(backups) == 1
    with connect_readonly(backups[0]) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


def test_source_block_markers_and_revision_relocation(corpus):
    db = corpus / "index.sqlite3"
    parser = ManualParser(corpus)
    with ManualIndex(db, corpus) as index:
        index.build()
        hit = index.search("dimension")[0]
        source = SourceLocation.from_dict(hit.source)
        doc = parser.parse(hit.path)
        soup = BeautifulSoup(parser.render(hit.path), "lxml")
        assert all(soup.select(f'[data-cmv-source-block="{b.id}"]') for b in doc.blocks)
        assert resolve_source(parser, source, index.library_id).status == "exact"
        path = corpus / hit.path
        path.write_text(
            path.read_text("utf-8").replace("Creating Pads", "Creating Pads Updated"), "utf-8"
        )
        assert resolve_source(parser, source, index.library_id).status == "relocated-quote"
        path.unlink()
        assert resolve_source(parser, source, index.library_id).status == "missing"


def test_source_never_chooses_first_repeated_quote(corpus):
    parser = ManualParser(corpus)
    relative = "online/prtug_C2/repeated.htm"
    path = corpus / relative
    path.write_text("<h1>Repeated</h1><p>Same text here.</p>", "utf-8")
    with ManualIndex(corpus / "index.sqlite3", corpus) as index:
        doc = parser.parse(relative)
        source = source_for_block(index.library_id, doc, doc.blocks[-1])
        source = replace(source, heading_path=(), text_prefix="", text_suffix="")
        path.write_text("<h1>Repeated</h1><p>Same text here.</p><p>Same text here.</p>", "utf-8")
        resolution = resolve_source(parser, source, index.library_id)
        assert resolution.status == "ambiguous" and resolution.candidates == 2
        assert resolution.source is None


def test_scoped_alias_spelling_and_cross_section_explanations(corpus):
    (corpus / "online/prtug_C2/limits.htm").write_text(
        "<h1>Limits</h1><h2 id='diameter'>Diameter</h2><p>Specify the diameter.</p>"
        "<h2 id='thick'>Thickness</h2><p>Specify thickness.</p>",
        "utf-8",
    )
    with ManualIndex(corpus / "index.sqlite3", corpus) as index:
        index.build()
        index.set_alias("小凹坑", "pocket", "prtug_C2")
        service = SearchService(index, {"provider": "glossary"})
        assert not service.search("小凹坑").hits
        assert service.search("小凹坑", modules=["prtug_C2"]).hits[0].title == "Pocket"
        suggestion = service.search("pockte")
        assert suggestion.hits[0].title == "Pocket" and "spelling" in suggestion.plan.method
        cross = service.search("diameter thickness")
        assert cross.hits and cross.plan.notes and "cross-section" in cross.plan.method
        source = SourceLocation.from_dict(cross.hits[0].source)
        assert source.fragment in {"diameter", "thick"}
        assert source.document.library_id == index.library_id
        assert not service.search("diameter thickness", modules=[]).hits


def test_attachment_rejects_manifest_tampering(corpus):
    db = corpus / "index.sqlite3"
    with ManualIndex(db, corpus) as index:
        index.build()
        index.connection.execute(
            "UPDATE library_files SET content_hash=? WHERE path LIKE '%pad.htm'", ("f" * 64,)
        )
        index.connection.commit()
    with pytest.raises(ValueError, match="清单校验失败"):
        verify_copy(db, corpus)


def test_manifest_checks_database_resources_as_well_as_html(corpus):
    resource = corpus / "online/prtug_C2/geometry.db"
    resource.write_bytes(b"original manual resource")
    db = corpus / "index.sqlite3"
    with ManualIndex(db, corpus) as index:
        index.build()
        assert index.stats()["files"] == 6
    target = copied(corpus)
    (target / resource.relative_to(corpus)).unlink()
    result = verify_copy(db, target)
    assert not result["matched"] and result["removed"] == ["online/prtug_C2/geometry.db"]


def test_unicode_source_paths_are_canonical_but_resolve_actual_files(corpus):
    import unicodedata

    actual = corpus / "online/prtug_C2/cafe\u0301.htm"
    actual.write_text("<h1>Unicode</h1><p>Unique settings.</p>", "utf-8")
    with ManualIndex(corpus / "index.sqlite3", corpus) as index:
        index.build()
        hit = index.search("Unicode")[0]
        source = SourceLocation.from_dict(hit.source)
        expected = unicodedata.normalize("NFC", actual.relative_to(corpus).as_posix())
        assert source.document.path == expected
        assert resolve_source(ManualParser(corpus), source, index.library_id).status == "exact"


def test_catalog_context_changes_without_reparsing_unchanged_articles(corpus, monkeypatch):
    with ManualIndex(corpus / "index.sqlite3", corpus) as index:
        index.build()
        (corpus / "online/CATIA_P3_TOC.xml").write_text(
            '<Root><ITEM name="CATIA"><ITEM name="Mechanical Design">'
            '<DITEM dir="prtug_C2" name="Custom Toolbox"/></ITEM></ITEM></Root>',
            "utf-8",
        )

        def no_parse(*args):
            raise AssertionError("unchanged source must not be reparsed")

        monkeypatch.setattr(ManualParser, "parse", no_parse)
        assert index.build()["unchanged"] == 3
        assert index.search("toolbox")


def test_long_article_does_not_starve_other_articles_before_limit(corpus):
    module = corpus / "online/stress_C2"
    module.mkdir()
    (module / "a.htm").write_text("<h1>Pad reference</h1>" + "<p>Create a pad.</p>" * 150, "utf-8")
    (module / "b.htm").write_text("<h1>Pad introduction</h1><p>Create a pad.</p>", "utf-8")
    with ManualIndex(corpus / "index.sqlite3", corpus) as index:
        index.build()
        hits = index.search("pad", modules=["stress_C2"], limit=2)
        assert {hit.path for hit in hits} == {"online/stress_C2/a.htm", "online/stress_C2/b.htm"}

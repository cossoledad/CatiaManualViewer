import hashlib
import json
import shutil
import threading
from dataclasses import replace

import pytest

np = pytest.importorskip("numpy")

from catia_manual.config import Settings, load_settings, save_settings  # noqa: E402
from catia_manual.core import embedding  # noqa: E402
from catia_manual.core.index import ManualIndex  # noqa: E402
from catia_manual.core.library import attach_copy  # noqa: E402
from catia_manual.core.library_contract import SourceLocation  # noqa: E402
from catia_manual.core.parser import ManualParser  # noqa: E402
from catia_manual.core.search import SearchService  # noqa: E402
from catia_manual.core.semantic import PASSAGE_VERSION, SemanticIndex, fuse  # noqa: E402
from catia_manual.core.source import resolve_source  # noqa: E402


class Encoder:
    fingerprint = "test-embedding-v1"
    dimension = 3
    spec = {"model": "fixture", "dimension": 3}

    def __init__(self):
        self.calls = []

    def encode(self, texts, *, query=False):
        self.calls.append((list(texts), query))
        return np.asarray(
            [
                [1, 0, 0]
                if "pad" in text.lower() or "长成" in text
                else [0, 1, 0]
                if "pocket" in text.lower()
                else [0, 0, 1]
                for text in texts
            ],
            dtype=np.float32,
        )


def built(corpus):
    index = ManualIndex(corpus / "data/index.sqlite3", corpus)
    index.build()
    return index


def test_semantic_unknown_chinese_preserves_scoped_source(corpus, monkeypatch):
    encoder = Encoder()
    monkeypatch.setattr(embedding, "make_embedder", lambda _: encoder)
    with built(corpus) as index:
        SemanticIndex(index).build(encoder)
        service = SearchService(index, {"provider": "glossary"}, {"mode": "hybrid"})
        result = service.search("让轮廓长成立体", modules=["prtug_C2"])
        assert result.hits[0].title == "Creating Pads"
        assert "semantic" in result.hits[0].channels
        source = SourceLocation.from_dict(result.hits[0].source)
        assert resolve_source(ManualParser(corpus), source, index.library_id).source
        assert not service.search("长成立体", modules=[]).hits
        assert not service.search("长成立体", modules=["absent"]).hits


def test_pause_resume_reuses_completed_vectors_without_rebuilding(corpus):
    cancel, encoder = threading.Event(), Encoder()
    with built(corpus) as index:
        service = SemanticIndex(index)
        first = service.build(encoder, batch_size=1, cancel=cancel, progress=lambda _: cancel.set())
        assert first["state"] == "paused" and first["embedded"] == 1
        cancel.clear()
        second = service.build(encoder, batch_size=1, cancel=cancel)
        assert second["state"] == "ready" and second["reused"] == 1
        assert first["embedded"] + second["embedded"] == second["total"]
        encoder.calls.clear()
        third = service.build(encoder)
        assert third["embedded"] == 0 and third["reused"] == third["total"]
        assert not encoder.calls


def test_revision_filters_stale_vectors_and_only_embeds_changes(corpus):
    encoder = Encoder()
    with built(corpus) as index:
        service = SemanticIndex(index)
        original = service.build(encoder)
        (corpus / "online/prtug_C2/other.htm").write_text(
            "<h1>Shell</h1><p>Hollow out the interior.</p>", "utf-8"
        )
        index.build()
        state = service.status(encoder.fingerprint + ":" + PASSAGE_VERSION)
        assert state["state"] == "stale" and state["valid"] < original["valid"]
        current = service.build(encoder)
        assert current["state"] == "ready" and current["embedded"] == 1
        assert current["reused"] > 0
        hits, _ = service.search("pocket", encoder)
        assert all(hit.title != "Pocket" for hit in hits)


def test_failed_encoder_keeps_previous_index_and_retry_is_possible(corpus):
    encoder = Encoder()
    with built(corpus) as index:
        service = SemanticIndex(index)
        original = service.build(encoder)
        (corpus / "online/prtug_C2/new.htm").write_text("<h1>New</h1><p>Content.</p>", "utf-8")
        index.build()
        encoder.encode = lambda texts, **_: np.zeros((len(texts), 3))
        with pytest.raises(ValueError, match="空向量"):
            service.build(encoder)
        state = service.status(encoder.fingerprint + ":" + PASSAGE_VERSION)
        assert state["state"] == "failed" and state["valid"] == original["valid"]
        assert service.build(Encoder())["state"] == "ready"


def test_model_versions_and_library_copy_are_isolated(corpus):
    encoder = Encoder()
    with built(corpus) as index:
        service = SemanticIndex(index)
        service.build(encoder)
        encoder.fingerprint = "test-embedding-v2"
        assert service.status(encoder.fingerprint + ":" + PASSAGE_VERSION)["valid"] == 0
        service.build(encoder)
        target = corpus.with_name(corpus.name + "-copy")
        shutil.copytree(corpus, target)
        destination = target / "data/attached.sqlite3"
        attach_copy(index.database, target, destination)
        with ManualIndex(destination, target) as attached:
            hits, status = SemanticIndex(attached).search("pad", encoder)
            assert status["state"] == "ready" and hits
            assert hits[0].source["document"]["library_id"] == index.library_id


def test_hybrid_keeps_exact_title_and_uses_article_level_fusion(corpus):
    with built(corpus) as index:
        hits = index.search("Pocket")
        semantic = [replace(index.search("Pad")[0], channels=["semantic"]), hits[0]]
        merged = fuse(hits, semantic, 2)
        assert merged[0].title == "Pocket"
        assert merged[0].channels == ["keyword", "semantic"]
        assert len({hit.path for hit in merged}) == len(merged)


def test_unavailable_model_falls_back_without_loading_qt(corpus, monkeypatch):
    monkeypatch.setattr(
        embedding, "make_embedder", lambda _: (_ for _ in ()).throw(ValueError("missing model"))
    )
    with built(corpus) as index:
        response = SearchService(index, {"provider": "glossary"}, {"mode": "hybrid"}).search("pock")
        assert response.hits[0].title == "Pocket"
        assert "missing model" in response.plan.notes[-1]


def test_download_resumes_validates_and_never_sends_document_text(tmp_path, monkeypatch):
    httpx = pytest.importorskip("httpx")
    data = b"a pinned model fixture"
    monkeypatch.setattr(
        embedding, "FILES", {"model.onnx": (len(data), hashlib.sha256(data).hexdigest())}
    )
    partial = tmp_path / "model.onnx.part"
    partial.write_bytes(data[:5])
    requests = []

    def response(request):
        requests.append(request)
        assert request.headers["Range"] == "bytes=5-"
        return httpx.Response(
            206, content=data[5:], headers={"Content-Range": f"bytes 5-{len(data) - 1}/{len(data)}"}
        )

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        assert embedding.download_model(tmp_path, client=client)["ready"]
    assert (tmp_path / "model.onnx").read_bytes() == data
    assert len(requests) == 1 and requests[0].method == "GET" and not requests[0].content
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        assert embedding.download_model(tmp_path, client=client)["ready"]
    assert len(requests) == 1


def test_download_rejects_corruption_and_cleans_lock(tmp_path, monkeypatch):
    httpx = pytest.importorskip("httpx")
    monkeypatch.setattr(embedding, "FILES", {"model.onnx": (3, hashlib.sha256(b"abc").hexdigest())})
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"bad"))
    ) as client:
        with pytest.raises(ValueError, match="校验失败"):
            embedding.download_model(tmp_path, client=client)
    assert not (tmp_path / "model.onnx").exists()
    from catia_manual.core.tasks import exclusive_file

    with exclusive_file(tmp_path / ".install.lock", "locked"):
        pass


def test_search_settings_roundtrip_remain_portable(corpus):
    config = corpus / "viewer/config.toml"
    config.parent.mkdir()
    config.write_text("[unrelated]\nvalue = 1\n", "utf-8")
    settings = Settings(
        corpus, corpus / "data/index.sqlite3", corpus / "none", "", corpus, config_path=config
    )
    directory = config.parent / "models/search"
    updated = save_settings(
        settings,
        {
            "search": {
                "mode": "hybrid",
                "model_dir": str(directory),
                "threads": 3,
            }
        },
    )
    assert updated.search["model_dir"] == str(directory)
    assert load_settings(config).search["mode"] == "hybrid"
    assert "models/search" in config.read_text("utf-8")
    assert "[unrelated]" in config.read_text("utf-8")


def test_simultaneous_builds_are_rejected_and_lock_recovers(corpus):
    from catia_manual.core.semantic import build_lock

    with built(corpus) as index:
        with build_lock(index.database):
            with pytest.raises(ValueError, match="正在建立"):
                SemanticIndex(index).build(Encoder())
        assert SemanticIndex(index).build(Encoder())["state"] == "ready"


def test_deleted_articles_cannot_survive_semantic_pruning(corpus):
    with built(corpus) as index:
        encoder, semantic = Encoder(), SemanticIndex(index)
        semantic.build(encoder)
        (corpus / "online/prtug_C2/other.htm").unlink()
        index.build()
        hits, _ = semantic.search("pocket", encoder)
        assert all(hit.title != "Pocket" for hit in hits)
        result = semantic.build(encoder)
        assert result["state"] == "ready"
        assert not index.connection.execute(
            "SELECT 1 FROM semantic_passages WHERE path LIKE '%other.htm'"
        ).fetchone()


def test_legacy_manifest_does_not_treat_new_coordination_lock_as_manual_content(corpus):
    from catia_manual.core.library import verify_copy

    with built(corpus) as index:
        exclusions = json.loads(index.meta("manifest_excludes"))
        index.set_meta(
            "manifest_excludes",
            json.dumps([path for path in exclusions if not path.endswith(".semantic-lock")]),
        )
        index.connection.commit()
        SemanticIndex(index).build(Encoder())
        assert verify_copy(index.database, corpus)["matched"]


def test_superseded_query_stops_before_loading_model(corpus, monkeypatch):
    def must_not_load(_):
        raise AssertionError("cancelled query must not load a model")

    monkeypatch.setattr(embedding, "make_embedder", must_not_load)
    cancel = threading.Event()
    cancel.set()
    with built(corpus) as index:
        service = SearchService(index, {"provider": "glossary"}, {"mode": "hybrid"})
        result = service.search("pad", cancel=cancel)
        assert result.hits == [] and not any("未就绪" in note for note in result.plan.notes)

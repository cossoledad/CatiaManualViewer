"""Resumable source-backed vector index and scoped retrieval, independent of Qt."""

import hashlib
import heapq
import json
import uuid
from dataclasses import dataclass, replace

from catia_manual.core.embedding import Cancelled, check_cancel
from catia_manual.core.index_schema import CHUNK_VERSION
from catia_manual.core.library import application_excludes
from catia_manual.core.library_contract import DocumentRef, SourceLocation
from catia_manual.core.models import SearchHit
from catia_manual.core.source import source_for_block
from catia_manual.core.tasks import exclusive_file

PASSAGE_VERSION = "semantic-passages-1"
VECTOR_SCHEMA_VERSION = "1"
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS semantic_models(fingerprint TEXT PRIMARY KEY,"
    "spec TEXT NOT NULL,dimension INTEGER NOT NULL,state TEXT NOT NULL,"
    "processed INTEGER NOT NULL,total INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS semantic_passages(model TEXT NOT NULL,id TEXT NOT NULL,"
    "path TEXT NOT NULL,content_hash TEXT NOT NULL,text TEXT NOT NULL,source TEXT NOT NULL,"
    "vector BLOB NOT NULL,generation TEXT NOT NULL,PRIMARY KEY(model,id))",
    "CREATE INDEX IF NOT EXISTS semantic_documents ON semantic_passages(model,path)",
)


def build_lock(database):
    return exclusive_file(
        database.with_name(database.name + ".semantic-lock"),
        "语义索引正在建立，请等待当前任务完成",
    )


@dataclass(frozen=True)
class Passage:
    id: str
    path: str
    content_hash: str
    text: str
    source: dict


def passages(library_id, document):
    """Group short adjacent blocks within one heading; split long text with overlap."""
    run, first, heading = [], None, None

    def emit():
        if not run:
            return
        body = " ".join(run)
        text = document.title + "\n" + " / ".join(heading) + "\n" + body
        source = source_for_block(library_id, document, first).to_dict()
        source["text_quote"] = run[0][:180]
        identity = hashlib.sha256(
            (document.path + "\n" + first.id + "\n" + text).encode()
        ).hexdigest()
        yield Passage(identity, document.path, document.content_hash, text, source)

    for block in document.blocks:
        for start in range(0, len(block.text), 1080):
            piece = block.text[start : start + 1200]
            if run and (heading != block.heading_path or sum(map(len, run)) + len(piece) > 1400):
                yield from emit()
                run = []
            if not run:
                first, heading = block, block.heading_path
            run.append(piece)
    yield from emit()


class SemanticIndex:
    def __init__(self, index):
        self.index, self.connection = index, index.connection
        if index.meta("semantic_schema_version") not in (None, VECTOR_SCHEMA_VERSION):
            raise ValueError("不支持的语义索引版本")

    def exists(self):
        return (
            self.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='semantic_models'"
            ).fetchone()
            is not None
        )

    def corpus_signature(self):
        digest = hashlib.sha256()
        for row in self.connection.execute(
            "SELECT path,content_hash FROM documents WHERE kind='article' ORDER BY path"
        ):
            digest.update(json.dumps(tuple(row), ensure_ascii=False).encode())
        digest.update(str(self.index.meta("parser_version")).encode())
        digest.update(str(self.index.meta("chunk_version")).encode())
        return digest.hexdigest()

    def ensure(self):
        for statement in SCHEMA:
            self.connection.execute(statement)
        self.index.set_meta("semantic_schema_version", VECTOR_SCHEMA_VERSION)
        self.connection.commit()

    def status(self, fingerprint):
        if not self.exists():
            return {"state": "absent", "processed": 0, "total": 0, "valid": 0}
        row = self.connection.execute(
            "SELECT * FROM semantic_models WHERE fingerprint=?", (fingerprint,)
        ).fetchone()
        if row is None:
            return {"state": "absent", "processed": 0, "total": 0, "valid": 0}
        valid = self.connection.execute(
            "SELECT count(*) FROM semantic_passages p JOIN documents d ON d.path=p.path "
            "AND d.content_hash=p.content_hash WHERE p.model=?",
            (fingerprint,),
        ).fetchone()[0]
        result = {**dict(row), "spec": json.loads(row["spec"]), "valid": valid}
        if result["state"] == "ready" and (
            result["spec"].get("corpus_signature") != self.corpus_signature()
        ):
            result["state"] = "stale"
        return result

    def build(self, embedder, *, progress=None, cancel=None, batch_size=8):
        exclusions = set(json.loads(self.index.meta("manifest_excludes", "[]")))
        updated = exclusions | set(application_excludes(self.index.database, self.index.root))
        if updated != exclusions:
            self.index.set_meta("manifest_excludes", json.dumps(sorted(updated)))
            self.connection.commit()
        with build_lock(self.index.database):
            return self._build(embedder, progress=progress, cancel=cancel, batch_size=batch_size)

    def _build(self, embedder, *, progress, cancel, batch_size):
        import numpy as np

        if self.index.meta("chunk_version") != CHUNK_VERSION:
            raise ValueError("请先完整更新文档索引")
        self.ensure()
        model = embedder.fingerprint + ":" + PASSAGE_VERSION
        generation = uuid.uuid4().hex
        paths = [
            row[0]
            for row in self.connection.execute(
                "SELECT path FROM documents WHERE kind='article' ORDER BY path"
            )
        ]
        if not paths:
            raise ValueError("当前没有文章，请先更新文档索引")
        spec = {
            "model": embedder.spec,
            "corpus_signature": self.corpus_signature(),
            "passage_version": PASSAGE_VERSION,
            "vector_dtype": "float32-le",
            "distance": "cosine",
        }
        total, processed, reused, embedded = 0, 0, 0, 0
        # Count one document at a time: never retain the full passage corpus in memory.
        for path in paths:
            check_cancel(cancel)
            row = self.connection.execute(
                "SELECT * FROM documents WHERE path=?", (path,)
            ).fetchone()
            if row is None:
                continue
            total += sum(
                1 for _ in passages(self.index.library_id, self.index.document_from_row(row))
            )
        self.connection.execute(
            "INSERT OR REPLACE INTO semantic_models VALUES(?,?,?,?,?,?)",
            (model, json.dumps(spec), embedder.dimension, "building", 0, total),
        )
        self.connection.commit()
        pending = []

        def flush():
            nonlocal processed, embedded
            if not pending:
                return
            check_cancel(cancel)
            vectors = np.asarray(embedder.encode([p.text for p in pending]), dtype="<f4")
            if (
                vectors.shape != (len(pending), embedder.dimension)
                or not np.isfinite(vectors).all()
            ):
                raise ValueError("向量维度或数值无效")
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            if (norms < 1e-12).any():
                raise ValueError("模型返回空向量")
            vectors /= norms
            self.connection.executemany(
                "INSERT OR REPLACE INTO semantic_passages VALUES(?,?,?,?,?,?,?,?)",
                [
                    (
                        model,
                        p.id,
                        p.path,
                        p.content_hash,
                        p.text,
                        json.dumps(p.source),
                        v.tobytes(),
                        generation,
                    )
                    for p, v in zip(pending, vectors, strict=True)
                ],
            )
            embedded += len(pending)
            processed += len(pending)
            pending.clear()
            update("building")

        def update(state):
            self.connection.execute(
                "UPDATE semantic_models SET state=?,processed=?,total=? WHERE fingerprint=?",
                (state, processed, total, model),
            )
            self.connection.commit()
            if progress:
                progress({"processed": processed, "total": total, "reused": reused})

        try:
            for path in paths:
                check_cancel(cancel)
                row = self.connection.execute(
                    "SELECT * FROM documents WHERE path=?", (path,)
                ).fetchone()
                if row is None:
                    continue
                for passage in passages(self.index.library_id, self.index.document_from_row(row)):
                    check_cancel(cancel)
                    existing = self.connection.execute(
                        "SELECT 1 FROM semantic_passages WHERE model=? AND id=?",
                        (model, passage.id),
                    ).fetchone()
                    if existing:
                        self.connection.execute(
                            "UPDATE semantic_passages SET generation=?,content_hash=?,source=? "
                            "WHERE model=? AND id=?",
                            (
                                generation,
                                passage.content_hash,
                                json.dumps(passage.source),
                                model,
                                passage.id,
                            ),
                        )
                        reused += 1
                        processed += 1
                        if processed % 64 == 0:
                            update("building")
                    else:
                        pending.append(passage)
                        if len(pending) >= batch_size:
                            flush()
            flush()
            self.connection.execute(
                "DELETE FROM semantic_passages WHERE model=? AND generation<>?", (model, generation)
            )
            update("ready")
        except Cancelled:
            update("paused")
        except BaseException:
            self.connection.rollback()
            update("failed")
            raise
        return {**self.status(model), "embedded": embedded, "reused": reused}

    def search(self, query, embedder, *, modules=None, limit=80, cancel=None):
        import numpy as np

        model = embedder.fingerprint + ":" + PASSAGE_VERSION
        check_cancel(cancel)
        status = self.status(model)
        if status["valid"] == 0:
            raise ValueError("请在智能搜索中建立语义索引")
        if modules is not None and not modules:
            return [], status
        parameters, where = [model], "p.model=?"
        if modules is not None:
            from catia_manual.core.scopes import scope_sql

            clause, values = scope_sql(self.connection, modules)
            where += " AND " + clause
            parameters.extend(values)
        cursor = self.connection.execute(
            "SELECT p.*,d.title,d.module FROM semantic_passages p JOIN documents d ON "
            "d.path=p.path AND d.content_hash=p.content_hash WHERE " + where,
            parameters,
        )
        check_cancel(cancel)
        vector = np.asarray(embedder.encode([query], query=True)[0], dtype=np.float32)
        if vector.shape != (embedder.dimension,) or not np.isfinite(vector).all():
            raise ValueError("查询向量无效")
        norm = np.linalg.norm(vector)
        if norm < 1e-12:
            raise ValueError("查询向量为空")
        vector /= norm
        limit = max(1, min(limit, 200))
        best, heap = {}, []
        while batch := cursor.fetchmany(1024):
            check_cancel(cancel)
            matrix = np.stack([np.frombuffer(row["vector"], dtype="<f4") for row in batch])
            if matrix.shape[1] != embedder.dimension or not np.isfinite(matrix).all():
                raise ValueError("语义索引损坏，请重新建立")
            scores = matrix @ vector
            for row, score in zip(batch, scores, strict=True):
                score, path = float(score), row["path"]
                if path in best and score <= best[path][0]:
                    continue
                while heap and (heap[0][1] not in best or best[heap[0][1]][0] != heap[0][0]):
                    heapq.heappop(heap)
                if path not in best and len(best) >= limit:
                    if score <= heap[0][0]:
                        continue
                    _, removed = heapq.heappop(heap)
                    del best[removed]
                # Retain only top article candidates; do not retain every vector/body in RAM.
                best[path] = (
                    score,
                    {key: row[key] for key in ("path", "title", "module", "text", "source")},
                )
                heapq.heappush(heap, (score, path))
                if len(heap) > limit * 4:
                    heap = [(value[0], key) for key, value in best.items()]
                    heapq.heapify(heap)
        results = []
        for score, row in sorted(best.values(), key=lambda pair: (-pair[0], pair[1]["path"]))[
            :limit
        ]:
            source = SourceLocation.from_dict(json.loads(row["source"]))
            results.append(
                SearchHit(
                    row["path"],
                    row["title"],
                    row["module"],
                    row["text"].split("\n", 2)[-1][:220],
                    -score,
                    DocumentRef(self.index.library_id, row["path"]).document_id,
                    source.to_dict(),
                    list(source.heading_path),
                    "语义相关 · 原文来源",
                    ["semantic"],
                    score,
                )
            )
        return results, status


def fuse(keyword, semantic, limit):
    """Reciprocal-rank fusion after article aggregation. Exact titles stay first."""
    entries, scores, channels = {}, {}, {}
    semantic_scores = {hit.path: hit.semantic_score for hit in semantic}
    for name, hits in (("keyword", keyword), ("semantic", semantic)):
        for rank, hit in enumerate(hits, 1):
            entries.setdefault(hit.path, hit)
            scores[hit.path] = scores.get(hit.path, 0) + 1 / (60 + rank)
            if name == "keyword":
                scores[hit.path] += min(hit.signals.get("title_terms", 0), 3) * 0.003
            channels.setdefault(hit.path, []).append(name)
            if name == "keyword" and hit.reason == "标题精确匹配":
                scores[hit.path] += 1
    return [
        replace(
            entries[path],
            semantic_score=semantic_scores.get(path),
            score=-scores[path],
            channels=channels[path],
            reason=entries[path].reason
            + (" · 关键词与语义共同命中" if len(channels[path]) > 1 else ""),
        )
        for path in sorted(
            entries,
            key=lambda p: (
                entries[p].signals.get("rank_tier", 9)
                if entries[p].signals.get("rank_tier", 9) <= 3
                else 9,
                -scores[p],
                p,
            ),
        )[:limit]
    ]

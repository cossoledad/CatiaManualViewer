import hashlib
import json
import re
import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from catia_manual.core.index_schema import CHUNK_VERSION, SCHEMA_VERSION, STATEMENTS
from catia_manual.core.library import application_excludes, backup_name, scan_manifest, snapshot
from catia_manual.core.library_contract import DocumentRef, SourceLocation, relative_path
from catia_manual.core.models import Block, Document, SearchHit
from catia_manual.core.parser import PARSER_VERSION, ManualParser
from catia_manual.core.query import TERMINOLOGY_VERSION, plan_query

SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents(
    id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
    module TEXT NOT NULL, kind TEXT NOT NULL, encoding TEXT NOT NULL,
    content_hash TEXT NOT NULL, mtime_ns INTEGER NOT NULL, size INTEGER NOT NULL,
    text TEXT NOT NULL, blocks TEXT NOT NULL, links TEXT NOT NULL, headings TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS document_module ON documents(module);
CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
    title, text, content=documents, content_rowid=id,
    tokenize='porter unicode61', prefix='2 3 4'
);
CREATE TRIGGER IF NOT EXISTS documents_ai AFTER INSERT ON documents BEGIN
    INSERT INTO documents_fts(rowid,title,text) VALUES(new.id,new.title,new.text);
END;
CREATE TRIGGER IF NOT EXISTS documents_ad AFTER DELETE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts,rowid,title,text)
    VALUES('delete',old.id,old.title,old.text);
END;
CREATE TRIGGER IF NOT EXISTS documents_au AFTER UPDATE ON documents BEGIN
    INSERT INTO documents_fts(documents_fts,rowid,title,text)
    VALUES('delete',old.id,old.title,old.text);
    INSERT INTO documents_fts(rowid,title,text) VALUES(new.id,new.title,new.text);
END;
CREATE TABLE IF NOT EXISTS translations(
    cache_key TEXT PRIMARY KEY, provider TEXT NOT NULL, target TEXT NOT NULL,
    source_text TEXT NOT NULL, translated_text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def portable_provider(value):
    return re.sub(r"local-argos-v3:.+:([a-f0-9]{16})(?=:|$)", r"local-argos-v3:\1", value)


class ManualIndex:
    def __init__(self, database: Path, root: Path):
        self.database, self.root = database.resolve(), root.resolve()
        database.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.module_labels = {}
        try:
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, SCHEMA_VERSION):
                raise ValueError(f"不支持的索引版本: {version}")
            if version:
                previous = self.meta("root")
                if previous and Path(previous).resolve() != self.root:
                    raise ValueError("索引属于另一文档目录，请验证副本后通过文档库管理复用")
            if version != SCHEMA_VERSION:
                if version == 1:
                    snapshot(database, backup_name(database))
                if version == 0:
                    self.connection.executescript(SCHEMA)
                self.connection.execute("BEGIN IMMEDIATE")
                if self.connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                    for statement in STATEMENTS:
                        self.connection.execute(statement)
                    self.set_meta("library_id", self.meta("library_id") or str(uuid.uuid4()))
                    self.set_meta("root", str(self.root))
                    self.set_meta("label", self.root.name or "CATIA V5")
                    self.set_meta("release", "")
                    self.set_meta("language", "en")
                    self.set_meta("manifest_verified", "0")
                    if version == 1:
                        for row in self.connection.execute("SELECT * FROM documents"):
                            doc = self.document_from_row(row)
                            self.index_chunks(doc, row["id"])
                        self.migrate_translation_identity()
                    self.connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                self.connection.commit()
            if self.connection.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                self.connection.execute("PRAGMA journal_mode=WAL")
            self.library_id = self.meta("library_id")
        except BaseException:
            self.connection.rollback()
            self.connection.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.connection.close()

    def meta(self, name, default=None):
        row = self.connection.execute("SELECT value FROM metadata WHERE key=?", (name,)).fetchone()
        return row[0] if row else default

    def set_meta(self, name, value):
        self.connection.execute("INSERT OR REPLACE INTO metadata VALUES(?,?)", (name, str(value)))

    def migrate_translation_identity(self):
        for row in list(self.connection.execute("SELECT * FROM translations")):
            provider = portable_provider(row["provider"])
            if provider != row["provider"]:
                key = hashlib.sha256(
                    f"{provider}\0{row['target']}\0{row['source_text']}".encode()
                ).hexdigest()
                self.connection.execute(
                    (
                        "INSERT OR IGNORE INTO "
                        "translations(cache_key,provider,target,source_text,translated_text) "
                        "VALUES(?,?,?,?,?)"
                    ),
                    (key, provider, row["target"], row["source_text"], row["translated_text"]),
                )
        exists = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='page_translations'"
        ).fetchone()
        if exists:
            for row in list(
                self.connection.execute("SELECT cache_key,fingerprint FROM page_translations")
            ):
                fingerprint = portable_provider(row["fingerprint"])
                if fingerprint != row["fingerprint"]:
                    self.connection.execute(
                        "UPDATE page_translations SET fingerprint=? WHERE cache_key=?",
                        (fingerprint, row["cache_key"]),
                    )

    @staticmethod
    def document_from_row(row):
        return Document(
            row["path"],
            row["title"],
            row["module"],
            row["kind"],
            row["encoding"],
            row["content_hash"],
            row["text"],
            blocks=[
                Block(**{**b, "heading_path": tuple(b.get("heading_path", ()))})
                for b in json.loads(row["blocks"])
            ],
            headings=json.loads(row["headings"]),
        )

    def index_chunks(self, doc, document_rowid):
        self.connection.execute("DELETE FROM chunks WHERE document_rowid=?", (document_rowid,))
        if doc.kind != "article":
            return
        keywords = self.module_labels.get(doc.module, doc.module) + " " + Path(doc.path).stem
        keywords += " " + " ".join(
            f"{link.label} {Path(link.target).name}"
            for link in doc.links
            if link.kind == "sample" and link.target
        )
        self.connection.executemany(
            (
                "INSERT INTO "
                "chunks(document_rowid,block_id,fragment,heading_path,title,heading,keywords,body)"
                " VALUES(?,?,?,?,?,?,?,?)"
            ),
            [
                (
                    document_rowid,
                    b.id,
                    b.fragment,
                    json.dumps(b.heading_path, ensure_ascii=False),
                    doc.title,
                    " / ".join(b.heading_path),
                    keywords,
                    b.text,
                )
                for b in doc.blocks
            ],
        )

    def upsert(self, doc, mtime_ns, size):
        self.connection.execute(
            """INSERT INTO documents(path,title,module,kind,encoding,content_hash,
          mtime_ns,size,text,blocks,links,headings) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(path) DO UPDATE SET title=excluded.title,module=excluded.module,
          kind=excluded.kind,encoding=excluded.encoding,content_hash=excluded.content_hash,
          mtime_ns=excluded.mtime_ns,size=excluded.size,text=excluded.text,
          blocks=excluded.blocks,links=excluded.links,headings=excluded.headings""",
            (
                doc.path,
                doc.title,
                doc.module,
                doc.kind,
                doc.encoding,
                doc.content_hash,
                mtime_ns,
                size,
                doc.text,
                json.dumps([asdict(b) for b in doc.blocks], ensure_ascii=False),
                json.dumps([asdict(link) for link in doc.links], ensure_ascii=False),
                json.dumps(doc.headings, ensure_ascii=False),
            ),
        )
        rowid = self.connection.execute(
            "SELECT id FROM documents WHERE path=?", (doc.path,)
        ).fetchone()[0]
        self.index_chunks(doc, rowid)

    def build(
        self, module=None, limit=None, force=False, progress: Callable[[dict], None] | None = None
    ):
        from catia_manual.core.catalog import ManualCatalog

        parser = ManualParser(self.root)
        if not self.root.is_dir():
            raise FileNotFoundError(f"文档目录不存在: {self.root}")
        if module and (Path(module).name != module or module in {".", ".."}):
            raise ValueError("模块名必须是 online 下的单个目录名")
        if limit is not None and limit < 1:
            raise ValueError("limit 必须大于 0")
        scope = parser.source(f"online/{module}") if module else self.root
        if not scope.is_dir():
            raise FileNotFoundError(f"模块目录不存在: {scope}")
        report = {"discovered": 0, "updated": 0, "unchanged": 0, "deleted": 0, "errors": []}
        force = (
            force
            or self.meta("parser_version") != PARSER_VERSION
            or self.meta("chunk_version") != CHUNK_VERSION
        )
        self.set_meta("manifest_verified", "0")
        exclusions = set(json.loads(self.meta("manifest_excludes", "[]")))
        exclusions.update(application_excludes(self.database, self.root))
        self.set_meta("manifest_excludes", json.dumps(sorted(exclusions)))
        self.connection.commit()
        manifest = None
        if not module and limit is None:
            try:
                manifest = scan_manifest(
                    self.root,
                    release=self.meta("release", ""),
                    language=self.meta("language", "en"),
                    exclude=[self.root / relative_path(p) for p in exclusions],
                    progress=progress,
                )
            except (OSError, ValueError) as exc:
                report["errors"].append({"phase": "manifest", "error": str(exc)})
                return report
            paths = [parser.source(f["path"]) for f in manifest["files"] if f["kind"] == "document"]
        else:
            paths = sorted(
                p for p in scope.rglob("*") if p.suffix.lower() in {".htm", ".html"} and p.is_file()
            )
            if limit:
                paths = paths[:limit]
        report["discovered"] = len(paths)
        hashes = {f["path"]: f["content_hash"] for f in manifest["files"]} if manifest else {}
        self.module_labels = {
            m.id: m.title + " " + m.label for m in ManualCatalog(self.root).manuals.values()
        }
        catalog_hash = hashlib.sha256(
            json.dumps(self.module_labels, sort_keys=True).encode()
        ).hexdigest()
        context_changed = catalog_hash != self.meta("catalog_hash")
        old = {
            row["path"]: row
            for row in self.connection.execute("SELECT path,content_hash FROM documents")
        }
        seen = set()
        batch_started = time.monotonic()
        try:
            self.connection.execute("BEGIN")
            for i, path in enumerate(paths, 1):
                relative = relative_path(path.relative_to(self.root).as_posix())
                seen.add(relative)
                self.connection.execute("SAVEPOINT document_update")
                try:
                    path = parser.source(relative)
                    stat = path.stat()
                    digest = hashes.get(relative)
                    if digest is None:
                        digest = hashlib.sha256(path.read_bytes()).hexdigest()
                    previous = old.get(relative)
                    if not force and previous and previous["content_hash"] == digest:
                        if context_changed:
                            row = self.connection.execute(
                                "SELECT * FROM documents WHERE path=?", (relative,)
                            ).fetchone()
                            self.index_chunks(self.document_from_row(row), row["id"])
                        report["unchanged"] += 1
                    else:
                        doc = parser.parse(relative)
                        if doc.content_hash != digest:
                            raise ValueError("扫描期间原文已变化，请重新更新索引")
                        self.upsert(doc, stat.st_mtime_ns, stat.st_size)
                        report["updated"] += 1
                    self.connection.execute("RELEASE document_update")
                except (OSError, ValueError) as exc:
                    self.connection.execute("ROLLBACK TO document_update")
                    self.connection.execute("RELEASE document_update")
                    report["errors"].append({"path": relative, "error": str(exc)})
                if i % 50 == 0 or time.monotonic() - batch_started >= 1:
                    self.connection.commit()
                    self.connection.execute("BEGIN")
                    batch_started = time.monotonic()
                if progress and (i % 100 == 0 or i == len(paths)):
                    progress({"phase": "index", "processed": i, "total": len(paths)})
            if limit is None and not report["errors"]:
                for relative in old.keys() - seen:
                    if not module or relative.startswith(f"online/{module}/"):
                        self.connection.execute("DELETE FROM documents WHERE path=?", (relative,))
                        report["deleted"] += 1
            if not report["errors"] and not module and limit is None:
                self.connection.execute("DELETE FROM library_files")
                self.connection.executemany(
                    (
                        "INSERT INTO library_files(path,size,content_hash,kind) "
                        "VALUES(:path,:size,:content_hash,:kind)"
                    ),
                    manifest["files"],
                )
                self.set_meta("manifest_hash", manifest["manifest_hash"])
                self.set_meta("manifest_verified", "1")
                self.set_meta("parser_version", PARSER_VERSION)
                self.set_meta("chunk_version", CHUNK_VERSION)
                self.set_meta("catalog_hash", catalog_hash)
                self.set_meta("terminology_version", TERMINOLOGY_VERSION)
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        report.update(
            library_id=self.library_id, manifest_verified=self.meta("manifest_verified") == "1"
        )
        return report

    def search(self, query, module=None, limit=30, *, match=None, modules=None, paths=None):
        if match is None:
            plan = plan_query(query, self.aliases(modules or ([module] if module else None)))
            match = "" if plan.unknown else plan.match
        if not match or modules == [] or modules == ():
            return []
        filters, params = ["chunks_fts MATCH ?", "d.kind='article'"], [match]
        if module:
            filters.append("d.module=?")
            params.append(module)
        if modules is not None:
            chosen = list(dict.fromkeys(modules))
            filters.append("d.module IN (" + ",".join("?" for _ in chosen) + ")")
            params.extend(chosen)
        if paths is not None:
            if not paths:
                return []
            filters.append("d.path IN (" + ",".join("?" for _ in paths) + ")")
            params.extend(paths)
        sql = (
            """WITH matches AS MATERIALIZED (
          SELECT d.path,d.title,d.module,d.content_hash,c.block_id,c.fragment,c.heading_path,c.body,
          snippet(chunks_fts,3,'[',']',' … ',36) AS snippet,
          bm25(chunks_fts,8.0,5.0,2.0,1.0) AS score,
          highlight(chunks_fts,0,'⟦','⟧') AS hit_title,
          highlight(chunks_fts,1,'⟦','⟧') AS hit_heading,
          highlight(chunks_fts,2,'⟦','⟧') AS hit_keywords
          FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.rowid
          JOIN documents d ON d.id=c.document_rowid
          WHERE """
            + " AND ".join(filters)
            + """), ranked AS (
          SELECT *,row_number() OVER(PARTITION BY path ORDER BY score,block_id) AS ordinal
          FROM matches)
          SELECT * FROM ranked WHERE ordinal=1
          ORDER BY (lower(title)=?) DESC,score,path LIMIT ?"""
        )
        params.extend([" ".join(query.lower().split()), max(1, min(limit, 200))])
        hits = []
        for row in self.connection.execute(sql, params):
            ref = DocumentRef(self.library_id, row["path"])
            heading = json.loads(row["heading_path"])
            source = SourceLocation(
                ref,
                row["content_hash"],
                fragment=row["fragment"],
                block_id=row["block_id"],
                heading_path=heading,
                text_quote=row["body"][:180],
                text_suffix=row["body"][180:244],
            )
            title_match = query.casefold().strip() == row["title"].casefold()
            reason = (
                "标题精确匹配"
                if title_match
                else "标题关键词匹配"
                if "⟦" in row["hit_title"]
                else "章节标题匹配"
                if "⟦" in row["hit_heading"]
                else "手册分类或文件名匹配"
                if "⟦" in row["hit_keywords"]
                else "正文关键词匹配"
            )
            hits.append(
                SearchHit(
                    row["path"],
                    row["title"],
                    row["module"],
                    row["snippet"],
                    row["score"],
                    ref.document_id,
                    source.to_dict(),
                    heading,
                    reason,
                )
            )
        return hits

    def document_candidates(self, match, module=None, modules=None, limit=200):
        if modules == [] or modules == ():
            return []
        sql = (
            "SELECT d.path FROM documents_fts JOIN documents d ON d.id=documents_fts.rowid "
            "WHERE documents_fts MATCH ? AND d.kind='article'"
        )
        params = [match]
        if module:
            sql += " AND d.module=?"
            params.append(module)
        if modules is not None:
            sql += " AND d.module IN (" + ",".join("?" for _ in modules) + ")"
            params.extend(modules)
        sql += " ORDER BY bm25(documents_fts,8.0,1.0),d.path LIMIT ?"
        params.append(max(1, min(limit, 200)))
        return [r[0] for r in self.connection.execute(sql, params)]

    def aliases(self, modules=None):
        result, scoped = {}, {}
        for row in self.connection.execute(
            "SELECT alias,english,module FROM term_aliases ORDER BY module"
        ):
            if not row["module"]:
                result[row["alias"]] = tuple(row["english"].split("|"))
            elif modules is not None and row["module"] in modules:
                scoped.setdefault(row["alias"], []).extend(row["english"].split("|"))
        result.update({alias: tuple(dict.fromkeys(values)) for alias, values in scoped.items()})
        return result

    def set_alias(self, alias, english, module=""):
        alias, english = alias.strip(), english.strip()
        if (
            not alias
            or not english
            or len(alias) > 100
            or len(english) > 200
            or not re.fullmatch(r"[A-Za-z0-9 _|.-]+", english)
            or any(not re.search(r"[A-Za-z0-9]", part) for part in english.split("|"))
        ):
            raise ValueError("请填写术语别名与英文关键词（多个同义词用 | 分隔）")
        self.connection.execute(
            "INSERT OR REPLACE INTO term_aliases VALUES(?,?,?)", (alias, english, module)
        )
        self.connection.commit()

    def delete_alias(self, alias, module=""):
        self.connection.execute(
            "DELETE FROM term_aliases WHERE alias=? AND module=?", (alias, module)
        )
        self.connection.commit()

    def suggestions(self, query):
        import difflib

        vocabulary = [
            r[0]
            for r in self.connection.execute(
                "SELECT term FROM keyword_vocabulary WHERE length(term)>3 ORDER BY doc DESC "
                "LIMIT 4000"
            )
        ]
        tokens = re.findall(r"[a-zA-Z]+", query)
        if not tokens or len(tokens) > 3:
            return []
        corrected = []
        for token in tokens:
            word = token.casefold()
            close = (
                difflib.get_close_matches(word, vocabulary, n=1, cutoff=0.8)
                if word not in vocabulary
                else [word]
            )
            corrected.append(close[0] if close else word)
        value = " ".join(corrected)
        return [value] if value != " ".join(tokens).casefold() else []

    def save_location(self, source):
        if source.document.library_id != self.library_id:
            raise ValueError("阅读位置属于另一文档库")
        self.connection.execute(
            "INSERT OR REPLACE INTO reader_locations(document_id,source) VALUES(?,?)",
            (source.document.document_id, json.dumps(source.to_dict(), ensure_ascii=False)),
        )
        self.connection.commit()

    def load_location(self, path):
        ref = DocumentRef(self.library_id, path)
        row = self.connection.execute(
            "SELECT source FROM reader_locations WHERE document_id=?", (ref.document_id,)
        ).fetchone()
        return SourceLocation.from_dict(json.loads(row[0])) if row else None

    def get(self, relative):
        row = self.connection.execute(
            "SELECT * FROM documents WHERE path=?", (relative,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        for name in ("blocks", "links", "headings"):
            result[name] = json.loads(result[name])
        result["document_id"] = DocumentRef(self.library_id, relative).document_id
        return result

    def stats(self):
        return {
            "documents": self.connection.execute("SELECT count(*) FROM documents").fetchone()[0],
            "chunks": self.connection.execute("SELECT count(*) FROM chunks").fetchone()[0],
            "modules": [
                dict(row)
                for row in self.connection.execute(
                    "SELECT module,count(*) AS documents FROM documents "
                    "GROUP BY module ORDER BY module"
                )
            ],
            "sqlite_version": sqlite3.sqlite_version,
            "schema_version": SCHEMA_VERSION,
            "parser_version": self.meta("parser_version"),
            "chunk_version": self.meta("chunk_version"),
            "terminology_version": TERMINOLOGY_VERSION,
            "library_id": self.library_id,
            "label": self.meta("label"),
            "release": self.meta("release"),
            "language": self.meta("language"),
            "manifest_verified": self.meta("manifest_verified") == "1",
            "manifest_hash": self.meta("manifest_hash"),
            "files": self.connection.execute("SELECT count(*) FROM library_files").fetchone()[0],
        }

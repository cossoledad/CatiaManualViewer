import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from catia_manual.core.models import Document, SearchHit
from catia_manual.core.parser import ManualParser

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


class ManualIndex:
    def __init__(self, database: Path, root: Path):
        database.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.root = root.resolve()
        try:
            self.connection.execute("PRAGMA journal_mode=WAL")
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"不支持的索引版本: {version}，请使用新的数据库路径")
            self.connection.executescript(SCHEMA)
            previous = self.connection.execute(
                "SELECT value FROM metadata WHERE key='root'"
            ).fetchone()
            if previous and Path(previous[0]).resolve() != self.root:
                raise ValueError("索引属于另一文档目录，请配置独立数据库")
            self.connection.execute(
                "INSERT OR IGNORE INTO metadata VALUES('root',?)", (str(self.root),)
            )
            self.connection.execute("PRAGMA user_version=1")
            self.connection.commit()
        except Exception:
            self.connection.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.connection.close()

    def upsert(self, doc: Document, mtime_ns: int, size: int) -> None:
        self.connection.execute(
            """INSERT INTO documents(
            path,title,module,kind,encoding,content_hash,mtime_ns,size,text,blocks,links,headings)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET
            title=excluded.title,module=excluded.module,kind=excluded.kind,
            encoding=excluded.encoding,content_hash=excluded.content_hash,mtime_ns=excluded.mtime_ns,
            size=excluded.size,text=excluded.text,blocks=excluded.blocks,links=excluded.links,
            headings=excluded.headings""",
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

    def build(
        self,
        module: str | None = None,
        limit: int | None = None,
        force: bool = False,
        progress: Callable[[dict], None] | None = None,
    ) -> dict:
        parser = ManualParser(self.root)
        if not self.root.is_dir():
            raise FileNotFoundError(f"文档目录不存在: {self.root}")
        if module and (Path(module).name != module or module in {".", ".."}):
            raise ValueError("模块名必须是 online 下的单个目录名")
        scope = parser.source(f"online/{module}") if module else self.root
        if not scope.is_dir():
            raise FileNotFoundError(f"模块目录不存在: {scope}")
        if limit is not None and limit < 1:
            raise ValueError("limit 必须大于 0")
        paths = sorted(
            p for p in scope.rglob("*") if p.suffix.lower() in {".htm", ".html"} and p.is_file()
        )
        if limit:
            paths = paths[:limit]
        report = {
            "discovered": len(paths),
            "updated": 0,
            "unchanged": 0,
            "deleted": 0,
            "errors": [],
        }
        old = {
            row["path"]: row
            for row in self.connection.execute("SELECT path,mtime_ns,size FROM documents")
        }
        seen = set()
        try:
            for i, path in enumerate(paths, 1):
                relative = path.relative_to(self.root).as_posix()
                seen.add(relative)
                try:
                    stat = path.stat()
                    previous = old.get(relative)
                    if (
                        not force
                        and previous
                        and (previous["mtime_ns"], previous["size"])
                        == (stat.st_mtime_ns, stat.st_size)
                    ):
                        report["unchanged"] += 1
                    else:
                        self.upsert(parser.parse(relative), stat.st_mtime_ns, stat.st_size)
                        report["updated"] += 1
                except (OSError, ValueError) as exc:
                    report["errors"].append({"path": relative, "error": str(exc)})
                if i % 100 == 0:
                    self.connection.commit()
                    if progress:
                        progress({"processed": i, "total": len(paths)})
            # A bounded scan or any parse error must never prune a valid previous index.
            if limit is None and not report["errors"]:
                for relative in old.keys() - seen:
                    if not module or relative.startswith(f"online/{module}/"):
                        self.connection.execute("DELETE FROM documents WHERE path=?", (relative,))
                        report["deleted"] += 1
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        return report

    def search(self, query: str, module: str | None = None, limit: int = 30) -> list[SearchHit]:
        # Plain user input cannot inject FTS operators or cause MATCH syntax errors.
        words = re.findall(r"[^\W_]+", query, re.UNICODE)
        if not words:
            return []
        match = " AND ".join('"' + word.replace('"', '""') + '"*' for word in words)
        sql = """SELECT d.path,d.title,d.module,
        snippet(documents_fts,1,'[',']',' … ',36) AS snippet,
        bm25(documents_fts,8.0,1.0) AS score
        FROM documents_fts JOIN documents d ON d.id=documents_fts.rowid
        WHERE documents_fts MATCH ? AND d.kind='article'"""
        params: list = [match]
        if module:
            sql += " AND d.module=?"
            params.append(module)
        sql += " ORDER BY (lower(d.title)=?) DESC,score,d.path LIMIT ?"
        params.extend([" ".join(query.lower().split()), max(1, min(limit, 200))])
        return [SearchHit(**dict(row)) for row in self.connection.execute(sql, params)]

    def get(self, relative: str) -> dict | None:
        row = self.connection.execute(
            "SELECT * FROM documents WHERE path=?", (relative,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        for key in ("blocks", "links", "headings"):
            result[key] = json.loads(result[key])
        return result

    def stats(self) -> dict:
        return {
            "documents": self.connection.execute("SELECT count(*) FROM documents").fetchone()[0],
            "modules": [
                dict(row)
                for row in self.connection.execute(
                    "SELECT module,count(*) AS documents FROM documents "
                    "GROUP BY module ORDER BY module"
                )
            ],
            "sqlite_version": sqlite3.sqlite_version,
        }

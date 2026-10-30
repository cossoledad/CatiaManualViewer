"""Article ranking, bilingual commands and many-to-many manual navigation. No Qt."""

import hashlib
import json
import re

from bs4 import Comment, NavigableString
from lxml import etree, html

from catia_manual.core.catalog import ManualCatalog
from catia_manual.core.library_contract import DocumentRef, SourceLocation
from catia_manual.core.models import SearchHit
from catia_manual.core.parser import ManualParser
from catia_manual.core.paths import resolve_link
from catia_manual.core.query import plan_query
from catia_manual.core.scopes import scope_sql
from catia_manual.core.source import source_for_block
from catia_manual.core.tasks import exclusive_file
from catia_manual.core.terminology import (
    chinese_label,
    commands,
    english_names,
    lookup,
    vocabulary,
)

STRUCTURE_VERSION = "articles-1"
ROLE_LABELS = {
    "task": "操作教程",
    "overview": "功能概览",
    "reference": "参考说明",
    "toolbar": "工具栏",
    "api": "API",
    "article": "正文",
    "index": "关键词索引",
}
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS manual_placements(id TEXT PRIMARY KEY,manual TEXT NOT NULL,"
    "parent TEXT NOT NULL,position INTEGER NOT NULL,title TEXT NOT NULL,path TEXT NOT NULL,"
    "fragment TEXT NOT NULL,trail TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS placement_path ON manual_placements(path,manual)",
    "CREATE TABLE IF NOT EXISTS manual_membership(manual TEXT NOT NULL,path TEXT NOT NULL,"
    "PRIMARY KEY(manual,path))",
    "CREATE INDEX IF NOT EXISTS membership_path ON manual_membership(path,manual)",
    "CREATE TABLE IF NOT EXISTS manual_keywords(manual TEXT NOT NULL,term TEXT NOT NULL,"
    "path TEXT NOT NULL,fragment TEXT NOT NULL,PRIMARY KEY(manual,term,path,fragment))",
    "CREATE INDEX IF NOT EXISTS manual_keyword_path ON manual_keywords(path)",
    "CREATE TABLE IF NOT EXISTS article_metadata(path TEXT PRIMARY KEY,content_hash TEXT NOT NULL,"
    "role TEXT NOT NULL,summary TEXT NOT NULL,command_terms TEXT NOT NULL,"
    "index_terms TEXT NOT NULL,"
    "toc_terms TEXT NOT NULL,title_zh TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS article_commands(path TEXT NOT NULL,command TEXT NOT NULL,"
    "PRIMARY KEY(path,command))",
    "CREATE INDEX IF NOT EXISTS command_pages ON article_commands(command,path)",
    "CREATE VIEW IF NOT EXISTS article_content AS SELECT d.id,d.title,m.command_terms,"
    "m.index_terms,m.summary,m.toc_terms,d.text AS body FROM documents d JOIN article_metadata m "
    "ON m.path=d.path",
    "CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(title,command_terms,index_terms,"
    "summary,toc_terms,body,content='article_content',content_rowid='id',"
    "tokenize='porter unicode61')",
    """CREATE TRIGGER IF NOT EXISTS articles_before_update BEFORE UPDATE ON documents BEGIN
      INSERT INTO articles_fts(articles_fts,rowid,title,command_terms,
      index_terms,summary,toc_terms,body)
      SELECT 'delete',old.id,old.title,m.command_terms,m.index_terms,
      m.summary,m.toc_terms,old.text
      FROM article_metadata m WHERE m.path=old.path;
      DELETE FROM article_metadata WHERE path=old.path;
      DELETE FROM article_commands WHERE path=old.path;
    END""",
    """CREATE TRIGGER IF NOT EXISTS articles_before_delete BEFORE DELETE ON documents BEGIN
      INSERT INTO articles_fts(articles_fts,rowid,title,command_terms,
      index_terms,summary,toc_terms,body)
      SELECT 'delete',old.id,old.title,m.command_terms,m.index_terms,m.summary,m.toc_terms,old.text
      FROM article_metadata m WHERE m.path=old.path;
      DELETE FROM article_metadata WHERE path=old.path;
      DELETE FROM article_commands WHERE path=old.path;
    END""",
)


def tidy(text):
    return " ".join(text.split())


def article_info(parser, path, title):
    root = html.document_fromstring(parser.source(path).read_bytes() or b"<html></html>")
    anchors = set(root.xpath("//*[@id]/@id | //*[@name]/@name"))
    identities = sorted(anchors & commands().keys())
    keyword_anchors = [
        anchor.removeprefix("ix-commands::")
        for anchor in anchors
        if anchor.startswith("ix-commands::")
    ]
    summary = ""
    for image in root.xpath("//img[@src]"):
        if "/atarget." in image.get("src").replace("\\", "/").lower():
            rows = image.xpath("ancestor::tr[1]")
            if rows:
                summary = tidy(rows[0].text_content())[:1200]
                break
    if not summary:
        for paragraph in root.xpath("//body//p[not(ancestor::script or ancestor::style)]"):
            text = tidy(paragraph.text_content())
            if len(text) >= 45:
                summary = text[:1200]
                break
    role = "article"
    classes = set(root.xpath("//p/@class"))
    if classes & {"ix1", "ix2", "sm1", "sm2"} or root.xpath("//frameset"):
        role = "index"
    elif (
        "task" in classes
        or root.xpath("//ol[@class='task']")
        or summary.lower().startswith("this task")
    ):
        role = "task"
    elif "toolbar" in title.lower():
        role = "toolbar"
    elif title.lower().startswith("managing ") and len(root.xpath("//a[@href]")) > 2:
        role = "overview"
    elif re.search(r"reference|constraints$|about |glossary", title, re.I):
        role = "reference"
    elif len(path.split("/")) > 1 and path.split("/")[1].startswith(("CAA", "interfaces")):
        role = "api"
    command_terms = list(keyword_anchors)
    for identity in identities:
        entry = commands()[identity]
        command_terms += entry["en"] + entry["zh"]
    return role, summary, identities, "\n".join(dict.fromkeys(command_terms))


class StructureIndex:
    def __init__(self, index):
        self.index, self.db = index, index.connection

    def ready(self):
        return (
            self.index.meta("structure_version") == STRUCTURE_VERSION
            and self.index.meta("structure_terms_revision") == vocabulary()["revision"]
        )

    def build(self, progress=None, backup=False):
        index, parser, catalog = (
            self.index,
            ManualParser(self.index.root),
            ManualCatalog(self.index.root),
        )
        # Shared with other application files excluded from the documentation manifest.
        lock = index.database.parent / "backups" / (index.database.stem + "-structure.lock")
        with exclusive_file(lock, "正在整理目录与术语，请等待当前任务完成"):
            if backup and self.index.meta("structure_version") is None:
                from catia_manual.core.library import backup_name, snapshot

                destination = backup_name(index.database, "structure0")
                snapshot(index.database, destination)
                index.set_meta("structure_backup", destination.name)
            for statement in SCHEMA:
                self.db.execute(statement)
            index.set_meta("structure_version", "building")
            self.db.commit()
            digest = hashlib.sha256(vocabulary()["revision"].encode())
            digest.update(STRUCTURE_VERSION.encode())
            for manual in catalog.manuals.values():
                digest.update((manual.id + manual.title).encode())
                for path in ([manual.toc] if manual.toc else []) + [
                    p.relative_to(index.root).as_posix()
                    for p in sorted((index.root / "online" / manual.id).glob("*ix.htm"))
                ]:
                    digest.update(path.encode())
                    digest.update(parser.source(path).read_bytes())
            context = digest.hexdigest()
            context_changed = index.meta("structure_context") != context
            if context_changed:
                self.db.execute("INSERT INTO articles_fts(articles_fts) VALUES('delete-all')")
                self.db.execute("DELETE FROM article_metadata")
                self.db.execute("DELETE FROM article_commands")
                self.db.execute("DELETE FROM manual_placements")
                self.db.execute("DELETE FROM manual_membership")
                self.db.execute("DELETE FROM manual_keywords")
                for manual in catalog.manuals.values():
                    stack = []
                    for ordinal, entry in enumerate(parser.toc(manual.toc) if manual.toc else []):
                        while stack and stack[-1][0] >= entry.depth:
                            stack.pop()
                        parent = stack[-1][1] if stack else ""
                        identity = hashlib.sha256(
                            f"{manual.id}:{ordinal}:{entry.path}:{entry.fragment}".encode()
                        ).hexdigest()[:24]
                        trail = (stack[-1][2] if stack else []) + [entry.title]
                        self.db.execute(
                            "INSERT INTO manual_placements VALUES(?,?,?,?,?,?,?,?)",
                            (
                                identity,
                                manual.id,
                                parent,
                                ordinal,
                                entry.title,
                                entry.path,
                                entry.fragment,
                                json.dumps(trail, ensure_ascii=False),
                            ),
                        )
                        self.db.execute(
                            "INSERT OR IGNORE INTO manual_membership VALUES(?,?)",
                            (manual.id, entry.path),
                        )
                        stack.append((entry.depth, identity, trail))
                    for file in sorted((index.root / "online" / manual.id).glob("*ix.htm")):
                        soup, _, _ = parser.soup(file.relative_to(index.root).as_posix())
                        for paragraph in soup.select("p.ix1,p.ix2,p.ix3,p.ix4"):
                            term = tidy(
                                " ".join(
                                    str(n)
                                    for n in paragraph.descendants
                                    if isinstance(n, NavigableString)
                                    and not isinstance(n, Comment)
                                    and not n.find_parent("a")
                                )
                            )
                            if not term:
                                continue
                            for anchor in paragraph.find_all("a", href=True):
                                link = resolve_link(index.root, file, anchor["href"])
                                if link.kind == "document" and link.target:
                                    self.db.execute(
                                        "INSERT OR IGNORE INTO manual_keywords VALUES(?,?,?,?)",
                                        (manual.id, term, link.target, link.fragment),
                                    )
                                    self.db.execute(
                                        "INSERT OR IGNORE INTO manual_membership VALUES(?,?)",
                                        (manual.id, link.target),
                                    )
                index.set_meta("structure_context", context)
                self.db.commit()
            paths = [
                tuple(row)
                for row in self.db.execute(
                    "SELECT path,title,content_hash,kind FROM documents ORDER BY path"
                )
            ]
            changed, skipped, errors = 0, 0, []
            self.db.execute("BEGIN")
            for ordinal, (path, title, content_hash, kind) in enumerate(paths, 1):
                previous = self.db.execute(
                    "SELECT content_hash FROM article_metadata WHERE path=?", (path,)
                ).fetchone()
                if previous and previous[0] == content_hash:
                    skipped += 1
                else:
                    self.db.execute("SAVEPOINT structure_article")
                    try:
                        role, summary, identities, terms = article_info(parser, path, title)
                        if kind != "article":
                            role = "index"
                        index_terms = "\n".join(
                            row[0]
                            for row in self.db.execute(
                                "SELECT DISTINCT term FROM manual_keywords WHERE path=?", (path,)
                            )
                        )
                        toc_terms = "\n".join(
                            row[0]
                            for row in self.db.execute(
                                "SELECT title FROM manual_placements WHERE path=?", (path,)
                            )
                        )
                        manuals = {
                            row[0]
                            for row in self.db.execute(
                                "SELECT manual FROM manual_membership WHERE path=?", (path,)
                            )
                        }
                        manuals.add(path.split("/")[1] if "/" in path else "")
                        toc_terms += "\n" + "\n".join(
                            catalog.manuals[identity].title + " " + catalog.manuals[identity].label
                            for identity in sorted(manuals)
                            if identity in catalog.manuals
                        )
                        old = self.db.execute(
                            "SELECT * FROM article_content WHERE id="
                            "(SELECT id FROM documents WHERE path=?)",
                            (path,),
                        ).fetchone()
                        if old:
                            self.db.execute(
                                "INSERT INTO articles_fts(articles_fts,rowid,title,command_terms,"
                                "index_terms,summary,toc_terms,body) "
                                "VALUES('delete',?,?,?,?,?,?,?)",
                                tuple(old),
                            )
                        self.db.execute(
                            "INSERT OR REPLACE INTO article_metadata VALUES(?,?,?,?,?,?,?,?)",
                            (
                                path,
                                content_hash,
                                role,
                                summary,
                                terms,
                                index_terms,
                                toc_terms,
                                chinese_label(title),
                            ),
                        )
                        self.db.execute("DELETE FROM article_commands WHERE path=?", (path,))
                        self.db.executemany(
                            "INSERT INTO article_commands VALUES(?,?)",
                            [(path, identity) for identity in identities],
                        )
                        self.db.execute(
                            "INSERT INTO articles_fts(rowid,title,command_terms,index_terms,"
                            "summary,toc_terms,body) SELECT * FROM article_content WHERE id="
                            "(SELECT id FROM documents WHERE path=?)",
                            (path,),
                        )
                        changed += 1
                        self.db.execute("RELEASE structure_article")
                    except (OSError, ValueError, etree.ParserError) as exc:
                        self.db.execute("ROLLBACK TO structure_article")
                        self.db.execute("RELEASE structure_article")
                        errors.append({"path": path, "error": str(exc)})
                if ordinal % 100 == 0 or ordinal == len(paths):
                    self.db.commit()
                    if progress:
                        progress({"phase": "structure", "processed": ordinal, "total": len(paths)})
                    self.db.execute("BEGIN")
            if not errors:
                index.set_meta("structure_context", context)
                index.set_meta("structure_version", STRUCTURE_VERSION)
                index.set_meta("structure_terms_revision", vocabulary()["revision"])
            self.db.commit()
            return {
                "updated": changed,
                "unchanged": skipped,
                "errors": errors,
                "placements": self.db.execute("SELECT count(*) FROM manual_placements").fetchone()[
                    0
                ],
            }

    def placements(self, path, manuals=()):
        rows = self.db.execute(
            "SELECT * FROM manual_placements WHERE path=? ORDER BY manual,position", (path,)
        )
        values = [{**dict(row), "trail": json.loads(row["trail"])} for row in rows]
        document = self.db.execute("SELECT title FROM documents WHERE path=?", (path,)).fetchone()
        title = document[0] if document else ""
        return sorted(
            values,
            key=lambda row: (
                row["manual"] not in manuals,
                row["title"].casefold() != title.casefold(),
                row["position"],
                row["manual"],
            ),
        )

    def related(self, path):
        document = self.db.execute("SELECT links FROM documents WHERE path=?", (path,)).fetchone()
        result, seen = [], set()
        for link in json.loads(document[0]) if document else []:
            target = link.get("target")
            if link.get("kind") != "document" or not target or target == path or target in seen:
                continue
            seen.add(target)
            row = self.db.execute(
                "SELECT d.title,d.content_hash,m.role FROM documents d JOIN article_metadata m "
                "ON m.path=d.path WHERE d.path=? AND m.role IN ('reference','toolbar')",
                (target,),
            ).fetchone()
            if row:
                source = SourceLocation(
                    DocumentRef(self.index.library_id, target),
                    row["content_hash"],
                    fragment=link.get("fragment", ""),
                )
                result.append(
                    {
                        "path": target,
                        "title": row["title"],
                        "role": row["role"],
                        "source": source.to_dict(),
                    }
                )
            if len(result) == 5:
                break
        return result

    def search(self, query, match, *, modules=None, module=None, paths=None, limit=30):
        aliases = english_names(query)
        identities = lookup(query)
        where, params = ["articles_fts MATCH ?", "m.role<>'index'"], [match]
        clause, values = scope_sql(self.db, modules)
        where.append(clause)
        params.extend(values)
        if module:
            where.append("d.module=?")
            params.append(module)
        if paths is not None:
            if not paths:
                return []
            where.append("d.path IN (" + ",".join("?" for _ in paths) + ")")
            params.extend(paths)
        # SQL ordering before LIMIT prevents common body mentions from starving command pages.
        titles = [tidy(value).casefold() for value in (query, *aliases)]
        exact = "lower(trim(d.title)) IN (" + ",".join("?" for _ in titles) + ")"
        direct = "0"
        if identities:
            direct = (
                "EXISTS(SELECT 1 FROM article_commands ac WHERE ac.path=d.path AND ac.command IN ("
                + ",".join("?" for _ in identities)
                + "))"
            )
        direct = "(" + direct + " OR EXISTS(SELECT 1 FROM manual_keywords mk WHERE "
        direct += (
            "mk.path=d.path AND lower(trim(mk.term)) IN (" + ",".join("?" for _ in titles) + ")))"
        )
        tier = f"CASE WHEN {exact} THEN 0 WHEN ({direct}) AND m.role='task' THEN 1 "
        tier += "WHEN d.id IN(SELECT rowid FROM articles_fts WHERE articles_fts MATCH ?) THEN 2 "
        tier += "WHEN d.id IN(SELECT rowid FROM articles_fts WHERE articles_fts MATCH ?) THEN 3 "
        tier += "WHEN instr(highlight(articles_fts,3,'⟦','⟧'),'⟦')>0 THEN 4 ELSE 5 END"
        sql = "SELECT d.path,d.title,d.module,d.content_hash,m.*,"
        sql += f"{tier} AS tier,bm25(articles_fts,12,14,10,5,1,1) AS rank,"
        sql += "snippet(articles_fts,5,'[',']',' … ',30) AS snippet FROM articles_fts "
        sql += "JOIN documents d ON d.id=articles_fts.rowid "
        sql += "JOIN article_metadata m ON m.path=d.path WHERE "
        sql += " AND ".join(where) + " ORDER BY tier,rank,d.path LIMIT ?"
        rows = list(
            self.db.execute(
                sql,
                [
                    *titles,
                    *identities,
                    *titles,
                    "title: (" + match + ")",
                    "{command_terms index_terms}: (" + match + ")",
                    *params,
                    min(max(limit, 1), 200),
                ],
            )
        )
        sources = (
            {
                h.path: h
                for h in self.index.search_chunks(
                    query, match=match, paths=[row["path"] for row in rows], limit=200
                )
            }
            if rows
            else {}
        )
        hits = []
        strict_sources = set(sources)
        missing = [row["path"] for row in rows if row["path"] not in sources]
        if missing:
            groups = plan_query(query, self.index.aliases(modules)).groups
            if groups:
                sources.update(
                    {
                        hit.path: hit
                        for hit in self.index.search_chunks(
                            query,
                            match=" OR ".join("(" + group + ")" for group in groups),
                            paths=missing,
                            limit=200,
                        )
                    }
                )
        for row in rows:
            positions = self.placements(row["path"], getattr(modules, "manuals", ()))
            position = positions[0] if positions else None
            role = row["role"]
            source = (
                sources[row["path"]].source
                if row["path"] in sources
                else SourceLocation(
                    DocumentRef(self.index.library_id, row["path"]), row["content_hash"]
                ).to_dict()
            )
            if row["tier"] <= 3:
                command = next(
                    (
                        identity
                        for identity in identities
                        if self.db.execute(
                            "SELECT 1 FROM article_commands WHERE path=? AND command=?",
                            (row["path"], identity),
                        ).fetchone()
                    ),
                    "",
                )
                if command:
                    source = SourceLocation(
                        DocumentRef(self.index.library_id, row["path"]),
                        row["content_hash"],
                        fragment=command,
                    ).to_dict()
                else:
                    stored = self.db.execute(
                        "SELECT * FROM documents WHERE path=?", (row["path"],)
                    ).fetchone()
                    document = self.index.document_from_row(stored)
                    if document.blocks:
                        block = next(
                            (block for block in document.blocks if block.kind == "h1"),
                            document.blocks[0],
                        )
                        source = source_for_block(self.index.library_id, document, block).to_dict()
            reason = [
                "标题精确匹配",
                "命令标识对应教程",
                "文章标题匹配",
                "原手册关键词或命令匹配",
                "任务摘要匹配",
                "正文内容匹配",
            ][row["tier"]]
            hits.append(
                SearchHit(
                    row["path"],
                    row["title"],
                    row["module"],
                    row["summary"] or row["snippet"],
                    row["tier"] * 100000 + row["rank"],
                    DocumentRef(self.index.library_id, row["path"]).document_id,
                    source,
                    list(source.get("heading_path", ())),
                    reason,
                    signals={
                        "rank_tier": row["tier"],
                        "cross_section": int(
                            row["tier"] >= 4
                            and row["path"] not in strict_sources
                            and row["path"] in sources
                        ),
                    },
                    placement=position,
                    page_role=role,
                    title_zh=row["title_zh"],
                    related=self.related(row["path"]) if role == "task" else [],
                )
            )
        return hits

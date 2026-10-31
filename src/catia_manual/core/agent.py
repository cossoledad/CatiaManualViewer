"""Bounded, source-backed agent queries. No Qt, network, inference or database writes."""

import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict

from catia_manual.core.catalog import ManualCatalog
from catia_manual.core.library_contract import SourceLocation
from catia_manual.core.parser import ManualParser
from catia_manual.core.readonly_index import ReadOnlyIndex
from catia_manual.core.search import SearchService
from catia_manual.core.source import resolve_source, source_for_block
from catia_manual.core.structure import StructureIndex
from catia_manual.core.terminology import chinese_label


class AgentError(ValueError):
    def __init__(self, code, message, *, retryable=False):
        super().__init__(message)
        self.code, self.retryable = code, retryable

    def to_dict(self):
        return {"code": self.code, "message": str(self), "retryable": self.retryable}


def integer(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        raise AgentError("INVALID_ARGUMENT", f"{name} 须为 {low}–{high} 的整数")
    return value


class AgentKnowledge:
    def __init__(self, settings):
        self.root, self.database = settings.root, settings.database
        self.parser = ManualParser(self.root)
        self.signing_key = secrets.token_bytes(32)
        self.instance_id = secrets.token_hex(12)
        try:
            with ReadOnlyIndex(self.database, self.root) as index:
                self.library_id = index.library_id
                self.catalog_context = index.meta("structure_context")
        except (sqlite3.Error, ValueError, OSError) as exc:
            raise AgentError("INDEX_NOT_READY", "请先在查看器中完成文档库索引") from exc
        self.catalog = ManualCatalog(self.root)
        self.catalog_lock = threading.Lock()

    def revision(self, index):
        values = [
            index.meta(k)
            for k in (
                "manifest_hash",
                "parser_version",
                "chunk_version",
                "structure_context",
                "structure_terms_revision",
            )
        ]
        aliases = list(
            map(
                tuple,
                index.connection.execute(
                    "SELECT alias,english,module FROM term_aliases ORDER BY module,alias"
                ),
            )
        )
        return hashlib.sha256(
            json.dumps([values, aliases], ensure_ascii=False).encode()
        ).hexdigest()[:24]

    @contextmanager
    def reader(self, *, ready=True):
        try:
            with ReadOnlyIndex(self.database, self.root) as index:
                if index.library_id != self.library_id:
                    raise AgentError(
                        "LIBRARY_CHANGED", "文档库已变化，请在查看器中重新启动 Agent 服务"
                    )
                if ready and (
                    index.meta("manifest_verified") != "1"
                    or index.meta("structure_version") == "building"
                ):
                    raise AgentError(
                        "INDEX_NOT_READY",
                        "索引正在更新或尚未完成，请在查看器中完成更新后重试",
                        retryable=True,
                    )
                if index.meta("structure_context") != self.catalog_context:
                    with self.catalog_lock:
                        self.catalog = ManualCatalog(self.root)
                        self.catalog_context = index.meta("structure_context")
                yield index, self.revision(index)
        except AgentError:
            raise
        except sqlite3.OperationalError as exc:
            raise AgentError(
                "INDEX_UNAVAILABLE", "索引暂不可用，请稍后重试", retryable=True
            ) from exc
        except (sqlite3.Error, OSError, ValueError) as exc:
            raise AgentError(
                "INDEX_UNAVAILABLE", "文档库不可用，请在查看器中检查挂载和索引"
            ) from exc

    def pack(self, revision, kind, **value):
        payload = json.dumps(
            {"v": 1, "l": self.library_id, "r": revision, "k": kind, **value},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
        encoded = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
        signature = hmac.new(self.signing_key, encoded.encode(), hashlib.sha256).hexdigest()
        return encoded + "." + signature

    def unpack(self, value, revision):
        try:
            if not isinstance(value, str) or len(value) > 8192:
                raise ValueError()
            encoded, signature = value.rsplit(".", 1)
            expected = hmac.new(self.signing_key, encoded.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError()
            result = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            if result.get("v") != 1 or result.get("l") != self.library_id:
                raise ValueError()
        except (ValueError, TypeError, KeyError, UnicodeError) as exc:
            raise AgentError(
                "INVALID_REFERENCE", "资料标识无效或属于上一次服务，请重新搜索或解析来源"
            ) from exc
        if result.get("r") != revision:
            raise AgentError("STALE_REFERENCE", "索引或术语已更新，请重新搜索或解析来源")
        return result

    def describe_library(self):
        with self.reader(ready=False) as (index, revision):
            return {
                "library_id": self.library_id,
                "revision": revision,
                "label": index.meta("label"),
                "release": index.meta("release") or None,
                "language": index.meta("language", "en"),
                "index_ready": index.meta("manifest_verified") == "1"
                and index.meta("structure_version") != "building",
                "structure_ready": StructureIndex(index).ready(),
                "documents": index.connection.execute("SELECT count(*) FROM documents").fetchone()[
                    0
                ],
                "categories": [
                    {"id": c.id, "title": c.title, "title_zh": c.label, "manuals": len(c.manuals)}
                    for c in self.catalog.categories
                ],
                "capabilities": {
                    "search": True,
                    "fetch": True,
                    "browse": True,
                    "resolve_source": True,
                    "images": False,
                    "relations": False,
                    "readonly": True,
                    "online_translation": False,
                    "inference": False,
                },
                "reference_lifetime": "本次服务与索引修订；持久引用请保存 SourceLocation",
            }

    def document_ref(self, index, revision, path):
        row = index.connection.execute(
            "SELECT content_hash FROM documents WHERE path=?", (path,)
        ).fetchone()
        return self.pack(revision, "document", p=path, h=row[0]) if row else None

    def search(self, query, manual=None, category=None, limit=5):
        if not isinstance(query, str) or not query.strip() or len(query) > 500:
            raise AgentError("INVALID_ARGUMENT", "查询须为 1–500 字符")
        integer(limit, 1, 20, "limit")
        if manual and category:
            raise AgentError("INVALID_ARGUMENT", "manual 与 category 只能指定一个")
        with self.reader() as (index, revision):
            modules = None
            if manual:
                if manual not in self.catalog.manuals:
                    raise AgentError("UNKNOWN_SCOPE", "未知手册，请先浏览文档库")
                modules = self.catalog.scope([manual])
            if category:
                try:
                    group = self.catalog.category(category)
                    modules = self.catalog.scope([m.id for m in group.manuals])
                except ValueError as exc:
                    raise AgentError("UNKNOWN_SCOPE", "未知分类，请先浏览文档库") from exc
            if modules is not None and not StructureIndex(index).ready():
                raise AgentError(
                    "INDEX_NOT_READY", "功能范围尚未整理，请先在查看器中更新索引", retryable=True
                )
            # A dedicated query policy prevents configured API providers and models
            # from being loaded by agent requests, even for unknown Chinese words.
            response = SearchService(index, {"provider": "glossary"}, {"mode": "keyword"}).search(
                query, modules=modules, limit=limit + 1
            )
            if response.plan.unknown:
                response.plan.notes = [
                    "存在未覆盖的中文术语，可改用英文关键词或在查看器中添加别名；"
                    "Agent 服务不会在线翻译。"
                ]
            hits = []
            for hit in response.hits[:limit]:
                source = hit.source or {}
                hits.append(
                    {
                        "id": self.pack(
                            revision,
                            "source" if source.get("block_id") else "document",
                            p=hit.path,
                            h=source.get("content_hash", ""),
                            b=source.get("block_id", ""),
                        ),
                        "article_id": self.document_ref(index, revision, hit.path),
                        "document_id": hit.document_id,
                        "title": hit.title,
                        "title_zh": hit.title_zh,
                        "heading_path": hit.heading_path,
                        "snippet": hit.snippet[:600],
                        "source": source,
                        "reason": hit.reason,
                        "role": hit.page_role,
                        "placement": hit.placement,
                    }
                )
            return {
                "library_id": self.library_id,
                "revision": revision,
                "query": query,
                "query_plan": asdict(response.plan),
                "results": hits,
                "has_more": len(response.hits) > limit,
                "mode": "keyword",
            }

    def document(self, index, ref):
        if ref.get("k") not in {"source", "section", "document"}:
            raise AgentError("INVALID_REFERENCE", "该标识不指向文章或小节")
        row = index.connection.execute(
            "SELECT content_hash FROM documents WHERE path=?", (ref.get("p"),)
        ).fetchone()
        if not row:
            raise AgentError("SOURCE_NOT_FOUND", "文章已不在当前索引中")
        if row[0] != ref.get("h"):
            raise AgentError("STALE_REFERENCE", "文章索引已更新，请重新搜索")
        try:
            if self.parser.source(ref["p"]).stat().st_size > 8 * 1024 * 1024:
                raise AgentError("SOURCE_TOO_LARGE", "此页面过大，请在查看器中阅读")
            document = self.parser.parse(ref["p"])
        except (OSError, ValueError) as exc:
            if isinstance(exc, AgentError):
                raise
            raise AgentError("SOURCE_NOT_FOUND", "原文文件不可读或已不存在") from exc
        if document.content_hash != row[0]:
            raise AgentError("STALE_SOURCE", "原文已变化，请先更新索引；未返回旧索引对应的新内容")
        return document

    def fetch(self, id, cursor=None, max_chars=8000):
        integer(max_chars, 1000, 24000, "max_chars")
        with self.reader() as (index, revision):
            ref = self.unpack(id, revision)
            document = self.document(index, ref)
            start, end, offset = 0, len(document.blocks), 0
            if ref["k"] != "document":
                target = next(
                    (i for i, b in enumerate(document.blocks) if b.id == ref.get("b")), None
                )
                if target is None:
                    raise AgentError("SOURCE_NOT_FOUND", "命中段落已不存在，请重新搜索")
                start = next(
                    (
                        i
                        for i in range(target, -1, -1)
                        if re.fullmatch(r"h[1-6]", document.blocks[i].kind)
                    ),
                    0,
                )
                end = next(
                    (
                        i
                        for i in range(start + 1, len(document.blocks))
                        if re.fullmatch(r"h[1-6]", document.blocks[i].kind)
                    ),
                    len(document.blocks),
                )
            first = start
            if cursor:
                page = self.unpack(cursor, revision)
                if (
                    page.get("k") != "cursor"
                    or page.get("target") != hashlib.sha256(id.encode()).hexdigest()
                ):
                    raise AgentError("INVALID_REFERENCE", "续读标识不属于本次文章")
                start = integer(page.get("i"), first, max(first, end - 1), "cursor")
                offset = integer(page.get("off", 0), 0, len(document.blocks[start].text), "offset")
            blocks, remaining = [], max_chars
            while start < end and remaining and len(blocks) < 40:
                block = document.blocks[start]
                text = block.text[offset:]
                if blocks and len(text) > remaining:
                    break
                value = text[:remaining]
                source = source_for_block(self.library_id, document, block, ordinal=start).to_dict()
                blocks.append(
                    {
                        "kind": block.kind,
                        "text": value,
                        "text_start": offset,
                        "text_end": offset + len(value),
                        "continued": bool(offset or len(value) < len(text)),
                        "source": source,
                    }
                )
                remaining -= len(value)
                offset += len(value)
                if offset == len(block.text):
                    start, offset = start + 1, 0
                else:
                    break
            more = start < end
            document_links = [
                item for item in document.links if item.kind == "document" and item.target
            ][:20]
            return {
                "library_id": self.library_id,
                "revision": revision,
                "title": document.title,
                "path": document.path,
                "content_hash": document.content_hash,
                "language": "en",
                "blocks": blocks,
                "text": "\n\n".join(b["text"] for b in blocks),
                "has_more": more,
                "next_cursor": self.pack(
                    revision,
                    "cursor",
                    target=hashlib.sha256(id.encode()).hexdigest(),
                    i=start,
                    off=offset,
                )
                if more
                else None,
                "scope": "article" if ref["k"] == "document" else "section",
                "article_id": self.document_ref(index, revision, document.path),
                "links": [
                    {
                        "label": link.label[:240],
                        "fragment": link.fragment,
                        "id": self.document_ref(index, revision, link.target),
                    }
                    for link in document_links
                ],
            }

    def browse(self, id=None, offset=0, limit=20):
        integer(offset, 0, 100000, "offset")
        integer(limit, 1, 50, "limit")
        with self.reader() as (index, revision):
            ref = self.unpack(id, revision) if id else {"k": "root"}
            kind, items = ref["k"], []
            if kind == "root":
                items = [
                    {
                        "id": self.pack(revision, "category", category=c.id),
                        "kind": "category",
                        "title": c.title,
                        "title_zh": c.label,
                    }
                    for c in self.catalog.categories
                ]
            elif kind == "category":
                group = self.catalog.category(ref["category"])
                items = [
                    {
                        "id": self.pack(revision, "manual", manual=m.id),
                        "manual": m.id,
                        "kind": "manual",
                        "title": m.title,
                        "title_zh": m.label,
                        "article_id": self.document_ref(index, revision, m.entry),
                    }
                    for m in group.manuals
                ]
            elif kind in {"manual", "placement"}:
                if not StructureIndex(index).ready():
                    raise AgentError(
                        "INDEX_NOT_READY", "功能目录尚未整理，请先更新索引", retryable=True
                    )
                rows = index.connection.execute(
                    "SELECT * FROM manual_placements WHERE manual=? AND parent=? "
                    "ORDER BY position LIMIT ? OFFSET ?",
                    (ref["manual"], ref.get("placement", ""), limit + 1, offset),
                ).fetchall()
                for row in rows:
                    items.append(
                        {
                            "id": self.pack(
                                revision, "placement", manual=row["manual"], placement=row["id"]
                            ),
                            "kind": "placement",
                            "title": row["title"],
                            "title_zh": chinese_label(row["title"]),
                            "trail": json.loads(row["trail"]),
                            "article_id": self.document_ref(index, revision, row["path"]),
                            "fragment": row["fragment"],
                        }
                    )
                return {
                    "library_id": self.library_id,
                    "revision": revision,
                    "items": items[:limit],
                    "has_more": len(items) > limit,
                    "next_offset": offset + limit if len(items) > limit else None,
                }
            elif kind in {"document", "source", "section"}:
                document = self.document(index, ref)
                items = [
                    {
                        "id": self.pack(
                            revision, "section", p=document.path, h=document.content_hash, b=b.id
                        ),
                        "kind": "section",
                        "title": b.text,
                        "level": int(b.kind[1]),
                        "source": source_for_block(
                            self.library_id, document, b, ordinal=i
                        ).to_dict(),
                    }
                    for i, b in enumerate(document.blocks)
                    if re.fullmatch(r"h[1-6]", b.kind)
                ]
            else:
                raise AgentError("INVALID_REFERENCE", "该标识不能用于目录浏览")
            more = offset + limit < len(items)
            return {
                "library_id": self.library_id,
                "revision": revision,
                "items": items[offset : offset + limit],
                "has_more": more,
                "next_offset": offset + limit if more else None,
            }

    def resolve_source(self, source):
        try:
            location = SourceLocation.from_dict(source)
        except (TypeError, KeyError, ValueError) as exc:
            raise AgentError("INVALID_ARGUMENT", "来源格式无效") from exc
        if location.document.library_id != self.library_id:
            raise AgentError("LIBRARY_CHANGED", "来源属于其他文档库")
        with self.reader() as (index, revision):
            if not index.connection.execute(
                "SELECT 1 FROM documents WHERE path=?", (location.document.path,)
            ).fetchone():
                raise AgentError("SOURCE_NOT_FOUND", "该来源不在当前索引中")
            try:
                if self.parser.source(location.document.path).stat().st_size > 8 * 1024 * 1024:
                    raise AgentError("SOURCE_TOO_LARGE", "此页面过大，请在查看器中阅读")
                result = resolve_source(self.parser, location, self.library_id)
            except (OSError, ValueError) as exc:
                if isinstance(exc, AgentError):
                    raise
                raise AgentError("SOURCE_NOT_FOUND", "原文文件不可读") from exc
            value = result.to_dict()
            value.update(library_id=self.library_id, revision=revision)
            if result.source:
                current = result.source
                row = index.connection.execute(
                    "SELECT content_hash FROM documents WHERE path=?", (current.document.path,)
                ).fetchone()
                if row[0] != current.content_hash:
                    raise AgentError("STALE_SOURCE", "原文已变化，请先更新索引再重新读取")
                value["id"] = self.pack(
                    revision,
                    "source" if current.block_id else "document",
                    p=current.document.path,
                    h=current.content_hash,
                    b=current.block_id,
                )
            return value

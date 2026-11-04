"""Persistent, machine-independent article links. No GUI or network dependencies."""

import re
import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from catia_manual.core.library_contract import DocumentRef, SourceLocation

SCHEME = "catia-manual"
MAX_LINK = 4096


@dataclass(frozen=True)
class PageLink:
    library_id: str
    path: str
    content_hash: str
    fragment: str = ""
    block_id: str = ""

    def __post_init__(self):
        ref = DocumentRef(self.library_id, self.path)
        object.__setattr__(self, "library_id", ref.library_id)
        object.__setattr__(self, "path", ref.path)
        SourceLocation(ref, self.content_hash, fragment=self.fragment, block_id=self.block_id)
        if PurePosixPath(ref.path).suffix.lower() not in {".htm", ".html"}:
            raise ValueError("分享链接只支持手册页面")
        if any(ord(c) < 32 for c in self.fragment + self.block_id):
            raise ValueError("分享位置包含无效字符")
        if len(self.fragment) > 512 or not re.fullmatch(r"[a-f0-9]{20}|", self.block_id):
            raise ValueError("分享位置无效")

    def to_uri(self):
        query = {"library": self.library_id, "path": self.path, "sha256": self.content_hash}
        if self.fragment:
            query["anchor"] = self.fragment
        if self.block_id:
            query["block"] = self.block_id
        uri = SCHEME + "://page/v1?" + urlencode(query)
        if len(uri) > MAX_LINK:
            raise ValueError("分享链接过长")
        return uri

    @classmethod
    def parse(cls, value):
        if not isinstance(value, str) or len(value) > MAX_LINK * 3:
            raise ValueError("分享链接无效或过长")
        if any(ord(c) < 32 for c in value):
            raise ValueError("分享链接包含无效字符")
        url = urlsplit(value)
        if url.scheme in {"http", "https"} and url.path == "/share":
            query = parse_qs(url.query, strict_parsing=True, max_num_fields=1)
            if set(query) != {"uri"} or len(query["uri"]) != 1:
                raise ValueError("分享链接无效")
            value = query["uri"][0]
            url = urlsplit(value)
        if len(value) > MAX_LINK:
            raise ValueError("分享链接过长")
        if (url.scheme, url.netloc, url.path) != (SCHEME, "page", "/v1") or url.fragment:
            raise ValueError("不支持的分享链接")
        query = parse_qs(url.query, strict_parsing=True, max_num_fields=5)
        required = {"library", "path", "sha256"}
        if not required <= query.keys() or query.keys() - (required | {"anchor", "block"}):
            raise ValueError("分享链接字段无效")
        if any(len(items) != 1 for items in query.values()):
            raise ValueError("分享链接字段重复")
        return cls(
            str(uuid.UUID(query["library"][0])),
            query["path"][0],
            query["sha256"][0],
            query.get("anchor", [""])[0],
            query.get("block", [""])[0],
        )


def link_for_source(source):
    source = SourceLocation.from_dict(source)
    return PageLink(
        source.document.library_id,
        source.document.path,
        source.content_hash,
        source.fragment,
        source.block_id,
    ).to_uri()


def browser_link(base, uri):
    link = PageLink.parse(uri)
    fragment = "block-" + link.block_id if link.block_id else link.fragment
    return (
        base.rstrip("/")
        + "/share?"
        + urlencode({"uri": uri})
        + ("#" + quote(fragment, safe="") if fragment else "")
    )


def resolve_page_link(index, parser, value):
    """Cross-library copies must match both relative path and original bytes exactly."""
    link = PageLink.parse(value)
    row = index.connection.execute(
        "SELECT content_hash FROM documents WHERE path=?", (link.path,)
    ).fetchone()
    if not row:
        raise ValueError("分享页面不在当前文档库中")
    if row[0] != link.content_hash:
        raise ValueError("分享页面与当前手册版本不一致")
    if parser.source(link.path).stat().st_size > 8 * 1024 * 1024:
        raise ValueError("分享页面超过 8 MiB，请在查看器中阅读原文")
    document = parser.parse(link.path)
    if document.content_hash != link.content_hash:
        raise ValueError("原文已变化，请更新索引后重新分享")
    if link.fragment and link.fragment not in document.anchors:
        raise ValueError("分享锚点已不存在")
    if link.block_id and not any(b.id == link.block_id for b in document.blocks):
        raise ValueError("分享段落已不存在")
    source = SourceLocation(
        DocumentRef(index.library_id, document.path),
        document.content_hash,
        fragment=link.fragment,
        block_id=link.block_id,
    )
    return document, source, link.library_id == index.library_id

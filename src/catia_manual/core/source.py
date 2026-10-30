"""Source blocks and evidence-preserving relocation, independent of rendering and Qt."""

import hashlib
from dataclasses import dataclass

from bs4 import Comment, NavigableString, Tag

from catia_manual.core.library_contract import DocumentRef, SourceLocation
from catia_manual.core.models import Block

BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th", "pre", "dt", "dd"}
EXCLUDED = {"head", "script", "style", "noscript", "object", "applet", "form", "button", "iframe"}


def clean(value):
    return " ".join(value.split())


def extract_blocks(soup, relative, *, annotate=False):
    body = soup.body or soup
    owner, fragment, last_anchor = None, "", ""
    nodes, blocks, headings = [], [], []

    def flush():
        text = clean(" ".join(str(node) for node in nodes))
        if text:
            kind = owner.name if owner else "text"
            if kind in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                level = int(kind[1])
                while headings and headings[-1][0] >= level:
                    headings.pop()
                headings.append((level, text))
            identity = hashlib.sha256(f"{relative}:{len(blocks)}:{text}".encode()).hexdigest()[:20]
            blocks.append(Block(identity, kind, text, fragment, tuple(h[1] for h in headings)))
            if annotate:
                for node in nodes:
                    marker = soup.new_tag("span", attrs={"data-cmv-source-block": identity})
                    node.wrap(marker)
        nodes.clear()

    # Snapshot the tree before adding markers so source and rendered IDs are identical.
    for node in list(body.descendants):
        if isinstance(node, Tag):
            if node.name in EXCLUDED or any(p.name in EXCLUDED for p in node.parents):
                continue
            if node.get("id") or (node.name == "a" and node.get("name")):
                last_anchor = str(node.get("id") or node.get("name"))
            continue
        if (
            not isinstance(node, NavigableString)
            or isinstance(node, Comment)
            or not clean(str(node))
        ):
            continue
        if any(parent.name in EXCLUDED for parent in node.parents):
            continue
        parent = node.parent
        while parent is not None and parent is not body and parent.name not in BLOCK_TAGS:
            parent = parent.parent
        if parent is not owner:
            flush()
            owner = parent
            ancestor = next((p for p in node.parents if p.get("id")), None)
            fragment = str(ancestor["id"]) if ancestor else last_anchor
        nodes.append(node)
    flush()
    return blocks


def source_for_block(library_id, document, block, offset=0, *, ordinal=None):
    if ordinal is None:
        ordinal = next(i for i, item in enumerate(document.blocks) if item.id == block.id)
    return SourceLocation(
        DocumentRef(library_id, document.path),
        document.content_hash,
        fragment=block.fragment,
        block_id=block.id,
        heading_path=block.heading_path,
        text_quote=block.text[:180],
        text_prefix=document.blocks[ordinal - 1].text[-64:] if ordinal else "",
        text_suffix=block.text[180:244],
        offset_css_px=offset,
    )


@dataclass(frozen=True)
class SourceResolution:
    status: str
    source: SourceLocation | None
    candidates: int = 0
    reason: str = ""

    def to_dict(self):
        return {
            "status": self.status,
            "source": self.source.to_dict() if self.source else None,
            "candidates": self.candidates,
            "reason": self.reason,
        }


def resolve_source(parser, location, library_id, *, prefer_fragment=False):
    if location.document.library_id != library_id:
        raise ValueError("来源引用属于另一文档库")
    try:
        document = parser.parse(location.document.path)
    except FileNotFoundError:
        return SourceResolution("missing", None, reason="原文文件已不存在")
    same = location.content_hash == document.content_hash
    if same and not prefer_fragment and location.block_id:
        block = next((b for b in document.blocks if b.id == location.block_id), None)
        if block:
            return SourceResolution(
                "exact", source_for_block(library_id, document, block, location.offset_css_px), 1
            )
    if location.fragment and location.fragment in document.anchors:
        block = next((b for b in document.blocks if b.fragment == location.fragment), None)
        source = (
            source_for_block(library_id, document, block, location.offset_css_px)
            if block
            else SourceLocation(
                location.document, document.content_hash, fragment=location.fragment
            )
        )
        return SourceResolution("anchor" if same else "relocated-anchor", source, 1)
    if not location.block_id and not location.text_quote and not location.fragment:
        return SourceResolution(
            "document", SourceLocation(location.document, document.content_hash), 1
        )
    quote = clean(location.text_quote)
    candidates = [b for b in document.blocks if quote and quote in b.text]
    if len(candidates) > 1 and location.heading_path:
        narrowed = [b for b in candidates if b.heading_path == location.heading_path]
        if narrowed:
            candidates = narrowed
    if len(candidates) > 1:
        narrowed = []
        for block in candidates:
            ordinal = document.blocks.index(block)
            prefix = document.blocks[ordinal - 1].text if ordinal else ""
            tail = block.text.split(quote, 1)[1]
            if (not location.text_prefix or prefix.endswith(location.text_prefix)) and (
                not location.text_suffix or tail.startswith(location.text_suffix)
            ):
                narrowed.append(block)
        if narrowed:
            candidates = narrowed
    if len(candidates) == 1:
        return SourceResolution(
            "relocated-quote",
            source_for_block(library_id, document, candidates[0], location.offset_css_px),
            1,
        )
    return SourceResolution(
        "ambiguous" if candidates else "missing",
        None,
        len(candidates),
        "原文已修订，来源无法唯一定位" if candidates else "未找到原来源内容",
    )

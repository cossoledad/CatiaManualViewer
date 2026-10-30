import hashlib
import html
import re
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag, UnicodeDammit

from catia_manual.core.legacy import LegacyLinks
from catia_manual.core.models import Block, Document, TocEntry
from catia_manual.core.paths import resolve_link, within_root
from catia_manual.core.reading import READING_CSS, ReadingPage, annotate
from catia_manual.core.resources import IMAGE_EXTENSIONS

BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "td", "th", "pre", "dt", "dd"}
PARSER_VERSION = "2"
STATIC_CALL = re.compile(r"(?:GoToBody|openwin|window\.open)\s*\(\s*(['\"])(.*?)\1", re.I)


def clean_text(value: str) -> str:
    return " ".join(value.split())


class ManualParser:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def source(self, relative: str) -> Path:
        return within_root(self.root, self.root / relative)

    def soup(self, relative: str) -> tuple[BeautifulSoup, bytes, str]:
        raw = self.source(relative).read_bytes()
        try:
            text, encoding = raw.decode("utf-8-sig"), "utf-8"
        except UnicodeDecodeError:
            decoded = UnicodeDammit(raw, is_html=True)
            text = decoded.unicode_markup or raw.decode("cp1252", errors="replace")
            encoding = decoded.original_encoding or "cp1252"
        soup = BeautifulSoup(text, "lxml")
        LegacyLinks(self.root, self.source(relative)).recover(soup)
        return soup, raw, encoding

    def parse(self, relative: str) -> Document:
        source = self.source(relative)
        relative = source.relative_to(self.root).as_posix()
        soup, raw, encoding = self.soup(relative)
        title_tag = soup.find("h1") or soup.title
        title = clean_text(title_tag.get_text(" ", strip=True)) if title_tag else source.stem
        title = title or source.stem
        links = []
        for tag in soup.find_all(["a", "frame", "iframe"]):
            href = tag.get("href") or tag.get("src")
            match = STATIC_CALL.search(str(tag.get("onclick", "")))
            if match and (not href or href == "#" or href.lower().startswith("javascript:")):
                href = match.group(2)
            if href:
                label = clean_text(tag.get_text(" ", strip=True)) or str(tag.get("title", ""))
                links.append(resolve_link(self.root, source, str(href), label))
        frames = bool(soup.find(["frame", "frameset"]))
        for tag in soup.find_all(["script", "style", "head", "noscript", "object", "applet"]):
            tag.decompose()
        body = soup.body or soup
        blocks: list[Block] = []
        owner = None
        parts: list[str] = []

        def flush() -> None:
            text = clean_text(" ".join(parts))
            if text:
                digest = hashlib.sha256(f"{relative}:{len(blocks)}:{text}".encode()).hexdigest()[
                    :20
                ]
                blocks.append(Block(digest, owner.name if owner else "text", text))
            parts.clear()

        for node in body.descendants:
            if not isinstance(node, NavigableString) or node.__class__.__name__ == "Comment":
                continue
            if not clean_text(str(node)):
                continue
            parent = node.parent
            while parent is not None and parent is not body and parent.name not in BLOCK_TAGS:
                parent = parent.parent
            if parent is not owner:
                flush()
                owner = parent
            parts.append(str(node))
        flush()
        kind = (
            "frameset"
            if frames
            else "navigation"
            if (
                "_navigation" in source.parts or source.name.lower().endswith(("sm.htm", "toc.htm"))
            )
            else "article"
        )
        module = relative.split("/")[1] if relative.startswith("online/") else "root"
        return Document(
            path=relative,
            title=title,
            module=module,
            kind=kind,
            encoding=encoding,
            content_hash=hashlib.sha256(raw).hexdigest(),
            text="\n".join(block.text for block in blocks),
            blocks=blocks,
            links=links,
            headings=[block.text for block in blocks if block.kind.startswith("h")],
        )

    def render(self, relative: str) -> str:
        return self.render_page(relative).html

    def render_page(self, relative: str) -> ReadingPage:
        """Keep original tables/images/anchors; remove executable legacy navigation."""
        source = self.source(relative)
        soup, raw, _ = self.soup(relative)
        for tag in soup.find_all(
            [
                "script",
                "iframe",
                "frame",
                "frameset",
                "object",
                "embed",
                "applet",
                "form",
                "input",
                "button",
                "meta",
                "base",
                "link",
                "style",
                "noscript",
            ]
        ):
            tag.decompose()
        for tag in soup.find_all(True):
            match = STATIC_CALL.search(str(tag.get("onclick", "")))
            href = str(tag.get("href", ""))
            if match and (not href or href == "#" or href.lower().startswith("javascript:")):
                tag["href"] = match.group(2)
            for attr in list(tag.attrs):
                if attr.lower().startswith(("on", "data-cmv-")) or attr.lower() in {
                    "srcdoc",
                    "target",
                }:
                    del tag[attr]
            for attr in ("href", "src", "background", "poster"):
                if attr not in tag.attrs:
                    continue
                link = resolve_link(self.root, source, str(tag[attr]))
                if link.target and link.kind != "blocked":
                    uri = (self.root / link.target).as_uri()
                    if link.fragment:
                        from urllib.parse import quote

                        uri += "#" + quote(link.fragment)
                    tag[attr] = uri
                    if attr == "href" and tag.name == "a":
                        tag["data-cmv-kind"] = (
                            "image"
                            if Path(link.target).suffix.lower() in IMAGE_EXTENSIONS
                            else link.kind
                        )
                    if attr == "src" and tag.name == "img":
                        tag["data-cmv-image"] = link.target
                elif attr == "href" and tag.name == "a" and link.kind == "external":
                    tag[attr] = link.href
                else:
                    del tag[attr]
            # Inline style is unnecessary for functionality; prevents external CSS requests.
            tag.attrs.pop("style", None)
            if tag.name in {"table", "tr", "td", "th"}:
                tag.attrs.pop("height", None)
        content_hash = hashlib.sha256(raw).hexdigest()
        page_key, units = annotate(soup, relative, content_hash)
        content = soup.body.decode_contents() if soup.body else str(soup)
        title = html.escape(source.stem)
        rendered = f"""<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font:16px/1.65 "Segoe UI","Microsoft YaHei",sans-serif;color:#213047;
background:#fff;margin:32px auto;padding:0 32px;max-width:1120px}}
h1,h2,h3{{color:#173f56;line-height:1.35}}h1{{font-size:30px;letter-spacing:-.4px}}
a{{color:#167c85}}img{{max-width:100%;height:auto}}
img[src*="/common/atarget"],img[src*="/common/aprereq"],img[src*="/common/ascenari"]{{min-width:28px}}
font{{font-family:inherit;color:inherit}}
table{{max-width:100%}}td{{vertical-align:top}}kbd,code{{background:#eef3f8;padding:2px 4px}}
{READING_CSS}
</style></head><body data-page-key="{page_key}" data-mode="original">{content}</body></html>"""
        return ReadingPage(relative, content_hash, page_key, rendered, units)

    def toc(self, relative: str) -> list[TocEntry]:
        soup, _, _ = self.soup(relative)
        source = self.source(relative)
        entries = []
        for tag in soup.select("p.sm1, p.sm2, p.sm3, p.sm4"):
            anchor = tag.find("a", href=True)
            if not isinstance(anchor, Tag):
                continue
            link = resolve_link(self.root, source, str(anchor["href"]))
            if link.kind != "document" or not link.target:
                continue
            margin = re.search(r"margin-left\s*:\s*(\d+)", str(tag.get("style", "")))
            depth = int(margin.group(1)) // 25 if margin else 0
            entries.append(
                TocEntry(
                    clean_text(anchor.get_text(" ", strip=True)), link.target, depth, link.fragment
                )
            )
        return entries

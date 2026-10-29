"""Qt-free reading representation shared by translation and the HTML renderer."""

import hashlib
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

RENDERER_VERSION = "reading-1"
BOUNDARIES = {
    "div",
    "section",
    "article",
    "header",
    "footer",
    "main",
    "nav",
    "table",
    "tbody",
    "thead",
    "tr",
    "td",
    "th",
    "p",
    "li",
    "ul",
    "ol",
    "dl",
    "dt",
    "dd",
    "blockquote",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "pre",
    "figure",
    "figcaption",
    "hr",
}
PROTECTED = {"pre", "code", "kbd", "samp", "script", "style", "textarea"}


@dataclass(frozen=True)
class ReadingUnit:
    id: str
    text: str


@dataclass(frozen=True)
class ReadingPage:
    path: str
    content_hash: str
    key: str
    html: str
    units: tuple[ReadingUnit, ...]


def annotate(soup: BeautifulSoup, relative: str, content_hash: str):
    """Group inline runs without moving a node across a block/table/anchor boundary."""
    units = []
    body = soup.body or soup
    page_key = hashlib.sha256(f"{RENDERER_VERSION}:{relative}:{content_hash}".encode()).hexdigest()[
        :24
    ]

    def protected(node):
        return any(parent.name in PROTECTED for parent in node.parents)

    def walk(container):
        if container.name in PROTECTED:
            return
        run = []

        def flush():
            if not run:
                return
            texts = []
            for node in run:
                candidates = [node] if isinstance(node, NavigableString) else node.descendants
                texts.extend(
                    str(n)
                    for n in candidates
                    if isinstance(n, NavigableString)
                    and not isinstance(n, Comment)
                    and not protected(n)
                )
            text = " ".join(" ".join(texts).split())
            if not re.search(r"[a-zA-Z]{2}", text):
                run.clear()
                return
            identity = hashlib.sha256(f"{page_key}:{len(units)}:{text}".encode()).hexdigest()[:20]
            unit = soup.new_tag("span", attrs={"class": "cmv-unit", "data-unit": identity})
            source = soup.new_tag("span", attrs={"class": "cmv-source"})
            run[0].insert_before(unit)
            unit.append(source)
            for node in run:
                source.append(node.extract())
            # Hide only ordinary source text in Chinese mode. Links, code and images stay live.
            for node in list(source.descendants):
                if isinstance(node, NavigableString) and not isinstance(node, Comment):
                    if protected(node) or not str(node).strip():
                        continue
                    span = soup.new_tag("span", attrs={"class": "cmv-original-text"})
                    node.wrap(span)
            translated = soup.new_tag("span", attrs={"class": "cmv-translated", "lang": "zh-CN"})
            unit.append(translated)
            retry = soup.new_tag("a", attrs={"class": "cmv-retry", "href": "cmv-retry:" + identity})
            retry.string = "重试此段"
            unit.append(retry)
            units.append(ReadingUnit(identity, text))
            run.clear()

        for child in list(container.children):
            if isinstance(child, Tag) and (child.name in BOUNDARIES or child.find(BOUNDARIES)):
                flush()
                walk(child)
            else:
                run.append(child)
        flush()

    walk(body)
    return page_key, tuple(units)


READING_CSS = """
html {overflow-anchor:none;}
.cmv-translated,.cmv-retry {display:none;}
body[data-mode="bilingual"] .cmv-unit[data-state="done"]>.cmv-translated,
body[data-mode="chinese"] .cmv-unit[data-state="done"]>.cmv-translated {
 display:block;white-space:pre-wrap;color:#176369;margin:5px 0 9px;
 font-family:"Microsoft YaHei UI",sans-serif;font-size:0.96em;line-height:1.7;}
body[data-mode="chinese"] .cmv-unit[data-state="done"]>.cmv-translated {color:inherit;}
body[data-mode="chinese"] .cmv-unit[data-state="done"]>.cmv-source .cmv-original-text {
 display:none;}
body[data-mode="chinese"] .cmv-source a .cmv-original-text {display:inline!important;}
body:not([data-mode="original"]) .cmv-unit[data-state="error"]>.cmv-retry {
 display:inline-block;font:12px "Microsoft YaHei UI",sans-serif;color:#a76738;margin-left:8px;}
"""

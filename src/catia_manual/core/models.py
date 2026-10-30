from dataclasses import dataclass, field


@dataclass(frozen=True)
class Link:
    label: str
    href: str
    kind: str
    target: str | None = None
    fragment: str = ""
    exists: bool = False


@dataclass(frozen=True)
class Block:
    id: str
    kind: str
    text: str
    fragment: str = ""
    heading_path: tuple[str, ...] = ()


@dataclass
class Document:
    path: str
    title: str
    module: str
    kind: str
    encoding: str
    content_hash: str
    text: str
    blocks: list[Block] = field(default_factory=list)
    links: list[Link] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    anchors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SearchHit:
    path: str
    title: str
    module: str
    snippet: str
    score: float
    document_id: str = ""
    source: dict | None = None
    heading_path: list[str] = field(default_factory=list)
    reason: str = "正文关键词匹配"
    channels: list[str] = field(default_factory=lambda: ["keyword"])
    semantic_score: float | None = None
    signals: dict[str, int] = field(default_factory=dict)
    placement: dict | None = None
    page_role: str = ""
    title_zh: str = ""
    related: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class TocEntry:
    title: str
    path: str
    depth: int
    fragment: str = ""

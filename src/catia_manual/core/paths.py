import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from catia_manual.core.models import Link

DOCUMENT_EXTENSIONS = {".htm", ".html"}
SAMPLE_EXTENSIONS = {
    ".catpart",
    ".catproduct",
    ".catdrawing",
    ".catanalysis",
    ".catprocess",
    ".catshape",
    ".catmaterial",
    ".catfct",
    ".model",
    ".cgr",
    ".3dxml",
    ".stp",
    ".step",
    ".igs",
    ".iges",
    ".stl",
    ".session",
    ".exp",
}


def within_root(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"路径超出文档根目录: {path}")
    return resolved


def resolve_link(root: Path, source: Path, href: str, label: str = "") -> Link:
    href = href.strip().replace("\\", "/")
    if re.match(r"^[a-zA-Z]:/", href):
        href = Path(href).as_uri()
    try:
        url = urlsplit(href)
    except ValueError:
        return Link(label, href, "blocked")
    if url.scheme in {"http", "https", "mailto"}:
        if url.hostname in {"dsdoc", "localhost", "127.0.0.1"} and "/online/" in url.path:
            href = "online/" + url.path.split("/online/", 1)[1]
            if url.fragment:
                href += "#" + url.fragment
            return resolve_link(root, root / "CATIA_P3_homepage.htm", href, label)
        return Link(label, href, "external")
    if url.scheme not in {"", "file"} or url.netloc not in {"", "localhost"}:
        return Link(label, href, "blocked")
    raw = unquote(url.path)
    build_time = re.search(r"(?:^|/)([A-Za-z0-9_-]+)\.doc/src/(.+)$", raw)
    if not url.scheme and build_time:
        module, tail = build_time.groups()
        candidates = sorted((root / "online").glob(module + "_*"))
        preferred = next(
            (path for path in candidates if path.name.lower() == module.lower() + "_c2"),
            candidates[0] if len(candidates) == 1 else None,
        )
        if preferred:
            raw = str(preferred / tail)
    if url.scheme == "file" and re.match(r"^/[a-zA-Z]:/", raw):
        raw = raw[1:]
    try:
        candidate = source if not raw else Path(raw)
        if not candidate.is_absolute():
            candidate = source.parent / candidate
        target = within_root(root, candidate)
    except (ValueError, OSError):
        return Link(label, href, "blocked")
    suffix = target.suffix.lower()
    kind = (
        "document"
        if suffix in DOCUMENT_EXTENSIONS
        else "sample"
        if suffix in SAMPLE_EXTENSIONS
        else "resource"
    )
    return Link(
        label,
        href,
        kind,
        target.relative_to(root.resolve()).as_posix(),
        unquote(url.fragment),
        target.is_file(),
    )

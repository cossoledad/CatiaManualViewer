"""Version 1 of the Qt-free library/source wire contract, persisted by the index."""

import hashlib
import math
import re
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

CONTRACT_VERSION = 1


def relative_path(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("来源路径必须是字符串")
    value = unicodedata.normalize("NFC", value.replace("\\", "/"))
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ":" in value
        or ".." in path.parts
        or any(ord(c) < 32 for c in value)
        or value.endswith("/")
        or str(path) == "."
    ):
        raise ValueError("来源路径须为库内相对文件路径")
    return path.as_posix()


@dataclass(frozen=True)
class LibraryIdentity:
    library_id: str
    label: str
    release: str
    language: str

    def __post_init__(self):
        object.__setattr__(self, "library_id", str(uuid.UUID(self.library_id)))


@dataclass(frozen=True)
class LibraryMount:
    library_id: str
    root: Path

    def __post_init__(self):
        object.__setattr__(self, "library_id", str(uuid.UUID(self.library_id)))
        root = Path(self.root)
        if not root.is_absolute():
            raise ValueError("挂载目录须为本机绝对路径")
        object.__setattr__(self, "root", root.resolve())


@dataclass(frozen=True)
class DocumentRef:
    library_id: str
    path: str

    def __post_init__(self):
        object.__setattr__(self, "library_id", str(uuid.UUID(self.library_id)))
        object.__setattr__(self, "path", relative_path(self.path))

    @property
    def document_id(self):
        return hashlib.sha256(f"{self.library_id}\n{self.path}".encode()).hexdigest()


@dataclass(frozen=True)
class SourceLocation:
    document: DocumentRef
    content_hash: str
    fragment: str = ""
    block_id: str = ""
    heading_path: tuple[str, ...] = ()
    text_quote: str = ""
    text_prefix: str = ""
    text_suffix: str = ""
    offset_css_px: float = 0

    def __post_init__(self):
        if not isinstance(self.document, DocumentRef):
            raise ValueError("来源必须引用 DocumentRef")
        if not isinstance(self.content_hash, str) or not re.fullmatch(
            r"[a-f0-9]{64}", self.content_hash
        ):
            raise ValueError("content_hash 必须是原始文件的 SHA-256")
        if type(self.offset_css_px) not in (int, float) or not math.isfinite(self.offset_css_px):
            raise ValueError("阅读偏移无效")
        if not isinstance(self.heading_path, (tuple, list)) or not all(
            isinstance(part, str) for part in self.heading_path
        ):
            raise ValueError("标题路径必须为字符串数组")
        object.__setattr__(self, "heading_path", tuple(self.heading_path))
        if not all(
            isinstance(getattr(self, key), str)
            for key in ("fragment", "block_id", "text_quote", "text_prefix", "text_suffix")
        ):
            raise ValueError("来源定位文本必须是字符串")

    def to_dict(self):
        value = asdict(self)
        value["heading_path"] = list(self.heading_path)
        return {"schema_version": CONTRACT_VERSION, **value}

    @classmethod
    def from_dict(cls, value):
        value = dict(value)
        version = value.pop("schema_version", None)
        if type(version) is not int or version != CONTRACT_VERSION:
            raise ValueError("不支持的来源位置契约版本")
        value["document"] = DocumentRef(**value["document"])
        return cls(**value)

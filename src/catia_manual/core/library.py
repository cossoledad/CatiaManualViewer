"""Content manifests, consistent snapshots and explicit library attachment. No Qt."""

import hashlib
import json
import os
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

from catia_manual.core.library_contract import relative_path
from catia_manual.core.paths import SAMPLE_EXTENSIONS, within_root
from catia_manual.core.resources import IMAGE_EXTENSIONS


def connect_readonly(path):
    return closing(
        sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    )


def application_excludes(database, root):
    database, root = Path(database).resolve(), Path(root).resolve()
    if not database.is_relative_to(root):
        return []
    return [
        path.relative_to(root).as_posix()
        for path in (
            database,
            database.with_name(database.name + "-wal"),
            database.with_name(database.name + "-shm"),
            database.parent / "rendered",
            database.parent / "backups",
        )
    ]


def snapshot(database, destination):
    """SQLite backup includes committed WAL pages. Never overwrite an existing file."""
    database, destination = Path(database).resolve(), Path(destination).resolve()
    if database == destination:
        raise ValueError("备份目标不能是原索引")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb"):
        pass
    try:
        with connect_readonly(database) as source, closing(sqlite3.connect(destination)) as target:
            source.backup(target)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return destination


def manifest_hash(files, release, language):
    value = {"schema_version": 1, "release": release, "language": language, "files": files}
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def scan_manifest(root, *, release="", language="en", exclude=(), progress=None):
    root = Path(root).resolve()
    if not (root / "online").is_dir():
        raise ValueError("文档根目录应包含 online 文件夹")
    excluded = {Path(p).resolve() for p in exclude}

    def is_excluded(path):
        absolute = path.absolute()
        return any(absolute == item or absolute.is_relative_to(item) for item in excluded)

    paths, names = [], set()

    # os.walk surfaces inaccessible directories; rglob may silently skip them on newer Python.
    def scan_error(error):
        raise error

    for folder, dirs, files in os.walk(root, onerror=scan_error, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not is_excluded(Path(folder) / d))
        for directory in dirs:
            candidate = Path(folder) / directory
            if candidate.is_symlink() or candidate.is_junction():
                raise ValueError(f"内容清单不接受目录链接：{candidate}")
        for name in sorted(files):
            path = Path(folder) / name
            if is_excluded(path):
                continue
            if path.is_symlink():
                raise ValueError(f"内容清单不接受文件链接：{path}")
            within_root(root, path)
            relative = relative_path(path.relative_to(root).as_posix())
            if relative.casefold() in names:
                raise ValueError(f"文档清单含大小写或 Unicode 冲突：{relative}")
            names.add(relative.casefold())
            paths.append((relative, path))
    entries = []
    for i, (relative, path) in enumerate(sorted(paths), 1):
        before = path.stat()
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        after = path.stat()
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            raise ValueError(f"扫描期间文件发生变化：{relative}，请重试")
        suffix = path.suffix.lower()
        kind = (
            "document"
            if suffix in {".htm", ".html"}
            else "image"
            if suffix in IMAGE_EXTENSIONS
            else "sample"
            if suffix in SAMPLE_EXTENSIONS
            else "resource"
        )
        entries.append(
            {"path": relative, "size": after.st_size, "content_hash": digest, "kind": kind}
        )
        if progress and (i % 100 == 0 or i == len(paths)):
            progress({"phase": "manifest", "processed": i, "total": len(paths)})
    return {
        "schema_version": 1,
        "release": release,
        "language": language,
        "files": entries,
        "manifest_hash": manifest_hash(entries, release, language),
    }


def verify_copy(database, root, progress=None):
    with connect_readonly(database) as connection:
        metadata = dict(connection.execute("SELECT key,value FROM metadata"))
        if metadata.get("manifest_verified") != "1":
            raise ValueError("原索引没有完整的已验证清单，请先在原文档库更新完整索引")
        stored = [
            dict(zip(("path", "size", "content_hash", "kind"), row, strict=True))
            for row in connection.execute(
                "SELECT path,size,content_hash,kind FROM library_files ORDER BY path"
            )
        ]
    expected = manifest_hash(stored, metadata.get("release", ""), metadata.get("language", "en"))
    if expected != metadata.get("manifest_hash"):
        raise ValueError("原索引的内容清单校验失败")
    actual = scan_manifest(
        root,
        release=metadata.get("release", ""),
        language=metadata.get("language", "en"),
        exclude=[
            Path(root) / relative_path(p)
            for p in json.loads(metadata.get("manifest_excludes", "[]"))
        ],
        progress=progress,
    )
    old, new = ({r["path"]: r for r in items} for items in (stored, actual["files"]))
    return {
        "library_id": metadata["library_id"],
        "expected_hash": expected,
        "manifest_hash": actual["manifest_hash"],
        "matched": expected == actual["manifest_hash"],
        "added": sorted(new.keys() - old.keys()),
        "removed": sorted(old.keys() - new.keys()),
        "modified": sorted(p for p in old.keys() & new.keys() if old[p] != new[p]),
        "files": len(new),
    }


def attach_copy(database, root, destination, *, accept_revision=False, progress=None):
    """Verify and copy an existing library, never rebind the original database."""
    root = Path(root).resolve()
    verification = verify_copy(database, root, progress)
    if not verification["matched"] and not accept_revision:
        raise ValueError("文档副本不一致；未复用索引。只有明确确认同库修订后才可继续")
    destination = snapshot(database, destination)
    try:
        with closing(sqlite3.connect(destination)) as connection:
            copied = dict(connection.execute("SELECT key,value FROM metadata"))
            if (
                copied.get("manifest_hash") != verification["expected_hash"]
                or copied.get("manifest_verified") != "1"
            ):
                raise ValueError("验证期间原索引已变化，请重新验证")
            connection.execute("INSERT OR REPLACE INTO metadata VALUES('root',?)", (str(root),))
            exclusions = set(json.loads(copied.get("manifest_excludes", "[]")))
            exclusions.update(application_excludes(destination, root))
            connection.execute(
                "INSERT OR REPLACE INTO metadata VALUES('manifest_excludes',?)",
                (json.dumps(sorted(exclusions)),),
            )
            connection.execute(
                "INSERT OR REPLACE INTO metadata VALUES('manifest_verified',?)",
                ("1" if verification["matched"] else "0",),
            )
            connection.execute(
                "INSERT OR REPLACE INTO metadata VALUES('attached_at',?)", (str(time.time_ns()),)
            )
            connection.commit()
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return {
        **verification,
        "database": str(destination),
        "root": str(root),
        "requires_update": not verification["matched"],
    }


def backup_name(database, label="schema1"):
    return (
        Path(database).parent
        / "backups"
        / f"{Path(database).stem}-{label}-{time.time_ns()}-{uuid.uuid4().hex[:6]}.sqlite3"
    )

"""Portable settings/data bundles. SQLite and filesystem work only; no Qt or models."""

import hashlib
import json
import shutil
import sqlite3
import stat
import tempfile
import tomllib
import uuid
import zipfile
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import tomlkit

from catia_manual import __version__
from catia_manual.core.index_schema import SCHEMA_VERSION
from catia_manual.core.library import attach_copy, connect_readonly, snapshot, verify_copy
from catia_manual.core.library_contract import relative_path
from catia_manual.core.paths import within_root

FORMAT = "catia-manual-viewer-transfer"
DATABASE = "data/library.sqlite3"
MANUAL = "manuals/English"
SECRET_FIELDS = {"api_key", "api_key_override", "api_key_encrypted", "token", "password"}


def check_cancel(cancelled):
    if cancelled and cancelled():
        raise ValueError("操作已取消")


def public_settings(settings):
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k.casefold() not in SECRET_FIELDS}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value

    return {
        "documentation": {"root": MANUAL, "database": DATABASE, "homepage": settings.homepage},
        "catia": {
            "launcher": "catia/CATSTART.exe",
            "environment_dir": "catia/CATEnv",
            "environment": "",
        },
        "translation": {**clean(deepcopy(settings.translation)), "model_dir": "data/models"},
        "search": {**clean(deepcopy(settings.search)), "model_dir": "data/search-models"},
        "appearance": deepcopy(settings.appearance),
    }


def activation_changes(current, imported):
    """Replace service credentials explicitly; never reuse a target key at a new endpoint."""
    changes = deepcopy(imported)
    changes["translation"].update(api_key_encrypted="", api_key="")
    changes["catia"] = {
        "launcher": str(current.launcher),
        "environment_dir": str(current.environment_dir),
        "environment": current.environment,
    }
    return changes


def portable_path(value):
    path = relative_path(value)
    if path != value or "\\" in value:
        raise ValueError("迁移包包含非规范路径")
    for part in path.split("/"):
        reserved = part.split(".", 1)[0].upper() in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }
        if part.endswith((".", " ")) or any(c in part for c in '<>"|?*') or reserved:
            raise ValueError("迁移包包含不支持的文件名")
    return path


def model_files(directory, prefix):
    directory = Path(directory).resolve()
    if not directory.is_dir():
        return []
    if prefix == "data/models":
        names = {"manifest.json"}
        for pair in ("en_zh", "zh_en"):
            names.update(
                pair + "/" + name
                for name in (
                    "metadata.json",
                    "README.md",
                    "sentencepiece.model",
                    "model/config.json",
                    "model/model.bin",
                    "model/shared_vocabulary.json",
                )
            )
    else:
        from catia_manual.core.embedding import FILES

        names = set(FILES) | {"model-info.json"}
    result = []
    for name in sorted(names):
        path = directory / name
        if any(
            p.is_symlink() or p.is_junction()
            for p in (path, *path.parents)
            if p != directory.parent
        ):
            raise ValueError("迁移包不接受模型目录链接")
        if path.is_file():
            result.append((prefix + "/" + name, path, None))
    return result


def export_bundle(
    settings,
    destination,
    *,
    include_documents=True,
    include_models=True,
    progress=None,
    cancelled=None,
):
    """Export a consistent current library, never overwrite another file or copy runtime caches."""
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_relative_to(settings.root) or any(
        destination.is_relative_to(Path(getattr(settings, section).get("model_dir", ".")).resolve())
        for section in ("translation", "search")
        if getattr(settings, section).get("model_dir")
    ):
        raise ValueError("请将迁移包保存到文档和模型目录之外")
    with tempfile.TemporaryDirectory(prefix="cmv-export-", dir=destination.parent) as temporary:
        database = snapshot(settings.database, Path(temporary) / "library.sqlite3")
        verification = verify_copy(database, settings.root, progress)
        if not verification["matched"]:
            raise ValueError("文档已变化，请完整更新索引后再导出")
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("UPDATE metadata SET value='' WHERE key='root'")
            connection.commit()
            files = connection.execute(
                "SELECT path,content_hash FROM library_files ORDER BY path"
            ).fetchall()
        config = public_settings(settings)
        if not include_models:
            if config["translation"].get("provider") == "local":
                config["translation"]["provider"] = "glossary"
            config["search"]["mode"] = "keyword"
        config_path = Path(temporary) / "config.toml"
        config_path.write_text(tomlkit.dumps(config), "utf-8")
        entries = [(DATABASE, database, None), ("config.toml", config_path, None)]
        if include_documents:
            entries.extend(
                (MANUAL + "/" + p, within_root(settings.root, settings.root / p), digest)
                for p, digest in files
            )
        if include_models:
            entries.extend(model_files(settings.translation["model_dir"], "data/models"))
            entries.extend(model_files(settings.search["model_dir"], "data/search-models"))
        manifest = {
            "format": FORMAT,
            "schema_version": 1,
            "app_version": __version__,
            "library_id": verification["library_id"],
            "manifest_hash": verification["expected_hash"],
            "documents": include_documents,
            "models": include_models,
            "files": [],
        }
        # Exclusive creation also protects against a second writer after a file dialog.
        with destination.open("xb") as output:
            try:
                names = set()
                with zipfile.ZipFile(
                    output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3
                ) as package:
                    for number, (name, path, expected) in enumerate(entries, 1):
                        name = portable_path(name)
                        if name.casefold() in names or path.is_symlink() or path.is_junction():
                            raise ValueError("迁移包包含重复路径或文件链接")
                        names.add(name.casefold())
                        digest, size = hashlib.sha256(), 0
                        before = path.stat()
                        with (
                            path.open("rb") as source,
                            package.open(name, "w", force_zip64=True) as target,
                        ):
                            while chunk := source.read(1024 * 1024):
                                check_cancel(cancelled)
                                digest.update(chunk)
                                size += len(chunk)
                                target.write(chunk)
                        after = path.stat()
                        value = digest.hexdigest()
                        if (before.st_size, before.st_mtime_ns) != (
                            after.st_size,
                            after.st_mtime_ns,
                        ) or (expected and value != expected):
                            raise ValueError("导出期间文件已变化，请重试：" + name)
                        manifest["files"].append({"path": name, "size": size, "sha256": value})
                        if progress and (number % 100 == 0 or number == len(entries)):
                            progress(
                                {"phase": "export", "processed": number, "total": len(entries)}
                            )
                    package.writestr("transfer.json", json.dumps(manifest, ensure_ascii=False))
            except BaseException:
                output.close()
                destination.unlink(missing_ok=True)
                raise
    return {
        "archive": str(destination),
        "files": len(manifest["files"]),
        "bytes": destination.stat().st_size,
    }


def inspect_bundle(archive):
    with zipfile.ZipFile(archive) as package:
        info = package.getinfo("transfer.json")
        if info.file_size > 32 * 1024 * 1024:
            raise ValueError("迁移清单过大")
        value = json.loads(package.read(info))
        if (
            not isinstance(value, dict)
            or value.get("format") != FORMAT
            or value.get("schema_version") != 1
        ):
            raise ValueError("不支持的迁移包")
        if not isinstance(value.get("app_version"), str) or not isinstance(
            value.get("files"), list
        ):
            raise ValueError("迁移清单信息无效")
        if not isinstance(value.get("manifest_hash"), str) or len(value["manifest_hash"]) != 64:
            raise ValueError("迁移包原文清单无效")
        uuid.UUID(value.get("library_id", ""))
        if type(value.get("documents")) is not bool or type(value.get("models")) is not bool:
            raise ValueError("迁移包选项无效")
        entries, names = {}, set()
        for item in value.get("files", []):
            if not isinstance(item, dict) or "path" not in item:
                raise ValueError("迁移清单文件信息无效")
            name = portable_path(item["path"])
            if name.casefold() in names or name == "transfer.json":
                raise ValueError("迁移清单包含重复文件")
            if not (
                name in {DATABASE, "config.toml"}
                or name.startswith((MANUAL + "/", "data/models/", "data/search-models/"))
            ):
                raise ValueError("迁移包包含非数据文件")
            if (
                type(item.get("size")) is not int
                or item["size"] < 0
                or not isinstance(item.get("sha256"), str)
                or len(item["sha256"]) != 64
            ):
                raise ValueError("迁移清单文件信息无效")
            entries[name] = item
            names.add(name.casefold())
        if not {DATABASE, "config.toml"} <= entries.keys() or len(entries) > 200000:
            raise ValueError("迁移包缺少索引/设置或文件过多")
        seen = set()
        for member in package.infolist():
            name = portable_path(member.filename)
            mode = member.external_attr >> 16
            if name in seen or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
                raise ValueError("迁移包包含重复文件或链接")
            seen.add(name)
            if name != "transfer.json" and (
                name not in entries or member.file_size != entries[name]["size"]
            ):
                raise ValueError("迁移包与清单不一致")
        if seen != entries.keys() | {"transfer.json"}:
            raise ValueError("迁移包缺少清单文件")
        value["total_bytes"] = sum(e["size"] for e in entries.values())
        return value


def import_bundle(archive, destination, *, root=None, progress=None, cancelled=None):
    """Verify into a new directory. Caller activates settings only after this succeeds."""
    archive, destination = Path(archive).resolve(), Path(destination).resolve()
    manifest = inspect_bundle(archive)
    if not manifest["documents"] and root is None:
        raise ValueError("此迁移包未包含文档，请选择文档副本")
    if manifest["documents"] and root is not None:
        raise ValueError("包含文档的迁移包无需指定外部文档目录")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir()  # Never overwrite an existing installation or an earlier import.
    try:
        free = shutil.disk_usage(destination).free
        database_bytes = next(e["size"] for e in manifest["files"] if e["path"] == DATABASE)
        if manifest["total_bytes"] + database_bytes + 64 * 1024 * 1024 > free:
            raise ValueError("目标磁盘空间不足")
        with zipfile.ZipFile(archive) as package:
            for number, entry in enumerate(manifest["files"], 1):
                path = destination / entry["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                with package.open(entry["path"]) as source, path.open("xb") as target:
                    while chunk := source.read(1024 * 1024):
                        check_cancel(cancelled)
                        digest.update(chunk)
                        target.write(chunk)
                if digest.hexdigest() != entry["sha256"]:
                    raise ValueError("迁移包文件校验失败：" + entry["path"])
                if progress and (number % 100 == 0 or number == len(manifest["files"])):
                    progress(
                        {"phase": "import", "processed": number, "total": len(manifest["files"])}
                    )
        database = destination / DATABASE
        with connect_readonly(database) as connection:
            if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                raise ValueError("迁移包索引版本不受支持，请使用匹配版本的软件")
            meta = dict(connection.execute("SELECT key,value FROM metadata"))
            if (
                meta.get("library_id") != manifest["library_id"]
                or meta.get("manifest_hash") != manifest["manifest_hash"]
            ):
                raise ValueError("迁移包文档库身份不一致")
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("迁移包数据库损坏")
        actual_root = destination / MANUAL if manifest["documents"] else Path(root).resolve()
        # Existing attachment logic verifies every original file before rebinding a copy.
        attached = attach_copy(
            database, actual_root, database.with_name("attached.sqlite3"), progress=progress
        )
        (database.parent / "attached.sqlite3").replace(database)
        config = tomllib.loads((destination / "config.toml").read_text("utf-8"))
        # Take preferences only. Never resolve archive-supplied filesystem paths.
        sections = {
            name: config.get(name, {})
            for name in ("documentation", "translation", "search", "appearance")
        }
        if not all(isinstance(v, dict) for v in sections.values()):
            raise ValueError("迁移包设置格式无效")
        clean = public_settings(
            SimpleNamespace(
                homepage=sections["documentation"].get("homepage", "CATIA_P3_homepage.htm"),
                translation=sections["translation"],
                search=sections["search"],
                appearance=sections["appearance"],
            )
        )
        clean["documentation"].update(root=str(actual_root), database=str(database))
        clean["translation"]["model_dir"] = str(destination / "data/models")
        clean["search"]["model_dir"] = str(destination / "data/search-models")
        clean["documentation"]["homepage"] = config.get("documentation", {}).get(
            "homepage", "CATIA_P3_homepage.htm"
        )
        path = destination / "config.toml"
        path.write_text(tomlkit.dumps(clean), "utf-8")
        return {
            "config": str(path),
            "library_id": attached["library_id"],
            "database": str(database),
            "root": str(actual_root),
        }
    except BaseException:
        # Only the fresh, exclusively created directory is rolled back.
        shutil.rmtree(destination)
        raise


def import_directory(data_directory):
    return Path(data_directory) / "imports" / uuid.uuid4().hex[:12]

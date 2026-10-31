import argparse
import importlib.util
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

from catia_manual.config import load_settings
from catia_manual.core.catalog import ManualCatalog
from catia_manual.core.index import ManualIndex
from catia_manual.core.library import attach_copy, snapshot, verify_copy
from catia_manual.core.library_contract import SourceLocation
from catia_manual.core.parser import ManualParser
from catia_manual.core.search import SearchService
from catia_manual.core.source import resolve_source, source_for_block
from catia_manual.core.translation import TranslationService, make_provider
from catia_manual.integrations.catia import CatiaLauncher


def emit(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="CATIA V5 本地文档工具（输出 JSON）")
    parser.add_argument("--config", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="检查环境、文档目录及 SQLite FTS5")
    index = sub.add_parser("index", help="增量建立索引")
    index.add_argument("--module")
    index.add_argument("--limit", type=int)
    index.add_argument("--force", action="store_true")
    search = sub.add_parser("search", help="搜索标题和正文")
    search.add_argument("query")
    search.add_argument("--module")
    search.add_argument("--category", help="功能分类名称或 ID，如 mechanical-design")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--explain", action="store_true", help="同时输出中文查询展开方式")
    search.add_argument("--mode", choices=("keyword", "hybrid", "semantic"))
    semantic = sub.add_parser("semantic", help="本机语义模型与增量向量索引")
    semantic.add_argument("action", choices=("status", "download", "build"))
    sub.add_parser("structure", help="增量整理目录、双语命令和文章搜索结构")
    graph = sub.add_parser("map", help="输出只读功能地图 JSON")
    graph_scope = graph.add_mutually_exclusive_group()
    graph_scope.add_argument("--manual")
    graph_scope.add_argument("--category")
    graph.add_argument("--focus", help="库内相对页面路径")
    graph.add_argument("--limit", type=int, default=80)
    locations = sub.add_parser("locations", help="读取文章在各手册中的目录位置")
    locations.add_argument("path")
    locations.add_argument("--manual")
    search.add_argument("--manual", help="按功能手册范围搜索，包括显式收录的共享页面")
    for name in ("read", "toc", "examples"):
        command = sub.add_parser(name)
        command.add_argument("path", help="相对于文档根目录的路径")
        if name == "read":
            command.add_argument("--with-source", action="store_true")
    library = sub.add_parser("library", help="文档库身份、验证副本与安全快照")
    library.add_argument("action", choices=("status", "manifest", "verify", "attach", "snapshot"))
    library.add_argument("--database", type=Path, help="已有索引，默认使用当前配置")
    library.add_argument("--root", type=Path, help="待验证的文档副本")
    library.add_argument("--destination", type=Path)
    library.add_argument("--accept-revision", action="store_true", help="明确确认副本属于同库修订")
    source = sub.add_parser("resolve-source", help="解析 v1 来源位置 JSON")
    source.add_argument("location", type=Path)
    terms = sub.add_parser("terms", help="读取或设置用户术语别名")
    terms.add_argument("--alias")
    terms.add_argument("--english")
    terms.add_argument("--module", default="")
    launch = sub.add_parser("open", help="查看打开示例的命令，--execute 才实际启动")
    launch.add_argument("path")
    launch.add_argument("--execute", action="store_true")
    translate = sub.add_parser("translate")
    translate.add_argument("text")
    translate.add_argument("--target", default="zh-CN")
    sub.add_parser("stats")
    sub.add_parser("catalog", help="输出功能分类和手册名称")
    gui = sub.add_parser("gui")
    gui.add_argument("--open", dest="open_target", help="打开本地文档路径或 CATIA 文档服务 URL")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.config)
        manual = ManualParser(settings.root)
        if args.command == "doctor":
            with sqlite3.connect(":memory:") as connection:
                connection.execute("CREATE VIRTUAL TABLE probe USING fts5(text)")
            emit(
                {
                    "python": sys.version,
                    "executable": sys.executable,
                    "documentation": str(settings.root),
                    "root_exists": settings.root.is_dir(),
                    "homepage_exists": (settings.root / settings.homepage).is_file(),
                    "launcher_exists": settings.launcher.is_file(),
                    "environment_exists": (
                        settings.environment_dir / (settings.environment + ".txt")
                    ).is_file(),
                    "sqlite": sqlite3.sqlite_version,
                    "fts5": True,
                    "optional_packages": {
                        name: importlib.util.find_spec(name) is not None
                        for name in ("PySide6", "win32com", "httpx", "onnxruntime", "tokenizers")
                    },
                }
            )
        elif args.command == "read":
            document = manual.parse(args.path)
            result = asdict(document)
            if args.with_source:
                with ManualIndex(settings.database, settings.root) as repository:
                    result["library_id"] = repository.library_id
                    result["sources"] = [
                        source_for_block(repository.library_id, document, b, ordinal=i).to_dict()
                        for i, b in enumerate(document.blocks)
                    ]
            emit(result)
        elif args.command == "library":
            database = args.database or settings.database
            root = args.root or settings.root
            if args.action in {"verify", "attach"}:
                if args.action == "verify":
                    result = verify_copy(database, root)
                    emit(result)
                    return 0 if result["matched"] else 2
                if args.destination is None:
                    raise ValueError("attach 必须指定 --destination，原索引不会被改绑")
                emit(
                    attach_copy(
                        database, root, args.destination, accept_revision=args.accept_revision
                    )
                )
            elif args.action == "snapshot":
                if args.destination is None:
                    raise ValueError("snapshot 必须指定 --destination")
                emit({"snapshot": str(snapshot(database, args.destination))})
            else:
                with ManualIndex(database, root) as repository:
                    if args.action == "status":
                        emit(repository.stats())
                    else:
                        if repository.meta("manifest_verified") != "1":
                            raise ValueError("请先完整更新索引以生成已验证清单")
                        emit(
                            {
                                "schema_version": 1,
                                "library_id": repository.library_id,
                                "release": repository.meta("release"),
                                "language": repository.meta("language"),
                                "manifest_hash": repository.meta("manifest_hash"),
                                "files": [
                                    dict(row)
                                    for row in repository.connection.execute(
                                        "SELECT * FROM library_files ORDER BY path"
                                    )
                                ],
                            }
                        )
        elif args.command == "resolve-source":
            location = SourceLocation.from_dict(json.loads(args.location.read_text("utf-8")))
            with ManualIndex(settings.database, settings.root) as repository:
                emit(resolve_source(manual, location, repository.library_id).to_dict())
        elif args.command in {"structure", "locations"}:
            from catia_manual.core.structure import StructureIndex

            with ManualIndex(settings.database, settings.root) as repository:
                service = StructureIndex(repository)
                if args.command == "structure":
                    emit(service.build(backup=True))
                elif service.ready():
                    emit(service.placements(args.path, [args.manual] if args.manual else []))
                else:
                    raise ValueError("请先整理搜索结构或更新索引")
        elif args.command == "terms":
            with ManualIndex(settings.database, settings.root) as repository:
                if args.alias:
                    repository.set_alias(args.alias, args.english or "", args.module)
                emit(
                    [
                        dict(row)
                        for row in repository.connection.execute(
                            "SELECT * FROM term_aliases ORDER BY alias,module"
                        )
                    ]
                )
        elif args.command == "semantic":
            from catia_manual.core.embedding import (
                FINGERPRINT,
                download_model,
                make_embedder,
                model_status,
            )
            from catia_manual.core.semantic import PASSAGE_VERSION, SemanticIndex

            if args.action == "download":
                emit(download_model(settings.search["model_dir"]))
            else:
                with ManualIndex(settings.database, settings.root) as repository:
                    service = SemanticIndex(repository)
                    if args.action == "build":
                        emit(service.build(make_embedder(settings.search)))
                    else:
                        emit(
                            {
                                "model": model_status(settings.search["model_dir"]),
                                "index": service.status(FINGERPRINT + ":" + PASSAGE_VERSION),
                            }
                        )
        elif args.command == "toc":
            emit([asdict(item) for item in manual.toc(args.path)])
        elif args.command == "examples":
            emit([asdict(link) for link in manual.parse(args.path).links if link.kind == "sample"])
        elif args.command == "open":
            launcher = CatiaLauncher(settings)
            emit(
                launcher.open(args.path)
                if args.execute
                else {
                    "dry_run": True,
                    "argv": launcher.command(args.path),
                    "strategy": "优先复用 CATIA COM 会话，未运行时使用 CATSTART",
                }
            )
        elif args.command == "gui":
            from catia_manual.gui.app import run

            return run(settings, args.open_target)
        elif args.command == "catalog":
            emit([asdict(group) for group in ManualCatalog(settings.root).categories])
        else:
            with ManualIndex(settings.database, settings.root) as repository:
                if args.command == "map":
                    from catia_manual.core.function_map import GraphService

                    scope = (
                        {"kind": "manual", "id": args.manual}
                        if args.manual
                        else {"kind": "category", "id": args.category}
                        if args.category
                        else {"kind": "all"}
                    )
                    query = {"scope": scope, "limit": args.limit}
                    if args.focus:
                        query["focus"] = {"path": args.focus, "manual": args.manual or ""}
                    emit(GraphService(repository).map(query).to_dict())
                elif args.command == "index":
                    result = repository.build(
                        args.module,
                        args.limit,
                        args.force,
                        progress=lambda p: print(json.dumps(p), file=sys.stderr),
                    )
                    emit(result)
                    return 1 if result["errors"] else 0
                elif args.command == "search":
                    if args.manual and args.category:
                        raise ValueError("manual 和 category 只能指定一种功能范围")
                    modules = (
                        ManualCatalog(settings.root).scope(
                            [
                                manual.id
                                for manual in ManualCatalog(settings.root)
                                .category(args.category)
                                .manuals
                            ]
                        )
                        if args.category
                        else None
                    )
                    if args.manual:
                        modules = ManualCatalog(settings.root).scope([args.manual])
                    config = {**settings.search}
                    if args.mode:
                        config["mode"] = args.mode
                    response = SearchService(repository, settings.translation, config).search(
                        args.query, args.module, args.limit, modules=modules
                    )
                    emit(
                        asdict(response) if args.explain else [asdict(hit) for hit in response.hits]
                    )
                elif args.command == "stats":
                    emit(repository.stats())
                elif args.command == "translate":
                    service = TranslationService(repository, make_provider(settings.translation))
                    emit(asdict(service.translate(args.text, args.target)))
        return 0
    except KeyboardInterrupt:
        print("操作已中断；已提交的索引进度保留。", file=sys.stderr)
        return 130
    except Exception as exc:
        emit({"error": str(exc), "type": type(exc).__name__})
        return 1

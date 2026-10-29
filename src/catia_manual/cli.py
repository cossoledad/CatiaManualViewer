import argparse
import importlib.util
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

from catia_manual.config import load_settings
from catia_manual.core.index import ManualIndex
from catia_manual.core.parser import ManualParser
from catia_manual.core.search import SearchService
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
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--explain", action="store_true", help="同时输出中文查询展开方式")
    for name in ("read", "toc", "examples"):
        command = sub.add_parser(name)
        command.add_argument("path", help="相对于文档根目录的路径")
    launch = sub.add_parser("open", help="查看打开示例的命令，--execute 才实际启动")
    launch.add_argument("path")
    launch.add_argument("--execute", action="store_true")
    translate = sub.add_parser("translate")
    translate.add_argument("text")
    translate.add_argument("--target", default="zh-CN")
    sub.add_parser("stats")
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
                        for name in ("PySide6", "win32com", "httpx")
                    },
                }
            )
        elif args.command == "read":
            result = asdict(manual.parse(args.path))
            emit(result)
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
        else:
            with ManualIndex(settings.database, settings.root) as repository:
                if args.command == "index":
                    result = repository.build(
                        args.module,
                        args.limit,
                        args.force,
                        progress=lambda p: print(json.dumps(p), file=sys.stderr),
                    )
                    emit(result)
                    return 1 if result["errors"] else 0
                elif args.command == "search":
                    response = SearchService(repository, settings.translation).search(
                        args.query, args.module, args.limit
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

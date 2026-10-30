"""Read installed documentation and validate all indexed candidates in a fresh SQLite snapshot."""

import argparse
import json
import sys
import time
from pathlib import Path

from catia_manual.config import load_settings
from catia_manual.core.catalog import ManualCatalog
from catia_manual.core.index import ManualIndex
from catia_manual.core.library import snapshot
from catia_manual.core.search import SearchService
from catia_manual.core.structure import StructureIndex

project = Path(__file__).resolve().parents[1]
settings = load_settings()
folder = project / ".cache/structure-real" / str(time.time_ns())
parser = argparse.ArgumentParser()
parser.add_argument("--reuse", action="store_true")
args = parser.parse_args()
if args.reuse:
    database = Path(
        json.loads((project / "data/structured-search-real.json").read_text("utf-8"))["database"]
    ).resolve()
    if not database.is_relative_to((project / ".cache/structure-real").resolve()):
        raise ValueError("仅允许复用本脚本的隔离索引副本")
else:
    database = snapshot(settings.database, folder / "index.sqlite3")
questions = [
    ("相合约束", "cfyugasmut0301.htm"),
    ("偏移约束", "cfyugasmut0303.htm"),
    ("管理约束", "cfyugasmut0300.htm"),
    ("Managing Constraints", "cfyugasmut0300.htm"),
    ("Coincidence Constraint", "cfyugasmut0301.htm"),
]
report = {"checks": [], "failures": [], "database": str(database)}
with ManualIndex(database, settings.root) as index:
    start = time.monotonic()

    def progress(value):
        if value["processed"] % 1000 == 0:
            print(json.dumps(value), flush=True)

    report["build"] = StructureIndex(index).build(progress)
    report["build_seconds"] = round(time.monotonic() - start, 3)
    report["articles"] = index.stats()["documents"]
    catalog = ManualCatalog(settings.root)
    service = SearchService(index, {"provider": "glossary"}, {"mode": "keyword"})
    scopes = {
        "all": None,
        "mechanical-design": catalog.scope(
            [m.id for m in catalog.category("mechanical-design").manuals]
        ),
        "assembly": catalog.scope(["asmug_C2"]),
        "weld": catalog.scope(["wd1ug_C2"]),
    }
    for name, scope in scopes.items():
        for query, expected in questions:
            start = time.monotonic()
            response = service.search(query, modules=scope, limit=5)
            hit = response.hits[0] if response.hits else None
            available = name != "weld" or expected != "cfyugasmut0300.htm"
            passed = (
                (hit is not None and hit.path.endswith(expected))
                if available
                else (all(not h.path.endswith(expected) for h in response.hits))
            )
            report["checks"].append(
                {
                    "scope": name,
                    "query": query,
                    "passed": passed,
                    "expected_in_scope": available,
                    "seconds": round(time.monotonic() - start, 3),
                    "top": [
                        {
                            "title": h.title,
                            "path": h.path,
                            "reason": h.reason,
                            "manual": h.placement["manual"] if h.placement else None,
                        }
                        for h in response.hits
                    ],
                }
            )
            if not passed:
                report["failures"].append(name + ": " + query)
    reference = service.search("Coincidence Constraints", modules=scopes["assembly"]).hits
    report["reference_exact"] = bool(reference and reference[0].path.endswith("cfyugasmrf0102.htm"))
    if not report["reference_exact"]:
        report["failures"].append("explicit reference title")
    report["no_model_or_qt"] = not any(
        name.startswith(("PySide6", "onnxruntime", "tokenizers")) for name in sys.modules
    )
report["passed"] = not report["failures"] and not report["build"]["errors"]
output = project / "data/structured-search-real.json"
output.parent.mkdir(exist_ok=True)
output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
print(
    json.dumps({"passed": report["passed"], "failures": report["failures"], "report": str(output)})
)
raise SystemExit(0 if report["passed"] else 1)

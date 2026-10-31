"""Validate the map against the isolated real-library snapshot used by search acceptance."""

import json
import sys
import time
from pathlib import Path

from catia_manual.config import load_settings
from catia_manual.core.catalog import ManualCatalog
from catia_manual.core.function_map import GraphService
from catia_manual.core.index import ManualIndex
from catia_manual.core.library_contract import SourceLocation

project = Path(__file__).resolve().parents[1]
settings = load_settings()
previous = json.loads((project / "data/structured-search-real.json").read_text("utf-8"))
database = Path(previous["database"]).resolve()
if not database.is_relative_to((project / ".cache/structure-real").resolve()):
    raise ValueError("仅使用已验证的隔离索引副本")
report = {"checks": [], "failures": [], "timings": [], "database": str(database)}
with ManualIndex(database, settings.root) as index:
    catalog = ManualCatalog(settings.root)
    cases = [
        ("asmug_C2", "online/cfyugasm_C2/cfyugasmut0301.htm"),
        ("asmug_C2", "online/cfyugasm_C2/cfyugasmut0303.htm"),
        ("asmug_C2", "online/cfyugasm_C2/cfyugasmut0300.htm"),
        ("wd1ug_C2", "online/cfyugasm_C2/cfyugasmut0301.htm"),
        ("prtug_C2", "online/prtug_C2/prtugbt0501.htm"),
    ]
    for manual, path in cases:
        start = time.perf_counter()
        graph = GraphService(index, catalog).map(
            {"scope": {"kind": "manual", "id": manual}, "focus": {"path": path, "manual": manual}}
        )
        elapsed = (time.perf_counter() - start) * 1000
        selected = next(n for n in graph.nodes if n["id"] == graph.selected)
        passed = (
            selected["document_ref"]["path"] == path
            and selected["placement_context"]["manual"] == manual
        )
        report["checks"].append(
            {
                "manual": manual,
                "path": path,
                "passed": passed,
                "nodes": len(graph.nodes),
                "title": selected["label_en"],
            }
        )
        report["timings"].append(round(elapsed, 2))
        if not passed:
            report["failures"].append(manual + ": " + path)
        if any((n["placement_context"] or {}).get("manual", manual) != manual for n in graph.nodes):
            report["failures"].append("scope leaked")
        if len(graph.nodes) > 80:
            report["failures"].append("node bound")
        for node in graph.nodes:
            if node["source"]:
                SourceLocation.from_dict(node["source"])
        if str(settings.root) in json.dumps(graph.to_dict()):
            report["failures"].append("absolute path in snapshot")
    for category in catalog.categories:
        graph = GraphService(index, catalog).map({"scope": {"kind": "category", "id": category.id}})
        if len(graph.nodes) > 80 or not graph.nodes:
            report["failures"].append(category.id)
    report["manuals"] = len(catalog.manuals)
    report["articles"] = index.stats()["documents"]
    report["no_qt_or_models"] = not any(
        n.startswith(("PySide6", "onnxruntime", "tokenizers")) for n in sys.modules
    )
report["passed"] = not report["failures"] and report["no_qt_or_models"]
output = project / "data/function-map-real.json"
output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
print(json.dumps(report, ensure_ascii=False))
raise SystemExit(0 if report["passed"] else 1)

"""Read actual CATIA page relationships using an isolated library index."""

import json
import sys
import time
from pathlib import Path

from catia_manual.config import load_settings
from catia_manual.core.index import ManualIndex
from catia_manual.core.library_contract import SourceLocation
from catia_manual.core.relations import RelationService

project = Path(__file__).resolve().parents[1]
settings = load_settings()
database = Path(
    json.loads((project / "data/structured-search-real.json").read_text("utf-8"))["database"]
).resolve()
if not database.is_relative_to((project / ".cache/structure-real").resolve()):
    raise ValueError("仅验证隔离索引")
report = {"cases": [], "failures": []}
with ManualIndex(database, settings.root) as index:
    for path in (
        "online/cfyugasm_C2/cfyugasmut0301.htm",
        "online/cfyugasm_C2/cfyugasmut0300.htm",
        "online/prtug_C2/prtugbt0501.htm",
    ):
        started = time.perf_counter()
        service = RelationService(index)
        graph = service.map({"path": path})
        expand = [n["id"] for n in graph.nodes if n["kind"] == "command"][:1]
        expand += [n["id"] for n in graph.nodes if n["role"] == "reference"][:1]
        graph = service.map({"path": path, "expanded": expand})
        nodes = {n["id"]: n for n in graph.nodes}
        for edge in graph.edges:
            for proof in edge["evidence"]:
                source = SourceLocation.from_dict(proof["source"])
                assert source.document.path == nodes[edge["source"]]["document_ref"]["path"]
                document = service.documents[source.document.path]
                assert source.content_hash == document.content_hash
                if source.block_id:
                    assert source.block_id in {b.id for b in document.blocks}
        assert len(graph.nodes) <= 80 and len(graph.edges) <= 240
        assert len([e for e in graph.edges if e["layout"]]) == len(graph.nodes) - 1
        report["cases"].append(
            {
                "path": path,
                "nodes": len(graph.nodes),
                "edges": len(graph.edges),
                "types": sorted({e["kind"] for e in graph.edges}),
                "milliseconds": round((time.perf_counter() - started) * 1000, 2),
            }
        )
report["no_qt_or_model"] = not any(
    n.startswith(("PySide6", "onnxruntime", "tokenizers")) for n in sys.modules
)
report["passed"] = not report["failures"] and report["no_qt_or_model"]
(project / "data/relations-real.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2), "utf-8"
)
print(json.dumps(report, ensure_ascii=False))

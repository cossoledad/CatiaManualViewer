"""Local real-model retrieval check on a small, explicit B33 article subset. No APIs."""

import json
import shutil
import sys
import time
from pathlib import Path

from catia_manual.config import load_settings
from catia_manual.core.embedding import make_embedder
from catia_manual.core.index import ManualIndex
from catia_manual.core.parser import ManualParser
from catia_manual.core.search import SearchService
from catia_manual.core.semantic import SemanticIndex
from catia_manual.core.source import SourceLocation, resolve_source

project = Path(__file__).resolve().parents[1]
settings = load_settings()
folder = project / ".cache/stage-c-real" / str(time.time_ns())
root = folder / "manual"
module = root / "online/prtug_C2"
module.mkdir(parents=True)
for number in (
    "0501",
    "0502",
    "0504",
    "0506",
    "0508",
    "0510",
    "0513",
    "0600",
    "0602",
    "0701",
    "0801",
    "0901",
):
    shutil.copy2(settings.root / f"online/prtug_C2/prtugbt{number}.htm", module)
config = {
    **settings.search,
    "model_dir": str(project / "data/search-models/e5-small"),
    "mode": "hybrid",
}
report = {
    "checks": [],
    "failures": [],
    "corpus": "12 real Part Design articles, restricted candidate set",
}
questions = [
    ("沿着旋转轴把草图转成一个回转实体", "0504"),
    ("把同一个特征按行和列复制很多次", "0701"),
    ("让凸台一直延伸到指定曲面", "0513"),
    ("在实体上挖出一块材料", "0502"),
    ("给棱边切一个斜角", "0602"),
]
with ManualIndex(folder / "index.sqlite3", root) as index:
    assert not index.build()["errors"]
    start = time.monotonic()
    embedder = make_embedder(config)
    built = SemanticIndex(index).build(embedder)
    report["build"] = {"seconds": round(time.monotonic() - start, 3), "passages": built["valid"]}
    service = SearchService(index, {"provider": "glossary"}, config)
    for question, number in questions:
        start = time.monotonic()
        response = service.search(question, limit=5)
        baseline = SearchService(index, {"provider": "glossary"}).search(question, limit=5)
        expected = f"prtugbt{number}.htm"
        rank = next(
            (i for i, hit in enumerate(response.hits, 1) if hit.path.endswith(expected)), None
        )
        sources = all(
            resolve_source(
                ManualParser(root), SourceLocation.from_dict(hit.source), index.library_id
            ).source
            for hit in response.hits
        )
        passed = rank is not None and rank <= 3 and sources
        report["checks"].append(
            {
                "query": question,
                "passed": bool(passed),
                "rank": rank,
                "keyword_results": len(baseline.hits),
                "seconds": round(time.monotonic() - start, 3),
                "titles": [hit.title for hit in response.hits],
            }
        )
        if not passed:
            report["failures"].append(question)
    report["qt_free"] = not any(name.startswith("PySide6") for name in sys.modules)
    start = time.monotonic()
    reused = SemanticIndex(index).build(embedder)
    report["warm_build"] = {
        "seconds": round(time.monotonic() - start, 3),
        "embedded": reused["embedded"],
        "reused": reused["reused"],
    }
report["passed"] = not report["failures"] and report["qt_free"]
output = project / "data/stage-c-real.json"
output.parent.mkdir(exist_ok=True)
output.write_text(json.dumps(report, ensure_ascii=False, indent=2), "utf-8")
print(json.dumps(report, ensure_ascii=False))
raise SystemExit(0 if report["passed"] else 1)

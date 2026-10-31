"""Bounded, read-only functional hierarchy projected from the existing library index."""

import hashlib
import json
import math
from dataclasses import asdict, dataclass

from catia_manual.core.catalog import ManualCatalog
from catia_manual.core.library_contract import DocumentRef, SourceLocation, relative_path
from catia_manual.core.structure import StructureIndex
from catia_manual.core.terminology import chinese_label

MAP_VERSION = 1
PAGE_SIZE = 20
SECTION_NAMES = {
    "User Tasks": "用户任务",
    "Reference Information": "参考信息",
    "Getting Started": "快速入门",
    "Overview": "概览",
    "Workbench Description": "工作台说明",
    "What's New?": "新增功能",
}


def map_label(title):
    return chinese_label(title) or SECTION_NAMES.get(title, title)


def map_request(value=None):
    if value is not None and not isinstance(value, dict):
        raise ValueError("地图请求无效")
    value = value or {}
    scope = value.get("scope", {"kind": "all"})
    if not isinstance(scope, dict) or scope.get("kind") not in {"all", "category", "manual"}:
        raise ValueError("地图范围无效")
    request = {"scope": {"kind": scope["kind"], "id": str(scope.get("id", ""))}}
    for key in ("focus", "branch"):
        item = value.get(key)
        if item:
            if not isinstance(item, dict):
                raise ValueError("地图位置无效")
            trail = item.get("trail", [])
            if (
                not isinstance(trail, list)
                or len(trail) > 100
                or not all(isinstance(t, str) for t in trail)
            ):
                raise ValueError("地图标题路径无效")
            request[key] = {
                "path": relative_path(item.get("path", "")),
                "manual": str(item.get("manual", "")),
                "fragment": str(item.get("fragment", "")),
                "trail": trail,
            }
    for key in ("expanded",):
        if key in value:
            items = value[key]
            if (
                not isinstance(items, list)
                or len(items) > 200
                or not all(isinstance(n, str) for n in items)
            ):
                raise ValueError("地图展开状态无效")
            request[key] = list(dict.fromkeys(items))
    offsets = value.get("offsets", {})
    if (
        not isinstance(offsets, dict)
        or len(offsets) > 200
        or any(
            not isinstance(k, str) or type(n) is not int or n < 0 or n > 100000
            for k, n in offsets.items()
        )
    ):
        raise ValueError("地图分页无效")
    request["offsets"] = offsets
    limit = value.get("limit", 80)
    if type(limit) is not int or not 12 <= limit <= 200:
        raise ValueError("地图节点上限须为 12–200")
    request["limit"] = limit
    return request


@dataclass
class GraphSnapshot:
    library_id: str
    graph_revision: str
    root: str
    selected: str
    nodes: list[dict]
    edges: list[dict]
    request: dict
    expanded: list[str]
    truncated: bool = False
    notice: str = ""
    parent: dict | None = None
    schema_version: int = MAP_VERSION
    view: str = "function-map"

    def to_dict(self):
        return asdict(self)


class GraphService:
    def __init__(self, index, catalog=None):
        self.index, self.db = index, index.connection
        self.catalog = catalog or ManualCatalog(index.root)
        self.structure = StructureIndex(index)
        self.structured = self.structure.ready()
        catalog_key = [
            (c.id, c.label, [(m.id, m.label) for m in c.manuals]) for c in self.catalog.categories
        ]
        self.revision = hashlib.sha256(
            json.dumps(
                [MAP_VERSION, index.meta("structure_context", ""), catalog_key], ensure_ascii=False
            ).encode()
        ).hexdigest()
        self.prefix = index.library_id
        self.nodes = {}
        self._doc_sources = {}
        self._descriptions = {}
        self.allowed = set(self.catalog.manuals)
        self.catalog_evidence = None
        if self.catalog.source:
            path = self.index.root / self.catalog.source
            self.catalog_evidence = SourceLocation(
                DocumentRef(self.prefix, self.catalog.source),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            ).to_dict()

    def source(self, path, fragment=""):
        if path not in self._doc_sources:
            row = self.db.execute(
                "SELECT content_hash FROM documents WHERE path=?", (path,)
            ).fetchone()
            self._doc_sources[path] = row[0] if row else None
        digest = self._doc_sources[path]
        return (
            SourceLocation(DocumentRef(self.prefix, path), digest, fragment=fragment).to_dict()
            if digest
            else None
        )

    def identity(self, kind, key=""):
        return (
            f"{kind}:{self.prefix}:"
            + (self.revision[:16] + ":" if kind == "placement" else "")
            + key
        )

    def make_node(self, kind, key, title, zh="", path="", fragment="", placement=None):
        identity = self.identity(kind, key)
        ref = DocumentRef(self.prefix, path) if path else None
        if path and self.structured and path not in self._descriptions:
            row = self.db.execute(
                "SELECT summary FROM article_metadata WHERE path=? AND role!='index'", (path,)
            ).fetchone()
            self._descriptions[path] = row[0] if row else ""
        node = {
            "id": identity,
            "kind": kind,
            "label_en": title,
            "label_zh": zh,
            "document_ref": asdict(ref) if ref else None,
            "document_id": ref.document_id if ref else "",
            "source": self.source(path, fragment) if path else None,
            "summary": self._descriptions.get(path, ""),
            "placement_context": placement,
            "fragment": fragment,
            "children": 0,
            "visible_children": 0,
            "offset": 0,
            "page_size": 0,
            "has_more": False,
        }
        self.nodes[identity] = node
        return node

    def root_node(self):
        return self.make_node("library", "", "CATIA V5", "功能地图")

    def category_node(self, category):
        return self.make_node("category", category.id, category.title, category.label)

    def manual_node(self, manual):
        return self.make_node(
            "manual",
            manual.id,
            manual.title,
            manual.label,
            manual.entry,
            placement={"manual": manual.id},
        )

    def placement_node(self, row):
        placement = dict(row)
        if isinstance(placement["trail"], str):
            placement["trail"] = json.loads(placement["trail"])
        node = self.make_node(
            "placement",
            placement["id"],
            placement["title"],
            map_label(placement["title"]),
            placement["path"],
            placement["fragment"],
            placement,
        )
        return node

    def descriptor(self, node):
        ref = node.get("document_ref")
        if not ref:
            return None
        return {
            "path": ref["path"],
            "fragment": node["fragment"],
            "manual": (node["placement_context"] or {}).get("manual", ""),
            "trail": (node["placement_context"] or {}).get("trail", []),
        }

    def locate(self, focus):
        """Resolve a portable placement descriptor, never an obsolete ordinal-derived ID."""
        if not focus:
            return [], ""
        path = relative_path(focus["path"])
        manual = focus.get("manual", "")
        positions = self.structure.placements(path, [manual]) if self.structured else []
        positions = [p for p in positions if p["manual"] in self.allowed]
        if manual:
            positions = [p for p in positions if p["manual"] == manual]
        exact = [p for p in positions if p["fragment"] == focus.get("fragment", "")]
        positions = exact or positions
        trail = focus.get("trail", [])
        if trail:
            positions = [p for p in positions if p["trail"] == trail]
        selected = positions[0] if positions else None
        manual = selected["manual"] if selected else manual
        if not manual:
            manual = next((m.id for m in self.catalog.manuals.values() if m.entry == path), "")
        if manual not in self.allowed:
            return [], "此页不在当前功能范围"
        category = next(
            c for c in self.catalog.categories if any(m.id == manual for m in c.manuals)
        )
        chain = [
            self.root_node(),
            self.category_node(category),
            self.manual_node(self.catalog.manuals[manual]),
        ]
        seen, ancestors = set(), []
        while selected and selected["id"] not in seen and len(ancestors) < 100:
            seen.add(selected["id"])
            ancestors.append(self.placement_node(selected))
            selected = (
                self.db.execute(
                    "SELECT * FROM manual_placements WHERE id=? AND manual=?",
                    (selected["parent"], manual),
                ).fetchone()
                if selected["parent"]
                else None
            )
        chain.extend(reversed(ancestors))
        if selected:
            return chain[:3], "目录存在循环或层级过深"
        is_entry = self.catalog.manuals[manual].entry == path
        return chain, "" if ancestors or is_entry else "此页未编入功能目录"

    def children(self, node, offset=0, limit=PAGE_SIZE):
        kind = node["kind"]
        if kind == "library":
            values = self.catalog.categories
            return len(values), [self.category_node(c) for c in values[offset : offset + limit]]
        if kind == "category":
            key = node["id"].rsplit(":", 1)[-1]
            values = self.catalog.category(key).manuals
            return len(values), [self.manual_node(m) for m in values[offset : offset + limit]]
        if not self.structured:
            return 0, []
        placement = node["placement_context"] or {}
        manual = placement.get("manual", "")
        parent = placement.get("id", "") if kind == "placement" else ""
        count = self.db.execute(
            "SELECT count(*) FROM manual_placements WHERE manual=? AND parent=?", (manual, parent)
        ).fetchone()[0]
        rows = (
            self.db.execute(
                "SELECT * FROM manual_placements WHERE manual=? AND parent=? "
                "ORDER BY position LIMIT ? OFFSET ?",
                (manual, parent, limit, offset),
            )
            if limit
            else []
        )
        return count, [self.placement_node(row) for row in rows]

    def map(self, value=None, *, reveal=True):
        request = map_request(value)
        self.allowed = set(self.catalog.manuals)
        scope = request["scope"]
        if scope["kind"] == "category":
            category = self.catalog.category(scope["id"])
            if category is None:
                raise ValueError("功能分类不存在")
            self.allowed = {m.id for m in category.manuals}
            root = self.category_node(category)
        elif scope["kind"] == "manual":
            manual = self.catalog.manuals.get(scope["id"])
            if manual is None:
                raise ValueError("功能手册不存在")
            self.allowed = {manual.id}
            root = self.manual_node(manual)
        else:
            root = self.root_node()
        chain, notice = self.locate(request.get("focus"))
        parent = None
        if request.get("branch"):
            branch, branch_notice = self.locate(request["branch"])
            if branch:
                root = branch[-1]
                parent = self.descriptor(branch[-2]) if len(branch) > 1 else None
            else:
                notice = branch_notice
        root_index = next((i for i, n in enumerate(chain) if n["id"] == root["id"]), None)
        chain = chain[root_index:] if root_index is not None else [root]
        selected = chain[-1]["id"]
        if len(chain) > request["limit"]:
            chain, selected, notice = [root], root["id"], "目录层级过深，请逐级展开"
        pinned = chain if reveal else [root]
        visible = {node["id"]: node for node in pinned}
        parents = {chain[i]["id"]: chain[i - 1]["id"] for i in range(1, len(pinned))}
        expanded = set(request.get("expanded", [root["id"]]))
        if reveal:
            expanded.update(n["id"] for n in chain[:-1])
        queue = [n for n in pinned if n["id"] in expanded]
        # The first manual view starts with its two nearest hierarchy levels.
        auto = "expanded" not in request and not request.get("focus") and root["kind"] == "manual"
        visited = set()
        while queue:
            node = queue.pop(0)
            if node["id"] in visited:
                continue
            visited.add(node["id"])
            offset = request["offsets"].get(node["id"], 0)
            capacity = request["limit"] - len(visible)
            total, children = self.children(node, offset, min(PAGE_SIZE, max(0, capacity)))
            node["children"], node["offset"] = total, offset
            node["page_size"] = len(children)
            for child in children:
                if child["id"] in visible:
                    continue
                if len(visible) >= request["limit"]:
                    break
                visible[child["id"]] = child
                parents[child["id"]] = node["id"]
                if auto and node["id"] == root["id"]:
                    expanded.add(child["id"])
                if child["id"] in expanded:
                    queue.append(child)
        # Pinning the focus chain must not be overwritten by child-node factory calls.
        for node in visible.values():
            node["children"] = self.children(node, limit=0)[0]
            node["visible_children"] = sum(
                parent_id == node["id"] for parent_id in parents.values()
            )
            node["has_more"] = node["children"] > node["visible_children"]
        edges = []
        for target, source in parents.items():
            child = visible[target]
            placement = child["placement_context"] or {}
            manual = self.catalog.manuals.get(placement.get("manual"))
            evidence = (
                (self.source(manual.toc) if manual and manual.toc else None)
                if child["kind"] == "placement"
                else self.catalog_evidence
            )
            edges.append(
                {
                    "id": source + "/" + target,
                    "source": source,
                    "target": target,
                    "kind": "contains",
                    "provenance": "catalog",
                    "evidence": [evidence] if evidence else [],
                }
            )
        request["expanded"] = sorted(expanded)
        if selected not in visible:
            selected = root["id"]
        return GraphSnapshot(
            self.prefix,
            self.revision,
            root["id"],
            selected,
            list(visible.values()),
            edges,
            request,
            request["expanded"],
            any(n["has_more"] and n["id"] in expanded for n in visible.values()),
            notice or ("目录待整理" if not self.structured else ""),
            parent,
        )

    def validate_view(self, state):
        if not isinstance(state, dict):
            raise ValueError("地图状态无效")
        if state.get("library_id") != self.prefix or state.get("schema_version") != MAP_VERSION:
            raise ValueError("地图状态属于另一文档库或版本")
        request = map_request(state["request"])
        view = state.get("viewport", {})
        if not isinstance(view, dict):
            raise ValueError("地图视口无效")
        if any(
            type(view.get(k, d)) not in (int, float)
            or not math.isfinite(view.get(k, d))
            or abs(view.get(k, d)) > 1000000
            for k, d in (("x", 0), ("y", 0), ("zoom", 1))
        ):
            raise ValueError("地图视口无效")
        positions = state.get("positions", {})
        if (
            not isinstance(positions, dict)
            or len(positions) > 200
            or any(
                not isinstance(k, str)
                or not isinstance(v, list)
                or len(v) != 2
                or any(
                    type(n) not in (int, float) or not math.isfinite(n) or abs(n) > 1000000
                    for n in v
                )
                for k, v in positions.items()
            )
        ):
            raise ValueError("地图布局无效")
        if not isinstance(state.get("selected", ""), str):
            raise ValueError("地图选中项无效")
        terms = state.get("search_terms", [])
        if (
            not isinstance(terms, list)
            or len(terms) > 500
            or not all(isinstance(t, str) and len(t) <= 500 for t in terms)
        ):
            raise ValueError("地图高亮词无效")
        payload = {
            k: state[k]
            for k in (
                "schema_version",
                "library_id",
                "graph_revision",
                "selected",
                "positions",
                "viewport",
                "search_terms",
            )
            if k in state
        }
        return {**payload, "request": request}

    def save_view(self, state):
        payload = self.validate_view(state)
        text = json.dumps(payload, ensure_ascii=False)
        if len(text) > 120000:
            raise ValueError("地图状态过大")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS map_view_states("
            "name TEXT PRIMARY KEY,payload TEXT NOT NULL)"
        )
        scope = payload["request"]["scope"]
        self.db.execute(
            "INSERT OR REPLACE INTO map_view_states VALUES(?,?)",
            (scope["kind"] + ":" + scope["id"], text),
        )
        self.db.commit()

    def load_view(self, scope):
        if not self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='map_view_states'"
        ).fetchone():
            return None
        row = self.db.execute(
            "SELECT payload FROM map_view_states WHERE name=?",
            (scope["kind"] + ":" + str(scope.get("id", "")),),
        ).fetchone()
        try:
            state = json.loads(row[0]) if row else None
            if (
                state
                and state["library_id"] == self.prefix
                and state["schema_version"] == MAP_VERSION
            ):
                return self.validate_view(state)
        except (ValueError, KeyError, TypeError):
            pass
        return None

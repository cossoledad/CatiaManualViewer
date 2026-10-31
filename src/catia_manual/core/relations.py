"""Evidence-backed local page neighborhoods; independent of Qt and inference runtimes."""

import hashlib
import json
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from catia_manual.core.function_map import GraphService, GraphSnapshot, map_label
from catia_manual.core.library_contract import DocumentRef, SourceLocation, relative_path
from catia_manual.core.parser import STATIC_CALL, ManualParser
from catia_manual.core.paths import DOCUMENT_EXTENSIONS, resolve_link
from catia_manual.core.resources import IMAGE_EXTENSIONS
from catia_manual.core.source import EXCLUDED, extract_blocks, source_for_block
from catia_manual.core.terminology import commands, vocabulary

RELATION_VERSION = 1
RELATION_LABELS = {
    "references": "参考说明",
    "links_to": "引用页面",
    "documents_command": "记录命令",
    "has_sample": "示例",
    "has_image": "图片",
}


def relation_request(value):
    if not isinstance(value, dict):
        raise ValueError("页面关系请求无效")
    path = relative_path(value.get("path", ""))
    if Path(path).suffix.lower() not in DOCUMENT_EXTENSIONS:
        raise ValueError("页面关系须从手册页面开始")
    types = value.get("types", list(RELATION_LABELS))
    if not isinstance(types, list) or any(
        not isinstance(t, str) or t not in RELATION_LABELS for t in types
    ):
        raise ValueError("关系类型无效")
    result = {
        "path": path,
        "manual": str(value.get("manual", "")),
        "types": list(dict.fromkeys(types)),
    }
    for key in ("expanded",):
        values = value.get(key, [])
        if (
            not isinstance(values, list)
            or len(values) > 200
            or not all(isinstance(v, str) for v in values)
        ):
            raise ValueError("关系展开状态无效")
        result[key] = list(dict.fromkeys(values))
    offsets = value.get("offsets", {})
    if (
        not isinstance(offsets, dict)
        or len(offsets) > 200
        or any(
            not isinstance(k, str) or type(v) is not int or not 0 <= v <= 100000
            for k, v in offsets.items()
        )
    ):
        raise ValueError("关系分页无效")
    result["offsets"] = offsets
    limit = value.get("limit", 80)
    if type(limit) is not int or not 12 <= limit <= 200:
        raise ValueError("关系节点上限须为 12–200")
    result["limit"] = limit
    return result


class RelationService:
    def __init__(self, index, catalog=None):
        self.index, self.db = index, index.connection
        self.graph = GraphService(index, catalog)
        self.parser = ManualParser(index.root)
        self.documents = {}
        self.rows = {}
        self.nodes = {}
        self.extractions = {}

    def identity(self, kind, value):
        digest = hashlib.sha256(value.encode()).hexdigest()
        return f"{kind}:{self.index.library_id}:{digest}"

    def row(self, path):
        if path not in self.rows:
            sql = "SELECT d.title,d.kind,d.content_hash"
            if self.graph.structured:
                sql += ",m.role,m.summary FROM documents d "
                sql += "LEFT JOIN article_metadata m ON m.path=d.path"
            else:
                sql += ",'' AS role,'' AS summary FROM documents d"
            self.rows[path] = self.db.execute(sql + " WHERE d.path=?", (path,)).fetchone()
        return self.rows[path]

    def node(self, kind, value, *, fragment="", label=""):
        identity = self.identity(kind, value)
        if identity in self.nodes:
            return self.nodes[identity]
        ref = DocumentRef(self.index.library_id, value) if kind != "command" else None
        source, summary, title, zh, role = None, "", label, "", ""
        exists = True
        if kind == "article":
            row = self.row(value)
            doc = self.documents.get(value)
            title = doc.title if doc else (row["title"] if row else label or Path(value).stem)
            digest = doc.content_hash if doc else (row["content_hash"] if row else None)
            if digest:
                source = SourceLocation(ref, digest, fragment=fragment).to_dict()
            summary, role = (row["summary"] or "", row["role"] or "") if row else ("", "")
            zh = map_label(title)
            exists = self.parser.source(value).is_file()
        elif kind == "command":
            entry = commands()[value]
            title, zh = entry["en"][0], entry["zh"][0]
            summary = entry.get("description_zh") or entry.get("description_en", "")
        else:
            title = label or Path(value).name
            zh = Path(value).name
            exists = self.parser.source(value).is_file()
        node = {
            "id": identity,
            "kind": kind,
            "label_en": title,
            "label_zh": zh,
            "document_ref": {"library_id": ref.library_id, "path": ref.path}
            if kind == "article"
            else None,
            "document_id": ref.document_id if kind == "article" else "",
            "resource_ref": {"library_id": ref.library_id, "path": ref.path, "kind": kind}
            if kind in {"image", "sample"}
            else None,
            "command_id": value if kind == "command" else "",
            "source": source,
            "summary": summary,
            "role": role,
            "exists": exists,
            "placement_context": None,
            "fragment": fragment,
            "children": 0,
            "visible_children": 0,
            "offset": 0,
            "page_size": 0,
            "has_more": False,
        }
        self.nodes[identity] = node
        return node

    def extract(self, path):
        if path in self.extractions:
            return self.extractions[path]
        document = self.parser.parse(path)
        self.documents[path] = document
        soup, _, _ = self.parser.soup(path)
        extract_blocks(soup, document.path, annotate=True)
        by_block = {b.id: b for b in document.blocks}
        own = self.node("article", document.path)
        own["source"] = SourceLocation(
            DocumentRef(self.index.library_id, document.path), document.content_hash
        ).to_dict()
        own["label_en"], own["label_zh"] = document.title, map_label(document.title)
        result, occurrences = [], defaultdict(int)

        def evidence(tag, attribute, target, fragment="", element=None):
            marker = tag.find(attrs={"data-cmv-source-block": True})
            if marker is None:
                parent = tag.parent
                while parent and parent.name not in {"body", "html", "[document]"}:
                    marker = parent.find(attrs={"data-cmv-source-block": True})
                    if marker:
                        break
                    parent = parent.parent
            block = by_block.get(marker.get("data-cmv-source-block")) if marker else None
            if block:
                source = source_for_block(self.index.library_id, document, block)
            else:
                anchor = (
                    tag.get("id")
                    or tag.get("name")
                    or next((p.get("id") for p in tag.parents if p.get("id")), "")
                )
                source = SourceLocation(
                    DocumentRef(self.index.library_id, document.path),
                    document.content_hash,
                    fragment=anchor,
                )
            if attribute in {"id", "name"}:
                source = replace(source, fragment=target)
            occurrence = occurrences[(attribute, target, fragment)]
            occurrences[(attribute, target, fragment)] += 1
            return {
                "source": source.to_dict(),
                "attribute": attribute,
                "target": target,
                "fragment": fragment,
                "occurrence": occurrence,
                "label": " ".join(tag.get_text(" ", strip=True).split())[:180],
                "element": element,
                "article_title": document.title,
            }

        for tag in list((soup.body or soup).find_all(True)):
            if tag.name in EXCLUDED or any(p.name in EXCLUDED for p in tag.parents):
                continue
            for attribute in ("id", "name"):
                command = tag.get(attribute)
                if command in commands():
                    result.append(
                        (
                            own,
                            self.node("command", command),
                            "documents_command",
                            evidence(tag, attribute, command),
                        )
                    )
                    break
            if tag.name == "a":
                href = str(tag.get("href", ""))
                match = STATIC_CALL.search(str(tag.get("onclick", "")))
                if match and (not href or href == "#" or href.lower().startswith("javascript:")):
                    href = match.group(2)
                if not href:
                    continue
                link = resolve_link(self.index.root, self.parser.source(path), href)
                if not link.target or link.kind in {"blocked", "external"}:
                    continue
                label = " ".join(tag.get_text(" ", strip=True).split())
                kind = (
                    "article"
                    if link.kind == "document"
                    else "sample"
                    if link.kind == "sample"
                    else "image"
                    if Path(link.target).suffix.lower() in IMAGE_EXTENSIONS
                    else ""
                )
                if not kind or (kind == "article" and link.target == document.path):
                    continue
                row = self.row(link.target) if kind == "article" else None
                if row and (row["role"] == "index" or row["kind"] != "article"):
                    continue
                target = self.node(kind, link.target, fragment=link.fragment, label=label)
                relation = (
                    "references"
                    if target["role"] in {"reference", "toolbar"}
                    else "links_to"
                    if kind == "article"
                    else "has_" + kind
                )
                result.append(
                    (own, target, relation, evidence(tag, "href", link.target, link.fragment))
                )
            elif tag.name == "img" and tag.get("src"):
                link = resolve_link(self.index.root, self.parser.source(path), str(tag["src"]))
                if (
                    link.target
                    and link.kind != "blocked"
                    and Path(link.target).suffix.lower() in IMAGE_EXTENSIONS
                ):
                    occurrence = occurrences[("src", link.target, "")]
                    element = {"kind": "image", "path": link.target, "occurrence": occurrence}
                    result.append(
                        (
                            own,
                            self.node("image", link.target, label=str(tag.get("alt", ""))),
                            "has_image",
                            evidence(tag, "src", link.target, element=element),
                        )
                    )
        self.extractions[path] = result
        return result

    def neighbors(self, node, types):
        if node["kind"] == "article":
            if not node["exists"]:
                return []
            edges = self.extract(node["document_ref"]["path"])
            return [(b, a, b, kind, proof) for a, b, kind, proof in edges if kind in types]
        if node["kind"] == "command" and "documents_command" in types and self.graph.structured:
            rows = self.db.execute(
                "SELECT path FROM article_commands WHERE command=? ORDER BY path LIMIT 201",
                (node["command_id"],),
            )
            result = []
            for row in rows:
                if not self.parser.source(row[0]).is_file():
                    continue
                for a, b, kind, proof in self.extract(row[0]):
                    if kind == "documents_command" and b["id"] == node["id"]:
                        result.append((a, a, b, kind, proof))
            return result
        return []

    def map(self, value):
        request = relation_request(value)
        self.extract(request["path"])
        root = self.node("article", request["path"])
        expanded = set(request["expanded"]) | {root["id"]}
        visible, edges, seen_edges = {root["id"]: root}, [], {}
        queue, visited = [root], set()
        while queue:
            current = queue.pop(0)
            if current["id"] in visited:
                continue
            visited.add(current["id"])
            merged = {}
            for neighbor, a, b, kind, proof in self.neighbors(current, request["types"]):
                key = (a["id"], b["id"], kind)
                merged.setdefault(
                    key, {"neighbor": neighbor, "a": a, "b": b, "kind": kind, "evidence": []}
                )["evidence"].append(proof)
            # Interleave relation types so page links cannot hide commands or resources.
            groups = {kind: [] for kind in RELATION_LABELS}
            for entry in merged.values():
                groups[entry["kind"]].append(entry)
            ordered = []
            while any(groups.values()):
                for entries in groups.values():
                    if entries:
                        ordered.append(entries.pop(0))
            current["children"] = len(ordered)
            current["children_known"] = True
            current["offset"] = request["offsets"].get(current["id"], 0)
            page = ordered[current["offset"] : current["offset"] + 20]
            consumed = 0
            for entry in page:
                if len(edges) >= min(400, request["limit"] * 3):
                    break
                neighbor = entry["neighbor"]
                new = neighbor["id"] not in visible
                if new and len(visible) >= request["limit"]:
                    break
                if new:
                    visible[neighbor["id"]] = neighbor
                key = (entry["a"]["id"], entry["b"]["id"], entry["kind"])
                if key not in seen_edges:
                    edge = {
                        "id": hashlib.sha256("\n".join(key).encode()).hexdigest(),
                        "source": key[0],
                        "target": key[1],
                        "kind": key[2],
                        "label": RELATION_LABELS[key[2]],
                        "provenance": "document",
                        "evidence": entry["evidence"][:20],
                        "evidence_count": len(entry["evidence"]),
                        "layout": new,
                        "layout_source": current["id"],
                        "layout_target": neighbor["id"],
                    }
                    seen_edges[key] = edge
                    edges.append(edge)
                consumed += 1
                if neighbor["id"] in expanded and neighbor["id"] not in visited:
                    queue.append(neighbor)
            current["page_size"] = consumed
            current["visible_children"] = consumed
            current["has_more"] = current["offset"] + consumed < len(ordered)
        for node in visible.values():
            if (
                node["id"] not in visited
                and node["kind"] in {"article", "command"}
                and node["exists"]
            ):
                node["children"] = 1  # Disclosure affordance; exact count is queried on expansion.
                node["children_known"] = False
            if node["kind"] == "article":
                placements = (
                    self.graph.structure.placements(
                        node["document_ref"]["path"], [request["manual"]]
                    )
                    if self.graph.structured
                    else []
                )
                node["placement_context"] = (
                    placements[0] if placements else {"manual": request["manual"]}
                )
        revision = hashlib.sha256(
            json.dumps(
                [
                    RELATION_VERSION,
                    self.documents[request["path"]].content_hash,
                    vocabulary()["revision"],
                    self.index.meta("manifest_hash", ""),
                ]
            ).encode()
        ).hexdigest()
        request["expanded"] = [root["id"]] + sorted(expanded - {root["id"]})[:199]
        root["disclosure"] = False
        root["current_label"] = "中心页面"
        return GraphSnapshot(
            self.index.library_id,
            revision,
            root["id"],
            root["id"],
            list(visible.values()),
            edges,
            request,
            request["expanded"],
            any(n["has_more"] for n in visible.values()),
            view="page-relations",
        )

    def validate_view(self, state):
        request = relation_request(state["request"])
        # Share geometry/library validation with the functional map, keeping the request separate.
        proxy = {**state, "request": {"scope": {"kind": "all"}}}
        return {**self.graph.validate_view(proxy), "request": request}

    def save_view(self, state):
        state = self.validate_view(state)
        text = json.dumps(state, ensure_ascii=False)
        if len(text) > 120000:
            raise ValueError("关系图状态过大")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS relation_view_states("
            "path TEXT PRIMARY KEY,payload TEXT NOT NULL)"
        )
        self.db.execute(
            "INSERT OR REPLACE INTO relation_view_states VALUES(?,?)",
            (state["request"]["path"], text),
        )
        self.db.commit()

    def load_view(self, path):
        if not self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='relation_view_states'"
        ).fetchone():
            return None
        row = self.db.execute(
            "SELECT payload FROM relation_view_states WHERE path=?", (relative_path(path),)
        ).fetchone()
        try:
            return self.validate_view(json.loads(row[0])) if row else None
        except (ValueError, TypeError, KeyError):
            return None

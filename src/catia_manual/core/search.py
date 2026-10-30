import math
from dataclasses import dataclass, replace

from catia_manual.core.embedding import Cancelled, check_cancel
from catia_manual.core.index import ManualIndex
from catia_manual.core.models import SearchHit
from catia_manual.core.query import CJK, ENGLISH_STOPWORDS, QueryPlan, phrase, plan_query, words
from catia_manual.core.translation import TranslationService, make_provider


@dataclass
class SearchResponse:
    hits: list[SearchHit]
    plan: QueryPlan


class SearchService:
    def __init__(self, index: ManualIndex, translation_config: dict, search_config=None):
        self.index, self.translation_config = index, translation_config
        self.search_config = search_config or {"mode": "keyword"}

    def search(
        self,
        query: str,
        module: str | None = None,
        limit: int = 30,
        *,
        modules: list[str] | tuple[str, ...] | None = None,
        cancel=None,
    ) -> SearchResponse:
        mode = self.search_config.get("mode", "keyword")
        if mode not in {"keyword", "hybrid", "semantic"}:
            raise ValueError("未知搜索模式")
        if modules is not None and not modules:
            return SearchResponse([], plan_query(query))
        response = (
            SearchResponse([], plan_query(query))
            if mode == "semantic"
            else self.keyword_search(
                query,
                module,
                max(limit, 80) if mode == "hybrid" else limit,
                modules=modules,
                translate=mode == "keyword",
                partial=mode == "hybrid",
            )
        )
        if mode == "keyword" or not query.strip():
            return response
        try:
            from catia_manual.core.embedding import make_embedder
            from catia_manual.core.semantic import SemanticIndex, fuse

            check_cancel(cancel)
            embedder = make_embedder(self.search_config)
            semantic, status = SemanticIndex(self.index).search(
                query[:500],
                embedder,
                modules=modules if modules is not None else ([module] if module else None),
                limit=max(limit, 80),
                cancel=cancel,
            )
            response.hits = (
                fuse(response.hits, semantic, limit) if mode == "hybrid" else semantic[:limit]
            )
            response.plan.method += "+" + mode
            if semantic:
                response.plan.notes = [
                    note for note in response.plan.notes if not note.startswith("未覆盖词语")
                ]
            if status["state"] == "stale":
                response.plan.notes.append("文档已变化，请更新语义索引")
            elif status["state"] != "ready":
                response.plan.notes.append("语义索引部分完成，可继续建立索引")
        except Cancelled:
            return SearchResponse([], response.plan)
        except Exception as exc:
            if mode == "semantic":
                response = self.keyword_search(
                    query, module, limit, modules=modules, translate=False
                )
            response.plan.notes.append(f"语义检索未就绪，使用关键词结果：{exc}")
        response.hits = response.hits[:limit]
        return response

    def keyword_search(
        self, query, module=None, limit=30, *, modules=None, translate=True, partial=False
    ):
        plan = plan_query(query, self.index.aliases(modules or ([module] if module else None)))
        if plan.unknown:
            if not translate or self.translation_config.get("provider", "glossary") == "glossary":
                plan.notes.append(f"未覆盖词语：{plan.unknown}；配置翻译服务可扩展查询")
            else:
                try:
                    service = TranslationService(self.index, make_provider(self.translation_config))
                    translated = service.translate(plan.unknown, "en").text
                    tokens = [
                        word for word in words(translated) if word.lower() not in ENGLISH_STOPWORDS
                    ]
                    if tokens and not CJK.search(translated):
                        extra = " ".join(tokens)
                        plan.match = " AND ".join(filter(None, [plan.match, phrase(extra)]))
                        plan.expanded = " + ".join(filter(None, [plan.expanded, extra]))
                        plan.method = "terminology+translation"
                        plan.unknown = ""
                        plan.groups.append(phrase(extra))
                    else:
                        plan.notes.append("查询翻译未生成可用英文关键词")
                except Exception as exc:
                    plan.notes.append(f"查询翻译不可用：{exc}")
        # Do not silently ignore unknown concepts and return a misleading partial search.
        hits = (
            []
            if plan.unknown
            else self.index.search(query, module, limit, match=plan.match, modules=modules)
        )
        if plan.unknown and partial and plan.match:
            hits = [
                replace(hit, reason="已识别术语匹配（部分词语未展开） · " + hit.reason)
                for hit in self.index.search(
                    query, module, limit, match=plan.match, modules=modules
                )
            ]
            if hits:
                plan.notes.append("关键词通道匹配已识别术语；语义通道使用完整问题")
        if not hits and not plan.unknown and 1 < len(plan.groups) <= 8:
            paths = self.index.document_candidates(plan.match, module, modules, limit)
            if paths:
                hits = self.index.search(
                    query,
                    module,
                    limit,
                    match=" OR ".join("(" + g + ")" for g in plan.groups),
                    modules=modules,
                    paths=paths,
                )
                plan.method += "+cross-section"
                hits = [replace(hit, reason=hit.reason + " · 跨章节匹配") for hit in hits]
                plan.notes.append("全部关键词在文章内命中，打开最相关章节")
        if not hits and not plan.unknown and 1 < len(plan.groups) <= 8:
            # Explicit partial fallback; keep evidence and scope for every contributing concept.
            candidates, counts = {}, {}
            for group in plan.groups:
                for hit in self.index.search(query, module, 200, match=group, modules=modules):
                    candidates.setdefault(hit.path, hit)
                    counts[hit.path] = counts.get(hit.path, 0) + 1
            minimum = max(2, math.ceil(len(plan.groups) * 0.6))
            hits = [
                candidates[path]
                for path in sorted(candidates, key=lambda p: (-counts[p], candidates[p].score, p))
                if counts[path] >= minimum
            ][:limit]
            if hits:
                hits = [
                    replace(
                        hit,
                        reason=hit.reason + f" · 部分概念 {counts[hit.path]}/{len(plan.groups)}",
                    )
                    for hit in hits
                ]
                plan.method += "+partial"
                plan.notes.append(
                    "关键词未在同一章节同时命中；以下为文章内至少匹配 "
                    + str(minimum)
                    + " 组概念的结果"
                )
        if not hits and not plan.unknown and not CJK.search(query):
            plan.suggestions = self.index.suggestions(plan.expanded)
            if plan.suggestions:
                corrected = plan_query(plan.suggestions[0])
                hits = self.index.search(
                    query, module, limit, match=corrected.match, modules=modules
                )
                if hits:
                    plan.method += "+spelling"
                    plan.notes.append("按拼写建议检索：" + plan.suggestions[0])
        return SearchResponse(hits, plan)

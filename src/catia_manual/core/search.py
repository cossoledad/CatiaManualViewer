from dataclasses import dataclass

from catia_manual.core.index import ManualIndex
from catia_manual.core.models import SearchHit
from catia_manual.core.query import CJK, ENGLISH_STOPWORDS, QueryPlan, phrase, plan_query, words
from catia_manual.core.translation import TranslationService, make_provider


@dataclass
class SearchResponse:
    hits: list[SearchHit]
    plan: QueryPlan


class SearchService:
    def __init__(self, index: ManualIndex, translation_config: dict):
        self.index, self.translation_config = index, translation_config

    def search(self, query: str, module: str | None = None, limit: int = 30) -> SearchResponse:
        plan = plan_query(query)
        if plan.unknown:
            if self.translation_config.get("provider", "glossary") == "glossary":
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
                    else:
                        plan.notes.append("查询翻译未生成可用英文关键词")
                except Exception as exc:
                    plan.notes.append(f"查询翻译不可用：{exc}")
        # Do not silently ignore unknown concepts and return a misleading partial search.
        hits = [] if plan.unknown else self.index.search(query, module, limit, match=plan.match)
        return SearchResponse(hits, plan)

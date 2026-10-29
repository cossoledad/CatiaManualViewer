from dataclasses import replace

import pytest
from bs4 import BeautifulSoup

from catia_manual.core.index import ManualIndex
from catia_manual.core.page_translation import PageTranslationService, PageTranslationTask, chunks
from catia_manual.core.parser import ManualParser
from catia_manual.core.translation import GlossaryProvider

PAGE = "online/prtug_C2/pad.htm"


class Provider:
    name = "test-page-v1"

    def __init__(self):
        self.calls = []

    def translate(self, text, target):
        self.calls.append(text)
        return "译文：" + text


def test_reading_units_have_one_dom_target_and_keep_assets(corpus):
    page = ManualParser(corpus).render_page(PAGE)
    soup = BeautifulSoup(page.html, "lxml")
    assert page == ManualParser(corpus).render_page(PAGE)
    assert [node["data-unit"] for node in soup.select(".cmv-unit")] == [u.id for u in page.units]
    assert len({u.id for u in page.units}) == len(page.units)
    assert soup.find("p", id="step").select_one(".cmv-source")
    assert soup.find("img")["src"].endswith("/pad.gif")
    assert soup.find("a", string="Example")["href"].endswith("Pad%201.CATPart")
    assert len(soup.select("table tr td")) == 1
    assert not any(node.find_parent("span", class_="cmv-unit") for node in soup.select(".cmv-unit"))


def test_code_is_not_a_page_translation_task(corpus):
    path = corpus / PAGE
    path.write_text(
        "<p>Click <code>PadFactory.Create()</code> to continue.</p>"
        '<pre>DoNotTranslate()</pre><p><a name="target"></a>Next step.</p>'
    )
    page = ManualParser(corpus).render_page(PAGE)
    assert all("DoNotTranslate" not in unit.text for unit in page.units)
    assert "PadFactory.Create()" not in page.units[0].text
    soup = BeautifulSoup(page.html, "lxml")
    assert soup.code.get_text() == "PadFactory.Create()"
    assert soup.find("a", attrs={"name": "target"}) is not None


def test_cache_survives_new_service_and_is_scoped_to_source_provider(corpus):
    page = ManualParser(corpus).render_page(PAGE)
    database = corpus / "data/db.sqlite3"
    provider = Provider()
    with ManualIndex(database, corpus) as index:
        service = PageTranslationService(index, provider, page)
        assert not service.translate(page.units[0]).cached
    with ManualIndex(database, corpus) as index:
        service = PageTranslationService(index, provider, page)
        assert service.translate(page.units[0]).cached
        assert service.cached()[page.units[0].id].cached
        assert len(provider.calls) == 1
        changed = replace(page, content_hash="changed")
        assert not PageTranslationService(index, provider, changed).cached()
        other = Provider()
        other.name = "different-model"
        assert not PageTranslationService(index, other, page).cached()
        assert not PageTranslationService(index, provider, page, "en").cached()


def test_partial_failure_is_not_cached_and_retry_keeps_completed_units(corpus):
    page = ManualParser(corpus).render_page(PAGE)
    task = PageTranslationTask(page, "bilingual")
    first, second = page.units[:2]
    task.errors[first.id] = "HTTP 429"
    assert task.next_unit([first.id, second.id]) == second
    task.paused = True
    assert task.next_unit() is None
    task.retry(first.id)
    assert task.next_unit([first.id]) == first
    with ManualIndex(corpus / "data/db.sqlite3", corpus) as index:
        task.results[first.id] = PageTranslationService(index, Provider(), page).translate(first)
    task.errors[second.id] = "timeout"
    task.retry()
    assert first.id in task.results and not task.errors
    task.mode = "original"
    assert task.next_unit() is None


def test_requests_are_bounded_and_glossary_is_rejected(corpus):
    values = list(chunks("A sentence ends here. " * 300))
    assert len(values) > 1 and max(map(len, values)) <= 1000
    assert " ".join(values) == ("A sentence ends here. " * 300).strip()
    assert list(chunks("x" * 2500)) == ["x" * 1000, "x" * 1000, "x" * 500]
    with ManualIndex(corpus / "data/db.sqlite3", corpus) as index:
        with pytest.raises(ValueError, match="整页翻译"):
            PageTranslationService(
                index, GlossaryProvider(), ManualParser(corpus).render_page(PAGE)
            )


def test_consecutive_service_failures_pause_remaining_requests(corpus):
    task = PageTranslationTask(ManualParser(corpus).render_page(PAGE), "bilingual")
    for unit in task.page.units[:3]:
        task.fail(unit.id, "unavailable")
    assert task.paused and task.next_unit() is None
    task.retry()
    assert not task.errors and task.consecutive_failures == 0 and task.next_unit() is not None


def test_offline_cad_headings_use_specialized_terms_without_loading_model(tmp_path):
    from catia_manual.core.offline_translation import LocalTranslationProvider

    provider = LocalTranslationProvider(tmp_path)
    assert provider.translate("Creating Pads", "zh-CN") == "创建凸台（拉伸）"
    assert provider.translate("About Profiles", "zh-CN") == "关于轮廓"

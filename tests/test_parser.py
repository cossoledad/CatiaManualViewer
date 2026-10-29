import pytest
from bs4 import BeautifulSoup

from catia_manual.core.parser import ManualParser
from catia_manual.core.paths import resolve_link

PAGE = "online/prtug_C2/pad.htm"


def test_extracts_body_blocks_examples_and_static_script_links(corpus):
    parser = ManualParser(corpus)
    doc = parser.parse(PAGE)
    assert doc.title == "Creating Pads"
    assert "Create a pad ." in doc.text
    assert "Extrude the profile." in doc.text
    assert "Set a dimension." in doc.text
    assert "secret_noise" not in doc.text and "comment_noise" not in doc.text
    sample = next(link for link in doc.links if link.kind == "sample")
    assert sample.exists and sample.target.endswith("Pad 1.CATPart")
    legacy = next(link for link in doc.links if link.label == "Legacy next")
    assert legacy.target == "online/prtug_C2/other.htm"
    assert doc.blocks == parser.parse(PAGE).blocks


@pytest.mark.parametrize(
    "href",
    [
        "../../../../secret.txt",
        "javascript:evil()",
        "https://example.com/a",
        "file://server/share/a.CATPart",
    ],
)
def test_rejects_local_escape_and_classifies_external(corpus, href):
    link = resolve_link(corpus, corpus / PAGE, href)
    assert link.kind in {"blocked", "external"} and link.target is None


def test_source_blocks_escape(corpus):
    with pytest.raises(ValueError):
        ManualParser(corpus).parse("../secret.htm")


def test_legacy_encoding_and_frames(corpus):
    module = corpus / "online/prtug_C2"
    (module / "latin.htm").write_bytes(
        b'<meta charset="iso-8859-1"><title>Caf\xe9</title><p>Caf\xe9</p>'
    )
    (module / "frames.htm").write_text(
        '<frameset><frame src="pad.htm"><frame src="other.htm"></frameset>'
    )
    parser = ManualParser(corpus)
    assert parser.parse("online/prtug_C2/latin.htm").text == "Café"
    frames = parser.parse("online/prtug_C2/frames.htm")
    assert frames.kind == "frameset" and len(frames.links) == 2


def test_render_preserves_assets_and_removes_execution(corpus):
    soup = BeautifulSoup(ManualParser(corpus).render(PAGE), "lxml")
    assert soup.find("script") is None
    assert soup.body.get("onload") is None
    assert soup.find("img")["src"].startswith("file:///")
    assert "Pad%201.CATPart" in str(soup)
    assert soup.find("p", id="step") is not None
    assert not any(str(tag.get("href", "")).startswith("javascript:") for tag in soup.find_all("a"))
    assert soup.find("table") is not None
    legacy = soup.find("a", string="Legacy next")
    assert legacy["href"].endswith("other.htm")


def test_toc_recovers_indentation(corpus):
    entries = ManualParser(corpus).toc("online/prtug_C2/prtugsm.htm")
    assert [entry.depth for entry in entries] == [0, 1, 2]
    assert entries[1].fragment == "anchor"

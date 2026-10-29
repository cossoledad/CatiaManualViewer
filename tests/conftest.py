from pathlib import Path

import pytest


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    module = tmp_path / "online" / "prtug_C2"
    (module / "samples").mkdir(parents=True)
    (module / "samples" / "Pad 1.CATPart").write_bytes(b"fixture-only")
    (module / "images").mkdir()
    (module / "images" / "pad.gif").write_bytes(b"GIF89a")
    (module / "pad.htm").write_text(
        """<html><head><title>Creating Pads</title><script>secret_noise()</script></head>
        <body onload="evil()"><h1>Creating Pads</h1><table><tr><td>
        Create a <b>pad</b>.<p>Extrude the profile.</p>Set a dimension.
        </td></tr></table><p id="step">Select Sketch.1 and click Pad.</p>
        <a href="samples/Pad%201.CATPart">Example</a><a href="other.htm#anchor">Next</a>
        <a href="javascript:evil()">Bad</a><a href="../../../../secret.txt">Escape</a>
        <a href="#" onclick="GoToBody('other.htm')">Legacy next</a>
        <img src="images/pad.gif"><!-- comment_noise --></body></html>""",
        encoding="utf-8",
    )
    (module / "other.htm").write_text(
        '<html><title>Pocket</title><h1>Pocket</h1><p id="anchor">Cut a pocket.</p></html>',
        encoding="utf-8",
    )
    (module / "prtugsm.htm").write_text(
        """<h1>Part Design</h1><p class="sm1"><a href="pad.htm">Tasks</a></p>
        <p class="sm2" style="margin-left:25"><a href="other.htm#anchor">Pocket</a></p>
        <p class="sm2" style="margin-left:50"><a href="pad.htm">Pad</a></p>""",
        encoding="utf-8",
    )
    return tmp_path

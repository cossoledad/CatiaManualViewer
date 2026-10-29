"""Recover a narrow set of static Dassault navigation constructs without evaluating JS."""

import ast
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

CALL = re.compile(
    r"(?:javascript:)?\s*(activateLinkAnchor|activateLink|openLink|GoToBody|openwin|window\.open)"
    r"\s*\((.*?)\)\s*;?\s*(?:return false;?)?\s*$",
    re.S,
)
QUOTED = r"""(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')"""


def arguments(text: str) -> list[str] | None:
    try:
        values = ast.literal_eval("[" + text + "]")
    except (ValueError, SyntaxError):
        return None
    return values if all(isinstance(value, str) for value in values) else None


@lru_cache(maxsize=100)
def script_text(path: str, mtime_ns: int) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


class LegacyLinks:
    def __init__(self, root: Path, source: Path):
        self.root, self.source = root.resolve(), source.resolve()
        self._objects = None
        self._aliases = None

    def read(self, path: Path) -> str:
        path = path.resolve()
        if not path.is_relative_to(self.root) or not path.is_file():
            return ""
        return script_text(str(path), path.stat().st_mtime_ns)

    def objects(self):
        if self._objects is None:
            text = self.read(self.root / "online/interfaces/_index/jslink.js")
            self._objects = dict(re.findall(r'objetIdx\["([^"]+)"\]\s*=\s*"([^"]+)"', text))
            self._aliases = dict(re.findall(r'aliasTab\["([^"]+)"\]\s*=\s*"([^"]+)"', text))
        return self._objects

    def object_target(self, name: str, anchor: str = "") -> str | None:
        objects = self.objects()
        name = self._aliases.get(name, name)
        target = objects.get(name) if name else ""
        if target is None:
            return None
        return target + ("#" + quote(anchor) if anchor else "")

    def literal_call(self, code: str) -> tuple[str | None, str] | None:
        match = CALL.fullmatch(code.strip())
        if not match:
            return None
        args = arguments(match.group(2))
        if args is None:
            return None
        function = match.group(1)
        if function == "activateLinkAnchor" and len(args) == 3:
            return self.object_target(args[0], args[1]), args[2]
        if function == "activateLink" and len(args) == 2:
            return self.object_target(args[0]), args[1]
        if function == "openLink" and len(args) == 2:
            return self.object_target(*args), args[0]
        if function in {"GoToBody", "openwin", "window.open"} and args:
            return args[0], ""
        return None

    def recover(self, soup) -> None:
        page_code = "\n".join(tag.get_text() for tag in soup.find_all("script", src=False))
        for tag in list(soup.find_all("script", src=False)):
            call = self.literal_call(tag.get_text())
            if call:
                href, label = call
                replacement = soup.new_tag("a" if href else "span")
                if href:
                    replacement["href"] = href
                # Generated link labels sometimes contain formatting; retain plain text only.
                from bs4 import BeautifulSoup

                replacement.string = BeautifulSoup(label, "lxml").get_text(" ", strip=True)
                tag.replace_with(replacement)
        for tag in soup.find_all("a"):
            code = str(tag.get("href", ""))
            if not code or code == "#" or code.lower().startswith("javascript:"):
                result = self.literal_call(str(tag.get("onclick", ""))) or self.literal_call(code)
                if result and result[0]:
                    tag["href"] = result[0]
                elif re.fullmatch(
                    r"javascript:openLinkNP\(getCurrentObjectName\(\),getCurrentType\(\),'[01]'\)",
                    code,
                ):
                    target = self.adjacent(page_code, 1 if "'1'" in code else -1)
                    if target:
                        tag["href"] = target

    def adjacent(self, page_code: str, step: int) -> str | None:
        name = re.search(
            r'function\s+getCurrentObjectName\(\)\s*\{\s*return\s*"([^"]+)"', page_code
        )
        kind = re.search(r'function\s+getCurrentType\(\)\s*\{\s*return\s*"([^"]+)"', page_code)
        if not name or not kind:
            return None
        text = self.read(self.source.parent / "jsFwLink.js")
        category = kind.group(1)
        characteristic = re.search(
            r'DSCharacteristic\["' + re.escape(name.group(1)) + r'"\]\s*=\s*"([^"]+)"', text
        )
        if characteristic:
            category = characteristic.group(1)
        prefix = r'objet\["' + re.escape(category) + r'"\]'
        number = re.search(prefix + r'\["' + re.escape(name.group(1)) + r'"\]\s*=\s*(\d+)', text)
        if not number:
            return None
        target = re.search(
            prefix + r"\[" + str(int(number.group(1)) + step) + r'\]\s*=\s*"([^"]+)"', text
        )
        return target.group(1) if target else None

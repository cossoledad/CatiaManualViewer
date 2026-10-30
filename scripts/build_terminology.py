"""Developer-only extraction of bilingual UI names linked to documentation command anchors."""

import argparse
import hashlib
import json
import re
from pathlib import Path

ASSIGNMENT = re.compile(r'([\w.-]+)\s*=\s*"((?:[^"\\]|\\.)*)"\s*;', re.S)
ANCHOR = re.compile(rb'\b(?:name|id)\s*=\s*[\'"]([^\'"<>]+)[\'"]', re.I)


def catalog_strings(raw, chinese=False):
    if not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        raw = re.sub(
            rb'"(?:[^"\\]|\\.)*"|//[^\n]*|/\*.*?\*/',
            lambda m: m[0] if m[0].startswith(b'"') else b" ",
            raw,
            flags=re.S,
        )
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16")
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("gb18030" if chinese else "cp1252")
    # Remove comments outside strings, retaining escapes and newlines in string literals.
    text = re.sub(
        r'"(?:[^"\\]|\\.)*"|//[^\n]*|/\*.*?\*/',
        lambda m: m[0] if m[0].startswith('"') else " ",
        text,
        flags=re.S,
    )
    return {
        key: re.sub(
            r'\\([nrt"\\])',
            lambda m: {
                "n": "\n",
                "r": "\r",
                "t": "\t",
                '"': '"',
                "\\": "\\",
            }[m[1]],
            value,
        )
        for key, value in ASSIGNMENT.findall(text)
    }


def extract(installation, documentation):
    base = installation / "win_b64/resources/msgcatalog"
    chinese = base / "Simplified_Chinese"
    anchored, anchor_files = set(), 0
    for path in sorted(documentation.rglob("*.htm")):
        anchor_files += 1
        for match in ANCHOR.findall(path.read_bytes()):
            value = match.decode("utf-8", errors="replace")
            if re.fullmatch(r"[A-Za-z][\w.-]+\.[\w.-]+", value):
                anchored.add(value)
    entries, errors, pairs = {}, [], 0
    for path in sorted(chinese.glob("*.CATNls")):
        original = base / path.name
        if not original.is_file():
            continue
        pairs += 1
        try:
            en, zh = (
                catalog_strings(original.read_bytes()),
                catalog_strings(path.read_bytes(), True),
            )
        except UnicodeError as exc:
            errors.append({"file": path.name, "error": type(exc).__name__})
            continue
        for key in sorted(en.keys() & zh.keys()):
            if not key.endswith(".ShortHelp"):
                continue
            identity = key.removesuffix(".ShortHelp")
            if identity not in anchored or not re.search(r"[\u3400-\u9fff]", zh[key]):
                continue
            english, translation = en[key].strip(), zh[key].strip()
            if not english or len(english) > 120 or len(translation) > 100:
                continue
            en_names, zh_names = [english], [translation]
            for field in ("Title",):
                name = identity + "." + field
                if name in en and name in zh and re.search(r"[\u3400-\u9fff]", zh[name]):
                    en_names.append(en[name].strip().rstrip(". …"))
                    zh_names.append(zh[name].strip().rstrip(". …"))
            entries[identity] = {
                "id": identity,
                "en": list(dict.fromkeys(en_names)),
                "zh": list(dict.fromkeys(zh_names)),
                "description_en": en.get(identity + ".Help", "")[:500],
                "description_zh": zh.get(identity + ".Help", "")[:500],
                "source": {"catalog": path.name, "key": key},
            }
    payload = {
        "schema_version": 1,
        "provenance": {
            "source_release": "CATIA V5 B33",
            "resource_pairs": pairs,
            "document_files": anchor_files,
            "version_policy": "informational-only",
        },
        "commands": [entries[key] for key in sorted(entries)],
        "editorial_aliases": [
            {"zh": "管理约束", "en": "Managing Constraints"},
            {"zh": "约束管理", "en": "Managing Constraints"},
        ],
        "extraction_errors": errors,
    }
    payload["revision"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--installation", type=Path, required=True, help="B33 or equivalent release directory"
    )
    parser.add_argument("--documentation", type=Path, required=True)
    parser.add_argument(
        "--output", type=Path, default=Path("src/catia_manual/assets/command_terms.json")
    )
    args = parser.parse_args()
    result = extract(args.installation, args.documentation)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", "utf-8")
    print(
        json.dumps(
            {
                "commands": len(result["commands"]),
                "errors": len(result["extraction_errors"]),
                "revision": result["revision"],
            }
        )
    )


if __name__ == "__main__":
    main()

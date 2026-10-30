"""Bundled bilingual command vocabulary; runtime never reads a CATIA installation."""

import json
import re
from functools import lru_cache
from pathlib import Path


def normalized(text):
    return " ".join(re.findall(r"[\w]+", text.casefold(), re.UNICODE))


@lru_cache(maxsize=1)
def vocabulary():
    path = Path(__file__).resolve().parents[1] / "assets/command_terms.json"
    value = json.loads(path.read_text("utf-8"))
    if value["schema_version"] != 1:
        raise ValueError("不支持的命令词库版本")
    return value


@lru_cache(maxsize=1)
def commands():
    return {entry["id"]: entry for entry in vocabulary()["commands"]}


@lru_cache(maxsize=1)
def names():
    result = {}
    for entry in commands().values():
        for label in entry["en"] + entry["zh"]:
            result.setdefault(normalized(label), []).append(entry["id"])
    return result


def lookup(text):
    return names().get(normalized(text), [])


@lru_cache(maxsize=1)
def aliases():
    result = {}
    for entry in commands().values():
        for label in entry["zh"]:
            result.setdefault(label, []).append(entry["en"][0])
    result.update({entry["zh"]: [entry["en"]] for entry in vocabulary()["editorial_aliases"]})
    return {label: tuple(dict.fromkeys(terms)) for label, terms in result.items()}


def english_names(text):
    return aliases().get(text.strip(), (text,))


def chinese_label(text):
    for entry in vocabulary()["editorial_aliases"]:
        if normalized(entry["en"]) == normalized(text):
            return entry["zh"]
    ids = lookup(text)
    if ids:
        return commands()[ids[0]]["zh"][0]
    for prefix, chinese in (("Managing ", "管理"), ("Creating ", "创建"), ("Using ", "使用")):
        if text.startswith(prefix):
            ids = lookup(text[len(prefix) :])
            if ids:
                return chinese + commands()[ids[0]]["zh"][0]
    return ""

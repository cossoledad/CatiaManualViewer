"""Incremental page translation with a persistent, versioned SQLite cache. No Qt."""

import hashlib
import json
import re
from dataclasses import dataclass, field

from catia_manual.core.reading import RENDERER_VERSION, ReadingPage, ReadingUnit
from catia_manual.core.translation import GLOSSARY, TranslationResult, TranslationService


@dataclass
class ScopedProvider:
    name: str
    delegate: object

    def translate(self, text, target):
        return self.delegate.translate(text, target)


class PageTranslationService:
    def __init__(self, index, provider, page: ReadingPage, target="zh-CN"):
        if provider.name.startswith("glossary"):
            raise ValueError("整页翻译需要本机模型或翻译 API，请在设置中选择翻译服务。")
        self.index, self.provider, self.page, self.target = index, provider, page, target
        glossary = hashlib.sha256(json.dumps(GLOSSARY, sort_keys=True).encode()).hexdigest()
        self.fingerprint = f"page-v1:{RENDERER_VERSION}:{provider.name}:{target}:{glossary}"
        index.connection.execute("""CREATE TABLE IF NOT EXISTS page_translations(
          cache_key TEXT PRIMARY KEY, page_path TEXT NOT NULL, source_hash TEXT NOT NULL,
          unit_id TEXT NOT NULL, fingerprint TEXT NOT NULL, translated_text TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""")
        index.connection.commit()

    def key(self, unit):
        value = [self.page.path, self.page.content_hash, unit.id, unit.text, self.fingerprint]
        return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()

    def cached(self):
        rows = self.index.connection.execute(
            "SELECT unit_id, translated_text FROM page_translations "
            "WHERE page_path=? AND source_hash=? AND fingerprint=?",
            (self.page.path, self.page.content_hash, self.fingerprint),
        )
        valid = {unit.id for unit in self.page.units}
        return {
            identity: TranslationResult(value, self.provider.name, True)
            for identity, value in rows
            if identity in valid
        }

    def translate(self, unit: ReadingUnit):
        row = self.index.connection.execute(
            "SELECT translated_text FROM page_translations WHERE cache_key=? OR "
            "(page_path=? AND source_hash=? AND unit_id=? AND fingerprint=?) LIMIT 1",
            (self.key(unit), self.page.path, self.page.content_hash, unit.id, self.fingerprint),
        ).fetchone()
        if row:
            return TranslationResult(row[0], self.provider.name, True)
        service = TranslationService(self.index, ScopedProvider(self.fingerprint, self.provider))
        translated = [service.translate(chunk, self.target).text for chunk in chunks(unit.text)]
        value = " ".join(translated)
        self.index.connection.execute(
            "INSERT OR REPLACE INTO page_translations"
            "(cache_key,page_path,source_hash,unit_id,fingerprint,translated_text) "
            "VALUES(?,?,?,?,?,?)",
            (
                self.key(unit),
                self.page.path,
                self.page.content_hash,
                unit.id,
                self.fingerprint,
                value,
            ),
        )
        self.index.connection.commit()
        return TranslationResult(value, self.provider.name, False)


def chunks(text, maximum=1000):
    """Bound every request, preferring sentence and then word boundaries."""
    while len(text) > maximum:
        candidates = [m.end() for m in re.finditer(r"[.!?;]\s+", text[: maximum + 1])]
        end = candidates[-1] if candidates else text.rfind(" ", 0, maximum + 1)
        if end < maximum // 3:
            end = maximum
        yield text[:end].strip()
        text = text[end:].lstrip()
    if text.strip():
        yield text.strip()


@dataclass
class PageTranslationTask:
    page: ReadingPage
    mode: str = "original"
    paused: bool = False
    results: dict[str, TranslationResult] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    consecutive_failures: int = 0

    def next_unit(self, visible=()):
        if self.paused or self.mode == "original":
            return None
        available = {
            unit.id: unit
            for unit in self.page.units
            if unit.id not in self.results and unit.id not in self.errors
        }
        return next(
            (available[i] for i in visible if i in available), next(iter(available.values()), None)
        )

    def retry(self, identity=None):
        self.consecutive_failures = 0
        if identity is None:
            self.errors.clear()
        else:
            self.errors.pop(identity, None)
        self.paused = False

    def succeed(self, identity, result):
        self.consecutive_failures = 0
        self.results[identity] = result
        self.errors.pop(identity, None)

    def fail(self, identity, message):
        self.consecutive_failures += 1
        self.errors[identity] = message
        if self.consecutive_failures >= 3:
            self.paused = True

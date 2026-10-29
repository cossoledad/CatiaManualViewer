import hashlib
import os
import re
from dataclasses import dataclass
from typing import Protocol

from catia_manual.core.index import ManualIndex

GLOSSARY = {
    "pad": "凸台（拉伸）",
    "pocket": "凹槽（挖切）",
    "sketch": "草图",
    "part design": "零件设计",
    "assembly design": "装配设计",
    "sketcher": "草图设计器",
    "fillet": "圆角",
    "chamfer": "倒角",
    "shaft": "旋转体",
    "groove": "旋转槽",
    "rib": "肋（扫掠）",
    "slot": "开槽",
    "hole": "孔",
    "shell": "抽壳",
    "draft": "拔模",
    "constraint": "约束",
    "profile": "轮廓",
    "surface": "曲面",
    "body": "几何体",
    "workbench": "工作台",
    "powercopy": "超级副本",
    "parameter": "参数",
    "dimension": "尺寸",
    "mirrored extent": "镜像延伸",
}


class TranslationProvider(Protocol):
    name: str

    def translate(self, text: str, target: str) -> str: ...


class GlossaryProvider:
    name = "glossary-v1"

    def translate(self, text: str, target: str) -> str:
        if not target.lower().startswith("zh"):
            raise ValueError("离线术语表目前仅支持中文")
        matches = [
            f"{term} → {translated}"
            for term, translated in GLOSSARY.items()
            if re.search(r"\b" + re.escape(term) + r"s?\b", text, re.I)
        ]
        return "术语辅助（非整句翻译）\n" + (
            "\n".join(matches) if matches else "未匹配术语，请配置翻译服务以翻译整句。"
        )


class LibreTranslateProvider:
    def __init__(self, endpoint: str, api_key_env: str = "CATIA_TRANSLATION_API_KEY"):
        if not endpoint.startswith(("https://", "http://")):
            raise ValueError("翻译服务地址必须为 HTTP(S)")
        self.endpoint, self.api_key_env = endpoint, api_key_env
        self.name = "libretranslate:" + endpoint

    def translate(self, text: str, target: str) -> str:
        import httpx

        payload = {
            "q": text,
            "source": "en",
            "target": "zh" if target == "zh-CN" else target,
            "format": "text",
        }
        key = os.environ.get(self.api_key_env)
        if key:
            payload["api_key"] = key
        with httpx.Client(timeout=60) as client:
            response = client.post(self.endpoint, json=payload)
            response.raise_for_status()
            result = response.json()["translatedText"]
        if not isinstance(result, str) or not result.strip():
            raise ValueError("翻译服务返回了空结果")
        return result


@dataclass(frozen=True)
class TranslationResult:
    text: str
    provider: str
    cached: bool


class TranslationService:
    def __init__(self, index: ManualIndex, provider: TranslationProvider):
        self.index, self.provider = index, provider

    def translate(self, text: str, target: str = "zh-CN") -> TranslationResult:
        if not text.strip():
            raise ValueError("请先选择需要翻译的文字")
        key = hashlib.sha256(f"{self.provider.name}\0{target}\0{text}".encode()).hexdigest()
        row = self.index.connection.execute(
            "SELECT translated_text FROM translations WHERE cache_key=?", (key,)
        ).fetchone()
        if row:
            return TranslationResult(row[0], self.provider.name, True)
        translated = self.provider.translate(text, target)
        self.index.connection.execute(
            "INSERT OR REPLACE INTO translations"
            "(cache_key,provider,target,source_text,translated_text) VALUES(?,?,?,?,?)",
            (key, self.provider.name, target, text, translated),
        )
        self.index.connection.commit()
        return TranslationResult(translated, self.provider.name, False)


def make_provider(config: dict) -> TranslationProvider:
    name = config.get("provider", "glossary")
    if name == "glossary":
        return GlossaryProvider()
    if name == "libretranslate":
        return LibreTranslateProvider(
            config["endpoint"], config.get("api_key_env", "CATIA_TRANSLATION_API_KEY")
        )
    raise ValueError(f"未知翻译提供方: {name}")

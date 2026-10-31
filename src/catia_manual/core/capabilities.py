"""Cheap local capability checks. Never start CATIA, load a model, or contact an API."""

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

from catia_manual.core.credentials import resolve_api_key
from catia_manual.core.providers import provider_profile


@dataclass(frozen=True)
class Capability:
    available: bool
    reason: str = ""


@dataclass(frozen=True)
class Capabilities:
    documents: Capability
    sample_launch: Capability
    selection_translation: Capability
    page_translation: Capability


def module_available(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def detect_capabilities(settings, *, catia_session=False):
    documents = Capability((settings.root / "online").is_dir(), "请先选择文档目录")
    launcher = (
        settings.launcher.is_file()
        and settings.environment_dir.is_dir()
        and bool(settings.environment.strip())
    )
    com = module_available("pythoncom") and module_available("win32com")
    samples = Capability(
        sys.platform == "win32" and (launcher or (com and catia_session)),
        "CATIA 不可用",
    )
    config = {
        **settings.translation,
        **provider_profile(settings.translation, settings.translation.get("provider", "glossary")),
    }
    name = config.get("provider", "glossary")
    ready, reason = True, ""
    if name == "local":
        pair = "zh_en" if config.get("target_language") == "en" else "en_zh"
        model = Path(config.get("model_dir", "")) / pair
        ready = all(
            (model / file).is_file()
            for file in ("model/model.bin", "sentencepiece.model", "metadata.json")
        ) and all(module_available(module) for module in ("ctranslate2", "sentencepiece"))
        reason = "离线模型未就绪"
    elif name != "glossary":
        try:
            key = resolve_api_key(config)
            ready = name not in {"deepl", "chat-completions"} or bool(key)
            if not ready:
                reason = "请填写 API Key"
        except ValueError as exc:
            ready = False
            reason = str(exc)
    if name != "local" and name != "glossary":
        ready = ready and module_available("httpx") and bool(config.get("endpoint", "").strip())
        if name == "chat-completions":
            ready = ready and bool(config.get("model", "").strip())
        reason = reason or "请检查翻译设置"
    selection = Capability(ready, "" if ready else reason)
    page = Capability(ready and name != "glossary", reason if not ready else "请选择模型或翻译 API")
    return Capabilities(documents, samples, selection, page)

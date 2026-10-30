"""Cheap local capability checks. Never start CATIA, load a model, or contact an API."""

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path

from catia_manual.core.credentials import get_api_key


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
        "未检测到可用的 CATIA 启动配置或会话；仍可定位和复制文件路径",
    )
    config = settings.translation
    name = config.get("provider", "glossary")
    ready, reason = True, ""
    if name == "local":
        pair = "zh_en" if config.get("target_language") == "en" else "en_zh"
        model = Path(config.get("model_dir", "")) / pair
        ready = all(
            (model / file).is_file()
            for file in ("model/model.bin", "sentencepiece.model", "metadata.json")
        ) and all(module_available(module) for module in ("ctranslate2", "sentencepiece"))
        reason = "离线翻译模型或推理组件不可用，请在设置中选择模型或其他服务"
    elif name in {"deepl", "chat-completions"}:
        ready = bool(get_api_key(config.get("api_key_env", "CATIA_TRANSLATION_API_KEY")))
        reason = "请在翻译设置中配置 API Key"
    if name != "local" and name != "glossary":
        ready = ready and module_available("httpx") and bool(config.get("endpoint", "").strip())
        if name == "chat-completions":
            ready = ready and bool(config.get("model", "").strip())
        reason = reason or "请检查翻译服务地址与组件"
    selection = Capability(ready, "" if ready else reason)
    page = Capability(ready and name != "glossary", reason if not ready else "请选择模型或翻译 API")
    return Capabilities(documents, samples, selection, page)

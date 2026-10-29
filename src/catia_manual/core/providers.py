"""Provider presets contain public defaults only, never credentials."""

from copy import deepcopy

PROVIDERS = {
    "local": {
        "label": "本机离线模型",
        "endpoint": "",
        "model": "",
        "api_key_env": "CATIA_TRANSLATION_API_KEY",
    },
    "mymemory": {
        "label": "MyMemory",
        "endpoint": "https://api.mymemory.translated.net/get",
        "model": "",
        "api_key_env": "MYMEMORY_API_KEY",
    },
    "deepl": {
        "label": "DeepL API Free",
        "endpoint": "https://api-free.deepl.com/v2/translate",
        "model": "",
        "api_key_env": "DEEPL_API_KEY",
    },
    "libretranslate": {
        "label": "LibreTranslate",
        "endpoint": "http://127.0.0.1:5000/translate",
        "model": "",
        "api_key_env": "LIBRETRANSLATE_API_KEY",
    },
    "glossary": {
        "label": "离线术语表",
        "endpoint": "",
        "model": "",
        "api_key_env": "CATIA_TRANSLATION_API_KEY",
    },
    "chat-completions": {
        "label": "大语言模型 · 兼容 API",
        "endpoint": "",
        "model": "",
        "api_key_env": "CATIA_TRANSLATION_API_KEY",
    },
}


def provider_profile(config: dict, name: str) -> dict:
    result = deepcopy(PROVIDERS.get(name, {}))
    result.pop("label", None)
    result.update(config.get("profiles", {}).get(name, {}))
    if config.get("provider") == name:
        for key in ("endpoint", "model", "api_key_env"):
            if config.get(key):
                result[key] = config[key]
    return result

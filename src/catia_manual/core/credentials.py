import os

# Session-only values deliberately stay out of os.environ/subprocess inheritance.
_session_keys: dict[str, str] = {}


def set_session_key(name: str, value: str) -> None:
    if not name or "=" in name or "\x00" in name:
        raise ValueError("API Key 环境变量名无效")
    _session_keys[name] = value


def get_api_key(name: str) -> str | None:
    return _session_keys.get(name) or os.environ.get(name)

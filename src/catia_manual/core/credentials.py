"""Optional environment/session credentials and user-scoped Windows persistence."""

import base64
import ctypes
import os
import sys
from ctypes import wintypes
from functools import lru_cache

# Session-only values deliberately stay out of os.environ/subprocess inheritance.
_session_keys: dict[str, str] = {}


def set_session_key(name: str, value: str) -> None:
    if not name or "=" in name or "\x00" in name:
        raise ValueError("API Key 环境变量名无效")
    _session_keys[name] = value


def get_api_key(name: str) -> str | None:
    return _session_keys.get(name) or os.environ.get(name)


def _crypt(data: bytes, *, decrypt=False) -> bytes:
    if sys.platform != "win32":
        raise ValueError("密钥保存需要 Windows")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    function = library.CryptUnprotectData if decrypt else library.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    function.restype = wintypes.BOOL
    # CRYPTPROTECT_UI_FORBIDDEN; deliberately do not use LOCAL_MACHINE scope.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ValueError("无法读取已保存的 Key，请重新填写" if decrypt else "无法保存 Key")
    free = ctypes.WinDLL("kernel32", use_last_error=True).LocalFree
    free.argtypes, free.restype = [ctypes.c_void_p], ctypes.c_void_p
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.memset(output.data, 0, output.size)
        free(output.data)


def protect_key(value: str) -> str:
    if not value:
        return ""
    return "dpapi:v1:" + base64.b64encode(_crypt(value.encode("utf-8"))).decode("ascii")


@lru_cache(maxsize=128)
def unprotect_key(value: str) -> str:
    if not value:
        return ""
    try:
        if not value.startswith("dpapi:v1:"):
            raise ValueError("unknown credential format")
        data = base64.b64decode(value.removeprefix("dpapi:v1:"), validate=True)
        return _crypt(data, decrypt=True).decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise ValueError("无法读取已保存的 Key，请重新填写") from exc


def resolve_api_key(config: dict) -> str | None:
    fallback = get_api_key(config.get("api_key_env", "CATIA_TRANSLATION_API_KEY"))
    if "api_key_override" in config:
        return config["api_key_override"] or fallback
    try:
        return unprotect_key(config.get("api_key_encrypted", "")) or fallback
    except ValueError:
        if fallback:
            return fallback
        raise

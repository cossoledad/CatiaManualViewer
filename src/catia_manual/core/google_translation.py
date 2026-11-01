"""Optional, rate-limited adapter for Google's public web translation endpoint."""

import threading
import time
from urllib.parse import quote, urlsplit

import httpx

DEFAULT_ENDPOINT = "https://translate.googleapis.com/translate_a/single"
_lock = threading.Lock()
_next_request = 0.0


def chunks(text):
    # Bound encoded GET URLs as well as character counts; never cut UTF-8 bytes.
    while text:
        end, size = 0, 0
        for char in text[:1000]:
            size += len(quote(char, safe=""))
            if size > 4500:
                break
            end += 1
        if end < len(text):
            boundary = max(text.rfind(mark, 0, end) for mark in ("\n", ". ", "。", " "))
            if boundary >= end // 2:
                end = boundary + 1
        yield text[:end]
        text = text[end:]


def wait_turn():
    global _next_request
    # Shared by selection and page workers, including newly created providers.
    with _lock:
        time.sleep(max(0, _next_request - time.monotonic()))
        _next_request = time.monotonic() + 0.75


class GoogleTranslateProvider:
    def __init__(self, endpoint=DEFAULT_ENDPOINT):
        url = urlsplit(endpoint)
        if (
            url.scheme not in {"https", "http"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("请填写不含参数的 HTTP(S) 翻译服务地址")
        self.endpoint = endpoint
        self.name = "google-gtx-v1:" + endpoint

    def translate(self, text, target):
        if not text.strip():
            raise ValueError("请先选择需要翻译的文字")
        translated = []
        with httpx.Client(timeout=20) as client:
            for chunk in chunks(text):
                wait_turn()
                try:
                    response = client.get(
                        self.endpoint,
                        params={
                            "client": "gtx",
                            "sl": "auto",
                            "tl": target,
                            "dt": "t",
                            "q": chunk,
                        },
                    )
                except httpx.HTTPError:
                    raise ValueError("Google 翻译连接失败，请稍后重试") from None
                if response.status_code in {403, 429}:
                    # Do not keep hammering a temporary quota or access restriction.
                    global _next_request
                    with _lock:
                        _next_request = max(_next_request, time.monotonic() + 10)
                    raise ValueError("Google 翻译访问受限，请暂停后重试或更换服务")
                if response.status_code != 200:
                    raise ValueError(f"Google 翻译请求失败（HTTP {response.status_code}）")
                try:
                    data = response.json()
                    segments = data[0]
                    if not isinstance(segments, list) or not segments:
                        raise ValueError
                    parts = []
                    for segment in segments:
                        if not isinstance(segment, list) or not isinstance(segment[0], str):
                            raise ValueError
                        parts.append(segment[0])
                    result = "".join(parts)
                    if not result.strip():
                        raise ValueError
                except (ValueError, TypeError, IndexError, KeyError):
                    raise ValueError("Google 翻译返回格式异常，请重试或更换服务") from None
                translated.append(result)
        return "".join(translated)

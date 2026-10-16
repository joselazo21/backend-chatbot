"""Small transport boundary for a chat-completions compatible LLM service."""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from django.conf import settings


class LLMError(Exception):
    def __init__(self, code, *, retryable):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def generate_reply(messages, on_delta=None):
    """Return one complete assistant reply; never log prompts or credentials."""
    url = settings.LLM_CHAT_URL
    parsed = urlsplit(url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or not settings.LLM_MODEL
    ):
        raise LLMError("invalid_configuration", retryable=False)

    payload = json.dumps(
        {"model": settings.LLM_MODEL, "messages": messages, "stream": on_delta is not None},
        ensure_ascii=False,
    ).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream" if on_delta else "application/json"}
    if settings.LLM_API_TOKEN:
        headers["Authorization"] = f"Bearer {settings.LLM_API_TOKEN}"
    request = Request(url, data=payload, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=settings.LLM_TIMEOUT_SECONDS) as response:
            if on_delta is not None:
                content = _read_stream(response, on_delta)
                return _validate_content(content)
            raw = response.read(1_000_001)
    except HTTPError as error:
        raise LLMError(
            "upstream_http_error", retryable=error.code == 429 or error.code >= 500
        ) from error
    except (URLError, TimeoutError, OSError) as error:
        raise LLMError("upstream_unavailable", retryable=True) from error

    if len(raw) > 1_000_000:
        raise LLMError("upstream_response_too_large", retryable=False)
    try:
        body = json.loads(raw)
        content = body["choices"][0]["message"]["content"]
    except (ValueError, UnicodeDecodeError, KeyError, IndexError, TypeError) as error:
        raise LLMError("invalid_upstream_response", retryable=False) from error
    return _validate_content(content)


def _validate_content(content):
    if not isinstance(content, str):
        raise LLMError("invalid_upstream_response", retryable=False)
    content = content.strip()
    try:
        content.encode("utf-8")
    except UnicodeEncodeError as error:
        raise LLMError("invalid_upstream_response", retryable=False) from error
    if not content or len(content) > 16000 or "\x00" in content:
        raise LLMError("invalid_upstream_response", retryable=False)
    return content


def _read_stream(response, on_delta):
    parts = []
    size = 0
    bytes_seen = 0
    finished = False
    for line in response:
        bytes_seen += len(line)
        if len(line) > 65536 or bytes_seen > 2_000_000:
            raise LLMError("upstream_response_too_large", retryable=False)
        if not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if data == b"[DONE]":
            finished = True
            break
        try:
            event = json.loads(data)
            delta = event["choices"][0]["delta"].get("content")
        except (ValueError, UnicodeDecodeError, KeyError, IndexError, TypeError, AttributeError) as error:
            raise LLMError("invalid_upstream_response", retryable=False) from error
        if delta is None:
            continue
        if not isinstance(delta, str) or "\x00" in delta:
            raise LLMError("invalid_upstream_response", retryable=False)
        size += len(delta)
        if size > 16000:
            raise LLMError("upstream_response_too_large", retryable=False)
        parts.append(delta)
        on_delta(delta)
    if not finished:
        raise LLMError("incomplete_upstream_stream", retryable=True)
    return "".join(parts)

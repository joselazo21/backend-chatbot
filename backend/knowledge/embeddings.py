"""OpenAI-compatible embeddings transport, kept separate from document processing."""

import json
import math
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from django.conf import settings


class EmbeddingError(Exception):
    def __init__(self, code, *, retryable):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def embed_texts(texts):
    if not texts or len(texts) > 16 or any(not isinstance(text, str) or not text for text in texts):
        raise ValueError("Expected 1 to 16 nonempty texts")
    parsed = urlsplit(settings.EMBEDDING_URL)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or not settings.EMBEDDING_MODEL
    ):
        raise EmbeddingError("invalid_configuration", retryable=False)
    payload = json.dumps(
        {"model": settings.EMBEDDING_MODEL, "input": texts}, ensure_ascii=False
    ).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if settings.EMBEDDING_API_TOKEN:
        headers["Authorization"] = f"Bearer {settings.EMBEDDING_API_TOKEN}"
    request = Request(settings.EMBEDDING_URL, data=payload, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=settings.EMBEDDING_TIMEOUT_SECONDS) as response:
            raw = response.read(8_000_001)
    except HTTPError as error:
        raise EmbeddingError("upstream_http_error", retryable=error.code == 429 or error.code >= 500) from error
    except (URLError, TimeoutError, OSError) as error:
        raise EmbeddingError("upstream_unavailable", retryable=True) from error
    if len(raw) > 8_000_000:
        raise EmbeddingError("upstream_response_too_large", retryable=False)
    try:
        body = json.loads(raw)
        items = body["data"]
        if not isinstance(items, list) or len(items) != len(texts):
            raise ValueError
        ordered = sorted(items, key=lambda item: item["index"])
        if [item["index"] for item in ordered] != list(range(len(texts))):
            raise ValueError
        vectors = [item["embedding"] for item in ordered]
        dimensions = len(vectors[0])
        if not 1 <= dimensions <= 16000:
            raise ValueError
        for vector in vectors:
            if (
                not isinstance(vector, list)
                or len(vector) != dimensions
                or any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) for value in vector)
                or not any(value != 0 for value in vector)
            ):
                raise ValueError
    except (ValueError, UnicodeDecodeError, KeyError, IndexError, TypeError) as error:
        raise EmbeddingError("invalid_upstream_response", retryable=False) from error
    return vectors

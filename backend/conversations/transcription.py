"""Small transport boundary for an OpenAI-compatible speech-to-text service (Speaches)."""

import json
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from django.conf import settings

ALLOWED_CONTENT_TYPES = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}


class TranscriptionError(Exception):
    def __init__(self, code, *, retryable):
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def normalize_content_type(value):
    """Return the bare media type if it is an accepted audio format, else None."""
    base = (value or "").split(";")[0].strip().lower()
    return base if base in ALLOWED_CONTENT_TYPES else None


def transcribe_audio(data, content_type):
    """Return the transcript text; never log audio, transcripts or credentials."""
    url = settings.WHISPER_URL
    parsed = urlsplit(url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or not settings.WHISPER_MODEL
    ):
        raise TranscriptionError("invalid_configuration", retryable=False)
    if not data or len(data) > settings.WHISPER_MAX_AUDIO_BYTES:
        raise TranscriptionError("invalid_audio", retryable=False)
    extension = ALLOWED_CONTENT_TYPES.get(normalize_content_type(content_type))
    if extension is None:
        raise TranscriptionError("invalid_audio", retryable=False)

    fields = {"model": settings.WHISPER_MODEL, "response_format": "json", "temperature": "0"}
    if settings.WHISPER_LANGUAGE:
        fields["language"] = settings.WHISPER_LANGUAGE
    boundary = uuid.uuid4().hex
    body = bytearray()
    for name, value in fields.items():
        body += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        ).encode()
    body += (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio{extension}"\r\n'
        f"Content-Type: {normalize_content_type(content_type)}\r\n\r\n"
    ).encode()
    body += data
    body += f"\r\n--{boundary}--\r\n".encode()

    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Accept": "application/json",
    }
    if settings.WHISPER_API_TOKEN:
        headers["Authorization"] = f"Bearer {settings.WHISPER_API_TOKEN}"
    request = Request(url, data=bytes(body), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=settings.WHISPER_TIMEOUT_SECONDS) as response:
            raw = response.read(1_000_001)
    except HTTPError as error:
        raise TranscriptionError(
            "upstream_http_error", retryable=error.code == 429 or error.code >= 500
        ) from error
    except (URLError, TimeoutError, OSError) as error:
        raise TranscriptionError("upstream_unavailable", retryable=True) from error

    if len(raw) > 1_000_000:
        raise TranscriptionError("upstream_response_too_large", retryable=False)
    try:
        text = json.loads(raw)["text"]
    except (ValueError, UnicodeDecodeError, KeyError, TypeError) as error:
        raise TranscriptionError("invalid_upstream_response", retryable=False) from error
    if not isinstance(text, str) or "\x00" in text:
        raise TranscriptionError("invalid_upstream_response", retryable=False)
    text = text.strip()
    if not text:
        raise TranscriptionError("empty_transcription", retryable=False)
    # Keep the transcript within the same limit as typed messages.
    return text[:4000]

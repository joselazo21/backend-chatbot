import io
from unittest.mock import patch
from urllib.error import HTTPError

from django.conf import settings
from django.test import SimpleTestCase

from .transcription import TranscriptionError, normalize_content_type, transcribe_audio

URL = "https://speaches.example.com/v1/audio/transcriptions"


def configured():
    return (
        patch.object(settings, "WHISPER_URL", URL),
        patch.object(settings, "WHISPER_MODEL", "Systran/faster-whisper-small"),
        patch.object(settings, "WHISPER_API_TOKEN", "private-token"),
        patch.object(settings, "WHISPER_LANGUAGE", "es"),
    )


class TranscriptionTransportTests(SimpleTestCase):
    def run_transcribe(self, response=b'{"text": "  Hola mundo  "}', content_type="audio/ogg", data=b"OggS-audio"):
        patches = configured()
        with patches[0], patches[1], patches[2], patches[3], patch(
            "conversations.transcription.urlopen", return_value=io.BytesIO(response)
        ) as send:
            return transcribe_audio(data, content_type), send

    def test_sends_multipart_request_with_auth_and_returns_trimmed_text(self):
        text, send = self.run_transcribe(content_type="audio/webm;codecs=opus")
        self.assertEqual(text, "Hola mundo")
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, URL)
        self.assertEqual(request.get_header("Authorization"), "Bearer private-token")
        self.assertEqual(request.get_header("User-agent"), "chatbot-backend/1.0")
        self.assertTrue(request.get_header("Content-type").startswith("multipart/form-data; boundary="))
        body = request.data
        self.assertIn(b'name="model"\r\n\r\nSystran/faster-whisper-small', body)
        self.assertIn(b'name="language"\r\n\r\nes', body)
        self.assertIn(b'filename="audio.webm"', body)
        self.assertIn(b"OggS-audio", body)

    def test_rejects_unsupported_or_oversized_audio_before_calling_upstream(self):
        patches = configured()
        with patches[0], patches[1], patches[2], patches[3], patch("conversations.transcription.urlopen") as send:
            for data, content_type in ((b"x", "video/mp4"), (b"", "audio/ogg"), (b"x" * (settings.WHISPER_MAX_AUDIO_BYTES + 1), "audio/ogg")):
                with self.assertRaises(TranscriptionError) as caught:
                    transcribe_audio(data, content_type)
                self.assertEqual(caught.exception.code, "invalid_audio")
                self.assertFalse(caught.exception.retryable)
        send.assert_not_called()

    def test_classifies_upstream_errors(self):
        patches = configured()
        for status, retryable in ((503, True), (429, True), (401, False), (400, False)):
            with patches[0], patches[1], patches[2], patches[3], patch(
                "conversations.transcription.urlopen", side_effect=HTTPError("url", status, "x", None, None)
            ):
                with self.assertRaises(TranscriptionError) as caught:
                    transcribe_audio(b"audio", "audio/ogg")
            self.assertEqual(caught.exception.code, "upstream_http_error")
            self.assertEqual(caught.exception.retryable, retryable)

    def test_unreachable_upstream_is_retryable(self):
        patches = configured()
        with patches[0], patches[1], patches[2], patches[3], patch(
            "conversations.transcription.urlopen", side_effect=TimeoutError()
        ):
            with self.assertRaises(TranscriptionError) as caught:
                transcribe_audio(b"audio", "audio/ogg")
        self.assertEqual(caught.exception.code, "upstream_unavailable")
        self.assertTrue(caught.exception.retryable)

    def test_silence_and_malformed_responses_are_not_retried(self):
        for body, code in ((b'{"text": "   "}', "empty_transcription"), (b"{}", "invalid_upstream_response"), (b"not json", "invalid_upstream_response")):
            with self.assertRaises(TranscriptionError) as caught:
                self.run_transcribe(response=body)
            self.assertEqual(caught.exception.code, code)
            self.assertFalse(caught.exception.retryable)

    def test_missing_configuration_is_rejected(self):
        with patch.object(settings, "WHISPER_URL", ""), self.assertRaises(TranscriptionError) as caught:
            transcribe_audio(b"audio", "audio/ogg")
        self.assertEqual(caught.exception.code, "invalid_configuration")

    def test_normalize_content_type(self):
        self.assertEqual(normalize_content_type("Audio/WebM; codecs=opus"), "audio/webm")
        self.assertIsNone(normalize_content_type("text/plain"))
        self.assertIsNone(normalize_content_type(None))

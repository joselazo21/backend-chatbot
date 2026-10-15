import io
import json
from unittest.mock import patch
from urllib.error import HTTPError

from django.conf import settings
from django.test import SimpleTestCase

from .llm import LLMError, generate_reply


class LLMTransportTests(SimpleTestCase):
    def test_sends_openai_compatible_request_and_reads_reply(self):
        body = json.dumps({"choices": [{"message": {"content": "  Hola  "}}]}).encode()
        with patch.object(settings, "LLM_CHAT_URL", "http://llm.railway.internal:8000/v1/chat/completions"), patch.object(
            settings, "LLM_MODEL", "local-model"
        ), patch.object(settings, "LLM_API_TOKEN", "private-token"), patch(
            "conversations.llm.urlopen", return_value=io.BytesIO(body)
        ) as send:
            answer = generate_reply([{"role": "user", "content": "Hola"}])
        self.assertEqual(answer, "Hola")
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, "http://llm.railway.internal:8000/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer private-token")
        self.assertEqual(json.loads(request.data)["messages"], [{"role": "user", "content": "Hola"}])

    def test_classifies_upstream_errors_without_exposing_response_body(self):
        with patch.object(settings, "LLM_CHAT_URL", "https://llm.example.com/v1/chat/completions"), patch.object(
            settings, "LLM_MODEL", "model"
        ), patch("conversations.llm.urlopen", side_effect=HTTPError("url", 503, "down", None, None)):
            with self.assertRaises(LLMError) as caught:
                generate_reply([{"role": "user", "content": "hola"}])
        self.assertEqual(caught.exception.code, "upstream_http_error")
        self.assertTrue(caught.exception.retryable)

    def test_rejects_invalid_model_response(self):
        with patch.object(settings, "LLM_CHAT_URL", "https://llm.example.com/v1/chat/completions"), patch.object(
            settings, "LLM_MODEL", "model"
        ), patch("conversations.llm.urlopen", return_value=io.BytesIO(b"{}")):
            with self.assertRaises(LLMError) as caught:
                generate_reply([{"role": "user", "content": "hola"}])
        self.assertEqual(caught.exception.code, "invalid_upstream_response")

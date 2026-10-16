import json
import asyncio
from unittest.mock import patch

from django.test import SimpleTestCase

from .models import AssistantRun
from .stream import _events


class StreamTests(SimpleTestCase):
    def test_replays_and_resets_after_retry(self):
        states = iter([
            {"status": AssistantRun.Status.RUNNING, "attempts": 1, "streamed_content": "Ho", "assistant_message_id": None, "error_code": "", "retrieved_sources": []},
            {"status": AssistantRun.Status.PENDING, "attempts": 1, "streamed_content": "Ho", "assistant_message_id": None, "error_code": "upstream_unavailable", "retrieved_sources": []},
            {"status": AssistantRun.Status.COMPLETED, "attempts": 2, "streamed_content": "Hola", "assistant_message_id": 42, "error_code": "", "retrieved_sources": []},
        ])

        async def collect():
            return [part async for part in _events(object(), 1, 1, "https://shop.example.com")]

        def fake_sync_to_async(_, **kwargs):
            async def get_state(*args):
                return next(states)
            return get_state

        with patch("conversations.stream.sync_to_async", side_effect=fake_sync_to_async):
            parts = asyncio.run(collect())
        events = [part for part in parts if part.startswith("event:")]
        self.assertEqual([part.split("\n", 1)[0] for part in events], [
            "event: reset", "event: delta", "event: reset", "event: delta", "event: complete"
        ])
        self.assertEqual(json.loads(events[-1].split("data: ", 1)[1])["message_id"], 42)

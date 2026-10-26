import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings
from django.core.cache.backends.locmem import LocMemCache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase
from django_tenants.test.cases import TenantTestCase
from django_tenants.test.client import TenantClient
from django_tenants.utils import get_public_schema_name, schema_context, tenant_context

from customers.models import WidgetSite

from .channel_views import telegram_webhook
from .models import AssistantRun, AudioAttachment, Message
from .tasks import generate_assistant_reply
from .transcription import TranscriptionError

LLM_URL = "http://llm.railway.internal:8000/v1/chat/completions"
WHISPER_URL = "http://speaches.example.com/v1/audio/transcriptions"


class WidgetAudioTests(TenantTestCase):
    def setUp(self):
        cache = LocMemCache("widget-audio-tests", {})
        for target, value in (
            (patch("conversations.widget_views.cache", cache), None),
            (patch.object(settings, "LLM_CHAT_URL", LLM_URL), None),
            (patch.object(settings, "WHISPER_URL", WHISPER_URL), None),
            (patch.object(settings, "WHISPER_MODEL", "small"), None),
        ):
            target.start()
            self.addCleanup(target.stop)
        with schema_context(get_public_schema_name()):
            self.site = WidgetSite.objects.create(
                tenant=self.tenant, allowed_origin="https://shop.example.com"
            )
        self.client = TenantClient(self.tenant)
        origin = {"HTTP_ORIGIN": "https://shop.example.com"}
        token = self.client.post(
            "/widget/v1/sessions/",
            data=json.dumps({"key": self.site.public_key}),
            content_type="application/json",
            **origin,
        ).json()["access_token"]
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {token}", **origin}

    def upload(self, content=b"OggS-audio", name="voice.ogg", content_type="audio/ogg"):
        return self.client.post(
            "/widget/v1/messages/audio/",
            data={"audio": SimpleUploadedFile(name, content, content_type=content_type)},
            **self.auth,
        )

    def test_audio_is_transcribed_then_answered_and_not_kept(self):
        with patch("conversations.widget_views.dispatch_assistant_run") as dispatch:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.upload()
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["content"], "")
        self.assertTrue(body["awaiting_transcription"])
        self.assertEqual(body["generation_status"], "pending")
        dispatch.assert_called_once()
        with tenant_context(self.tenant):
            run = AssistantRun.objects.get(user_message_id=body["id"])
        with patch("conversations.tasks.transcribe_audio", return_value="¿Dónde vive Ana?") as stt, patch(
            "conversations.tasks.generate_reply", return_value="En Tarapoto"
        ) as llm:
            generate_assistant_reply(self.tenant.pk, run.pk)
        self.assertEqual(stt.call_args.args, (b"OggS-audio", "audio/ogg"))
        self.assertEqual(llm.call_args.args[0], [{"role": "user", "content": "¿Dónde vive Ana?"}])
        with tenant_context(self.tenant):
            self.assertFalse(AudioAttachment.objects.exists())
            self.assertEqual(Message.objects.get(pk=body["id"]).content, "¿Dónde vive Ana?")
            self.assertEqual(AssistantRun.objects.get(pk=run.pk).status, "completed")
        listing = self.client.get("/widget/v1/messages/", **self.auth).json()["messages"]
        self.assertFalse(listing[0]["awaiting_transcription"])
        self.assertEqual(listing[0]["content"], "¿Dónde vive Ana?")

    def test_retryable_transcription_failure_keeps_audio_and_permanent_failure_drops_it(self):
        with patch("conversations.widget_views.dispatch_assistant_run"):
            body = self.upload().json()
        with tenant_context(self.tenant):
            run = AssistantRun.objects.get(user_message_id=body["id"])
        with patch(
            "conversations.tasks.transcribe_audio",
            side_effect=TranscriptionError("upstream_unavailable", retryable=True),
        ), patch("conversations.tasks.generate_reply") as llm:
            generate_assistant_reply(self.tenant.pk, run.pk)
        llm.assert_not_called()
        with tenant_context(self.tenant):
            run.refresh_from_db()
            self.assertEqual((run.status, run.error_code), ("pending", "upstream_unavailable"))
            self.assertTrue(AudioAttachment.objects.exists())
            AssistantRun.objects.filter(pk=run.pk).update(next_attempt_at=None)
        with patch(
            "conversations.tasks.transcribe_audio",
            side_effect=TranscriptionError("empty_transcription", retryable=False),
        ):
            generate_assistant_reply(self.tenant.pk, run.pk)
        with tenant_context(self.tenant):
            run.refresh_from_db()
            self.assertEqual((run.status, run.error_code), ("failed", "empty_transcription"))
            self.assertFalse(AudioAttachment.objects.exists())

    def test_rejects_bad_uploads(self):
        self.assertEqual(self.upload(content_type="video/mp4").status_code, 415)
        self.assertEqual(self.upload(content=b"").status_code, 413)
        with patch.object(settings, "WHISPER_MAX_AUDIO_BYTES", 8):
            self.assertEqual(self.upload(content=b"x" * 5000).status_code, 413)
        json_post = self.client.post(
            "/widget/v1/messages/audio/", data=json.dumps({"audio": "x"}),
            content_type="application/json", **self.auth,
        )
        self.assertEqual(json_post.status_code, 415)
        no_auth = self.client.post(
            "/widget/v1/messages/audio/",
            data={"audio": SimpleUploadedFile("a.ogg", b"x", content_type="audio/ogg")},
            HTTP_ORIGIN="https://shop.example.com",
        )
        self.assertEqual(no_auth.status_code, 401)
        with tenant_context(self.tenant):
            self.assertFalse(Message.objects.exists())

    def test_unavailable_when_transcription_is_not_configured(self):
        with patch.object(settings, "WHISPER_URL", ""):
            self.assertEqual(self.upload().status_code, 503)
        preflight = self.client.options("/widget/v1/messages/audio/", HTTP_ORIGIN="https://shop.example.com")
        self.assertEqual(preflight.status_code, 204)


class TelegramVoiceTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.account = SimpleNamespace(pk=uuid4(), tenant_id=17)
        self.path = f"/channels/telegram/{self.account.pk}/webhook/"

    def post(self, message):
        payload = {"message": {"message_id": 12, "chat": {"id": 42, "type": "private"}, **message}}
        request = self.factory.post(self.path, data=json.dumps(payload), content_type="application/json")
        with patch("conversations.channel_views._account", return_value=self.account), patch(
            "conversations.channel_views.accept_inbound"
        ) as ingest:
            status = telegram_webhook(request, self.account.pk).status_code
        return status, ingest

    def test_voice_note_is_accepted_when_transcription_is_configured(self):
        with patch.object(settings, "WHISPER_URL", WHISPER_URL):
            status, ingest = self.post({"voice": {"file_id": "AwAD", "file_size": 20000}})
        self.assertEqual(status, 200)
        ingest.assert_called_once_with(self.account, "42", "42:12", "", telegram_file_id="AwAD")

    def test_voice_note_is_ignored_without_transcription_or_when_too_large(self):
        with patch.object(settings, "WHISPER_URL", ""):
            _, ingest = self.post({"voice": {"file_id": "AwAD", "file_size": 20000}})
        ingest.assert_not_called()
        with patch.object(settings, "WHISPER_URL", WHISPER_URL), patch.object(settings, "WHISPER_MAX_AUDIO_BYTES", 100):
            _, ingest = self.post({"voice": {"file_id": "AwAD", "file_size": 20000}})
        ingest.assert_not_called()

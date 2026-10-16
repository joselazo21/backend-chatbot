import hashlib
import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.test import RequestFactory, SimpleTestCase
from django.conf import settings
from django_tenants.test.cases import TenantTestCase
from django_tenants.utils import get_public_schema_name, schema_context, tenant_context

from customers.models import ChannelAccount

from .channel_views import baileys_ack, baileys_inbound, telegram_webhook
from .channels import accept_inbound, acknowledge_baileys_reply, lease_baileys_reply, queue_channel_reply, secret_matches
from .models import AssistantRun, ChannelConversation, InboundChannelMessage, Message, OutboundChannelMessage


class ChannelEndpointTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.account = SimpleNamespace(pk=uuid4(), tenant_id=17)

    def test_secret_comparison(self):
        digest = hashlib.sha256(b"private-secret").hexdigest()
        self.assertTrue(secret_matches("private-secret", digest))
        self.assertFalse(secret_matches("wrong", digest))
        self.assertFalse(secret_matches("", digest))

    def test_telegram_requires_header_and_deduplicates_at_ingress(self):
        path = f"/channels/telegram/{self.account.pk}/webhook/"
        payload = {"update_id": 99, "message": {"message_id": 12, "text": "Hola", "chat": {"id": 42, "type": "private"}}}
        request = self.factory.post(path, data=json.dumps(payload), content_type="application/json")
        with patch("conversations.channel_views._account", return_value=None):
            self.assertEqual(telegram_webhook(request, self.account.pk).status_code, 401)
        with patch("conversations.channel_views._account", return_value=self.account), patch(
            "conversations.channel_views.accept_inbound", return_value=(None, True)
        ) as ingest:
            self.assertEqual(telegram_webhook(request, self.account.pk).status_code, 200)
        ingest.assert_called_once_with(self.account, "42", "42:12", "Hola")

    def test_telegram_ignores_groups_and_non_text_updates(self):
        path = f"/channels/telegram/{self.account.pk}/webhook/"
        for payload in (
            {"message": {"message_id": 1, "text": "Hola", "chat": {"id": -5, "type": "group"}}},
            {"edited_message": {"message_id": 1}},
            {"message": {"message_id": 1, "chat": {"id": 5, "type": "private"}}},
        ):
            request = self.factory.post(path, data=json.dumps(payload), content_type="application/json")
            with patch("conversations.channel_views._account", return_value=self.account), patch(
                "conversations.channel_views.accept_inbound"
            ) as ingest:
                self.assertEqual(telegram_webhook(request, self.account.pk).status_code, 200)
            ingest.assert_not_called()

    def test_baileys_accepts_only_personal_chat_and_authenticated_bridge(self):
        path = f"/channels/baileys/{self.account.pk}/inbound/"
        payload = {"chat_id": "123@s.whatsapp.net", "message_id": "abc", "content": "Hola"}
        request = self.factory.post(path, data=json.dumps(payload), content_type="application/json")
        with patch("conversations.channel_views._bridge_account", return_value=None):
            self.assertEqual(baileys_inbound(request, self.account.pk).status_code, 401)
        with patch("conversations.channel_views._bridge_account", return_value=self.account), patch(
            "conversations.channel_views.accept_inbound", return_value=(SimpleNamespace(pk=8), True)
        ) as ingest:
            response = baileys_inbound(request, self.account.pk)
        self.assertEqual(response.status_code, 201)
        ingest.assert_called_once_with(self.account, "123@s.whatsapp.net", "abc", "Hola")

        payload["chat_id"] = "123@g.us"
        request = self.factory.post(path, data=json.dumps(payload), content_type="application/json")
        with patch("conversations.channel_views._bridge_account", return_value=self.account):
            self.assertEqual(baileys_inbound(request, self.account.pk).status_code, 400)

    def test_baileys_ack_requires_matching_lease(self):
        path = f"/channels/baileys/{self.account.pk}/outbound/3/ack/"
        request = self.factory.post(path, data=json.dumps({"lease_token": str(uuid4()), "provider_message_id": "wamid"}), content_type="application/json")
        with patch("conversations.channel_views._bridge_account", return_value=self.account), patch(
            "conversations.channel_views.acknowledge_baileys_reply", return_value=False
        ):
            self.assertEqual(baileys_ack(request, self.account.pk, 3).status_code, 409)


class ChannelPersistenceTests(TenantTestCase):
    def setUp(self):
        with schema_context(get_public_schema_name()):
            self.account = ChannelAccount.objects.create(
                tenant=self.tenant,
                provider="baileys",
                name="test",
                secret_digest=hashlib.sha256(b"secret").hexdigest(),
            )

    def test_repeated_inbound_creates_one_conversation_message_and_run(self):
        with patch.object(settings, "LLM_CHAT_URL", "https://llm.example.test/v1/chat/completions"), patch(
            "conversations.channels.dispatch_assistant_run"
        ):
            first, created = accept_inbound(self.account, "123@s.whatsapp.net", "msg-1", "Hola")
            repeated, repeated_created = accept_inbound(self.account, "123@s.whatsapp.net", "msg-1", "Hola")
            second, second_created = accept_inbound(self.account, "123@s.whatsapp.net", "msg-2", "Otra pregunta")
        self.assertTrue(created)
        self.assertFalse(repeated_created)
        self.assertTrue(second_created)
        self.assertEqual(first.pk, repeated.pk)
        self.assertEqual(first.conversation_id, second.conversation_id)
        with tenant_context(self.tenant):
            self.assertEqual(ChannelConversation.objects.count(), 1)
            self.assertEqual(InboundChannelMessage.objects.count(), 2)
            self.assertEqual(Message.objects.count(), 2)
            self.assertEqual(AssistantRun.objects.count(), 2)

    def test_baileys_reply_lease_and_ack(self):
        with patch.object(settings, "LLM_CHAT_URL", "https://llm.example.test/v1/chat/completions"), patch(
            "conversations.channels.dispatch_assistant_run"
        ):
            user_message, _ = accept_inbound(self.account, "123@s.whatsapp.net", "msg-1", "Hola")
        with tenant_context(self.tenant):
            run = AssistantRun.objects.get(user_message=user_message)
            run.assistant_message = Message.objects.create(
                conversation=user_message.conversation, role=Message.Role.ASSISTANT, content="Respuesta"
            )
            run.status = AssistantRun.Status.COMPLETED
            run.save(update_fields=["assistant_message", "status"])
            queue_channel_reply(run)
            self.assertEqual(OutboundChannelMessage.objects.count(), 1)
        item = lease_baileys_reply(self.account)
        self.assertEqual(item["chat_id"], "123@s.whatsapp.net")
        self.assertEqual(item["content"], "Respuesta")
        self.assertIsNone(lease_baileys_reply(self.account))
        self.assertFalse(acknowledge_baileys_reply(self.account, item["id"], str(uuid4()), "wa-1"))
        self.assertTrue(acknowledge_baileys_reply(self.account, item["id"], item["lease_token"], "wa-1"))
        with tenant_context(self.tenant):
            delivery = OutboundChannelMessage.objects.get(pk=item["id"])
            self.assertEqual(delivery.status, OutboundChannelMessage.Status.SENT)
            self.assertEqual(delivery.provider_message_id, "wa-1")

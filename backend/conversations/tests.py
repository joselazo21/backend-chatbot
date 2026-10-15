import hashlib
import json
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.core.cache.backends.locmem import LocMemCache
from django.utils import timezone
from django_tenants.test.cases import TenantTestCase
from django_tenants.test.client import TenantClient
from django_tenants.utils import get_public_schema_name, schema_context, tenant_context

from customers.models import Customer, Domain, WidgetSite

from .models import Conversation, Message, WidgetSession
from .llm import LLMError
from .models import AssistantRun
from .tasks import dispatch_pending_assistant_runs, generate_assistant_reply


class WidgetSessionTests(TenantTestCase):
    def setUp(self):
        self.cache = LocMemCache("widget-session-tests", {})
        cache_patch = patch("conversations.widget_views.cache", self.cache)
        cache_patch.start()
        self.addCleanup(cache_patch.stop)
        for name in (
            "WIDGET_SESSION_SITE_LIMIT_PER_MINUTE",
            "WIDGET_SESSION_IP_LIMIT_PER_MINUTE",
            "WIDGET_MESSAGE_LIMIT_PER_MINUTE",
        ):
            limit_patch = patch.object(settings, name, 2)
            limit_patch.start()
            self.addCleanup(limit_patch.stop)
        with schema_context(get_public_schema_name()):
            self.site = WidgetSite.objects.create(
                tenant=self.tenant, allowed_origin="https://shop.example.com"
            )
        self.client = TenantClient(self.tenant)
        self.origin = {"HTTP_ORIGIN": "https://shop.example.com"}

    def create_session(self, **extra):
        return self.client.post(
            "/widget/v1/sessions/",
            data=json.dumps({"key": self.site.public_key}),
            content_type="application/json",
            **self.origin,
            **extra,
        )

    def test_session_creates_tenant_conversation_and_authenticates(self):
        preflight = self.client.options("/widget/v1/sessions/", **self.origin)
        self.assertEqual(preflight.status_code, 204)
        self.assertEqual(preflight["Access-Control-Allow-Origin"], self.origin["HTTP_ORIGIN"])
        self.assertIn("Content-Type", preflight["Access-Control-Allow-Headers"])

        response = self.create_session()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response["Access-Control-Allow-Origin"], self.origin["HTTP_ORIGIN"])
        self.assertEqual(response["Cache-Control"], "no-store")
        token = response.json()["access_token"]
        self.assertTrue(token.startswith("ws_"))
        self.assertEqual(response.json()["token_type"], "Bearer")
        with tenant_context(self.tenant):
            session = WidgetSession.objects.get(conversation_id=response.json()["conversation_id"])
            self.assertEqual(session.widget_site_id, self.site.pk)
            self.assertEqual(session.token_digest, hashlib.sha256(token.encode()).hexdigest())
            self.assertNotEqual(session.token_digest, token)

        current_preflight = self.client.options("/widget/v1/sessions/current", **self.origin)
        self.assertEqual(current_preflight.status_code, 204)
        self.assertIn("Authorization", current_preflight["Access-Control-Allow-Headers"])
        self.assertIn("DELETE", current_preflight["Access-Control-Allow-Methods"])
        current = self.client.get(
            "/widget/v1/sessions/current",
            HTTP_AUTHORIZATION=f"Bearer {token}",
            **self.origin,
        )
        self.assertEqual(current.status_code, 200)
        self.assertEqual(current.json()["conversation_id"], response.json()["conversation_id"])
        self.assertEqual(current.json()["expires_at"], response.json()["expires_at"])
        self.assertEqual(
            self.client.delete(
                "/widget/v1/sessions/current",
                HTTP_AUTHORIZATION=f"Bearer {token}",
                **self.origin,
            ).status_code,
            204,
        )
        self.assertEqual(
            self.client.get(
                "/widget/v1/sessions/current",
                HTTP_AUTHORIZATION=f"Bearer {token}",
                **self.origin,
            ).status_code,
            401,
        )

    def test_key_origin_and_token_cannot_cross_boundaries(self):
        wrong_key = self.client.post(
            "/widget/v1/sessions/",
            data=json.dumps({"key": "pk_unknown"}),
            content_type="application/json",
            **self.origin,
        )
        self.assertEqual(wrong_key.status_code, 404)
        self.assertEqual(
            self.client.post(
                "/widget/v1/sessions/",
                data=json.dumps({"key": self.site.public_key}),
                content_type="application/json",
                HTTP_ORIGIN="https://other.example.com",
            ).status_code,
            404,
        )
        self.assertEqual(self.client.post("/widget/v1/sessions/").status_code, 404)
        self.assertEqual(
            self.client.post("/widget/v1/sessions/", data="{}", content_type="application/json", **self.origin).status_code,
            400,
        )
        self.assertEqual(
            self.client.post("/widget/v1/sessions/", data="{}", content_type="text/plain", **self.origin).status_code,
            415,
        )

        response = self.create_session()
        token = response.json()["access_token"]
        self.assertEqual(
            self.client.get("/widget/v1/sessions/current", **self.origin).status_code, 401
        )
        self.assertEqual(
            self.client.get(
                "/widget/v1/sessions/current",
                HTTP_AUTHORIZATION=f"Bearer {self.site.public_key}",
                **self.origin,
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.get(
                "/widget/v1/sessions/current",
                HTTP_AUTHORIZATION=f"Bearer {token}",
                HTTP_ORIGIN="https://other.example.com",
            ).status_code,
            404,
        )
        with tenant_context(self.tenant):
            WidgetSession.objects.filter(conversation_id=response.json()["conversation_id"]).update(
                expires_at=timezone.now() - timedelta(seconds=1)
            )
        self.assertEqual(
            self.client.get(
                "/widget/v1/sessions/current",
                HTTP_AUTHORIZATION=f"Bearer {token}",
                **self.origin,
            ).status_code,
            401,
        )

    def test_disabled_site_and_rate_limit(self):
        self.assertEqual(self.create_session().status_code, 201)
        self.assertEqual(self.create_session().status_code, 201)
        limited = self.create_session()
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited["Retry-After"], "60")
        with tenant_context(self.tenant):
            self.assertEqual(Conversation.objects.count(), 2)

        with schema_context(get_public_schema_name()):
            self.site.is_active = False
            self.site.save(update_fields=["is_active"])
        self.assertEqual(self.create_session().status_code, 404)

    def test_session_token_is_tenant_scoped(self):
        created = self.create_session()
        token = created.json()["access_token"]
        with schema_context(get_public_schema_name()):
            other = Customer.objects.create(schema_name="session_other", name="Other")
            Domain.objects.create(tenant=other, domain="session-other.test.com", is_primary=True)
            WidgetSite.objects.create(tenant=other, allowed_origin=self.origin["HTTP_ORIGIN"])
        settings.ALLOWED_HOSTS.append("session-other.test.com")
        try:
            other_client = TenantClient(other)
            response = other_client.get(
                "/widget/v1/sessions/current",
                HTTP_AUTHORIZATION=f"Bearer {token}",
                **self.origin,
            )
            self.assertEqual(response.status_code, 401)
            self.assertEqual(
                other_client.get(
                    "/widget/v1/messages/",
                    HTTP_AUTHORIZATION=f"Bearer {token}",
                    **self.origin,
                ).status_code,
                401,
            )
        finally:
            settings.ALLOWED_HOSTS.remove("session-other.test.com")
            with schema_context(get_public_schema_name()):
                other.delete(force_drop=True)

    def test_configured_iframe_origin_can_use_existing_token(self):
        with patch.object(settings, "WIDGET_FRONTEND_ORIGIN", "https://widget.example.com"):
            created = self.create_session()
            token = created.json()["access_token"]
            iframe_origin = {"HTTP_ORIGIN": "https://widget.example.com"}
            self.assertEqual(
                self.client.post(
                    "/widget/v1/sessions/",
                    data=json.dumps({"key": self.site.public_key}),
                    content_type="application/json",
                    **iframe_origin,
                ).status_code,
                404,
            )
            self.assertEqual(
                self.client.options("/widget/v1/sessions/current", **iframe_origin).status_code,
                204,
            )
            response = self.client.get(
                "/widget/v1/sessions/current",
                HTTP_AUTHORIZATION=f"Bearer {token}",
                **iframe_origin,
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["conversation_id"], created.json()["conversation_id"])

    def test_cache_failure_does_not_create_a_session(self):
        with patch.object(self.cache, "add", side_effect=ConnectionError("cache unavailable")), patch(
            "conversations.widget_views.logger.exception"
        ) as report:
            response = self.create_session()
        report.assert_called_once()
        self.assertEqual(response.status_code, 503)
        with tenant_context(self.tenant):
            self.assertEqual(Conversation.objects.count(), 0)

    def test_messages_are_bound_to_the_authenticated_conversation(self):
        created = self.create_session().json()
        token = created["access_token"]
        auth = {"HTTP_AUTHORIZATION": f"Bearer {token}", **self.origin}
        preflight = self.client.options("/widget/v1/messages/", **self.origin)
        self.assertEqual(preflight.status_code, 204)
        self.assertIn("Authorization", preflight["Access-Control-Allow-Headers"])
        self.assertIn("Content-Type", preflight["Access-Control-Allow-Headers"])

        saved = self.client.post(
            "/widget/v1/messages/",
            data=json.dumps({"content": "  Hola  "}),
            content_type="application/json",
            **auth,
        )
        self.assertEqual(saved.status_code, 201)
        self.assertEqual(saved.json()["role"], "user")
        self.assertEqual(saved.json()["content"], "Hola")
        with tenant_context(self.tenant):
            message = Message.objects.get(pk=saved.json()["id"])
            self.assertEqual(str(message.conversation_id), created["conversation_id"])

        listing = self.client.get("/widget/v1/messages/", **auth)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["messages"], [saved.json()])
        self.assertEqual(listing.json()["next_cursor"], saved.json()["id"])
        self.assertFalse(listing.json()["has_more"])
        self.assertEqual(
            self.client.get(
                "/widget/v1/messages/", {"after_id": saved.json()["id"]}, **auth
            ).json()["messages"],
            [],
        )

        other_token = self.create_session().json()["access_token"]
        self.assertEqual(
            self.client.get(
                "/widget/v1/messages/",
                HTTP_AUTHORIZATION=f"Bearer {other_token}",
                **self.origin,
            ).json()["messages"],
            [],
        )
        self.assertEqual(
            self.client.get(
                f"/widget/v1/messages/{saved.json()['id']}/generation/",
                HTTP_AUTHORIZATION=f"Bearer {other_token}",
                **self.origin,
            ).status_code,
            404,
        )
        self.assertEqual(self.client.get("/widget/v1/messages/", **self.origin).status_code, 401)
        self.assertEqual(
            self.client.get(
                "/widget/v1/messages/",
                HTTP_AUTHORIZATION=f"Bearer {token}",
                HTTP_ORIGIN="https://other.example.com",
            ).status_code,
            404,
        )

    def test_message_validation_rate_limit_and_revocation(self):
        token = self.create_session().json()["access_token"]
        auth = {"HTTP_AUTHORIZATION": f"Bearer {token}", **self.origin}
        self.assertEqual(
            self.client.post(
                "/widget/v1/messages/", data="{}", content_type="text/plain", **auth
            ).status_code,
            415,
        )
        for payload in (
            {"content": " "},
            {"content": "x" * 4001},
            {"content": "hi\x00"},
            {"content": "\ud800"},
            {"role": "assistant", "content": "hi"},
        ):
            self.assertEqual(
                self.client.post(
                    "/widget/v1/messages/", data=json.dumps(payload), content_type="application/json", **auth
                ).status_code,
                400,
            )
        self.assertEqual(
            self.client.post(
                "/widget/v1/messages/", data="{", content_type="application/json", **auth
            ).status_code,
            400,
        )
        for cursor in ("-1", "abc", "9999999999999999999"):
            self.assertEqual(
                self.client.get("/widget/v1/messages/", {"after_id": cursor}, **auth).status_code,
                400,
            )
        for _ in range(2):
            self.assertEqual(
                self.client.post(
                    "/widget/v1/messages/",
                    data=json.dumps({"content": "Hola"}),
                    content_type="application/json",
                    **auth,
                ).status_code,
                201,
            )
        limited = self.client.post(
            "/widget/v1/messages/",
            data=json.dumps({"content": "Hola"}),
            content_type="application/json",
            **auth,
        )
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited["Retry-After"], "60")
        with tenant_context(self.tenant):
            self.assertEqual(Message.objects.count(), 2)
        self.assertEqual(self.client.delete("/widget/v1/sessions/current", **auth).status_code, 204)
        self.assertEqual(self.client.get("/widget/v1/messages/", **auth).status_code, 401)

    def test_message_history_is_paginated_and_includes_assistant_entries(self):
        token = self.create_session().json()["access_token"]
        auth = {"HTTP_AUTHORIZATION": f"Bearer {token}", **self.origin}
        with tenant_context(self.tenant):
            session = WidgetSession.objects.get(
                token_digest=hashlib.sha256(token.encode()).hexdigest()
            )
            Message.objects.bulk_create(
                [
                    Message(
                        conversation_id=session.conversation_id,
                        role=Message.Role.ASSISTANT if number % 2 else Message.Role.USER,
                        content=f"Mensaje {number}",
                    )
                    for number in range(51)
                ]
            )
        first = self.client.get("/widget/v1/messages/", **auth).json()
        self.assertEqual(len(first["messages"]), 50)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["messages"][1]["role"], "assistant")
        second = self.client.get(
            "/widget/v1/messages/", {"after_id": first["next_cursor"]}, **auth
        ).json()
        self.assertEqual(len(second["messages"]), 1)
        self.assertFalse(second["has_more"])
        self.assertEqual(second["messages"][0]["content"], "Mensaje 50")

    def test_unconfigured_model_keeps_message_without_queueing(self):
        token = self.create_session().json()["access_token"]
        with patch.object(settings, "LLM_CHAT_URL", ""), patch(
            "conversations.widget_views.dispatch_assistant_run"
        ) as dispatch:
            response = self.client.post(
                "/widget/v1/messages/",
                data=json.dumps({"content": "Hola"}),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {token}",
                **self.origin,
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["generation_status"], "unconfigured")
        generation_url = f"/widget/v1/messages/{response.json()['id']}/generation/"
        self.assertEqual(
            self.client.get(
                generation_url, HTTP_AUTHORIZATION=f"Bearer {token}", **self.origin
            ).json()["status"],
            "unconfigured",
        )
        dispatch.assert_not_called()
        with tenant_context(self.tenant):
            self.assertFalse(AssistantRun.objects.exists())

    def test_worker_generates_reply_once_with_ordered_history(self):
        token = self.create_session().json()["access_token"]
        auth = {"HTTP_AUTHORIZATION": f"Bearer {token}", **self.origin}
        with patch.object(settings, "LLM_CHAT_URL", "http://llm.railway.internal:8000/v1/chat/completions"), patch(
            "conversations.widget_views.dispatch_assistant_run"
        ) as dispatch:
            with self.captureOnCommitCallbacks(execute=True):
                first = self.client.post(
                    "/widget/v1/messages/", data=json.dumps({"content": "Hola"}),
                    content_type="application/json", **auth,
                )
            self.assertEqual(first.json()["generation_status"], "pending")
            generation_url = f"/widget/v1/messages/{first.json()['id']}/generation/"
            self.assertEqual(self.client.options(generation_url, **self.origin).status_code, 204)
            self.assertEqual(self.client.get(generation_url, **auth).json()["status"], "pending")
            dispatch.assert_called_once()
            with tenant_context(self.tenant):
                first_run = AssistantRun.objects.get(user_message_id=first.json()["id"])
            with patch("conversations.tasks.generate_reply", return_value="Hola, ¿qué necesitas?") as llm:
                generate_assistant_reply(self.tenant.pk, first_run.pk)
                generate_assistant_reply(self.tenant.pk, first_run.pk)
            llm.assert_called_once_with([{"role": "user", "content": "Hola"}])
            self.assertEqual(self.client.get(generation_url, **auth).json()["status"], "completed")

            with self.captureOnCommitCallbacks(execute=True):
                second = self.client.post(
                    "/widget/v1/messages/", data=json.dumps({"content": "Horario"}),
                    content_type="application/json", **auth,
                )
            with tenant_context(self.tenant):
                second_run = AssistantRun.objects.get(user_message_id=second.json()["id"])
            with patch("conversations.tasks.generate_reply", return_value="De 9 a 5") as llm:
                generate_assistant_reply(self.tenant.pk, second_run.pk)
            llm.assert_called_once_with([
                {"role": "user", "content": "Hola"},
                {"role": "assistant", "content": "Hola, ¿qué necesitas?"},
                {"role": "user", "content": "Horario"},
            ])

        listing = self.client.get("/widget/v1/messages/", **auth).json()["messages"]
        self.assertEqual([message["role"] for message in listing], ["user", "assistant", "user", "assistant"])
        self.assertEqual(listing[0]["generation_status"], "completed")
        self.assertEqual(listing[2]["generation_status"], "completed")
        with tenant_context(self.tenant):
            self.assertEqual(Message.objects.filter(role=Message.Role.ASSISTANT).count(), 2)

    def test_failed_generation_is_retryable_and_recovered_by_dispatcher(self):
        token = self.create_session().json()["access_token"]
        with patch.object(settings, "LLM_CHAT_URL", "http://llm.railway.internal:8000/v1/chat/completions"), patch(
            "conversations.widget_views.dispatch_assistant_run"
        ):
            response = self.client.post(
                "/widget/v1/messages/", data=json.dumps({"content": "Hola"}),
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {token}", **self.origin,
            )
            with tenant_context(self.tenant):
                run = AssistantRun.objects.get(user_message_id=response.json()["id"])
            with patch("conversations.tasks.generate_reply", side_effect=LLMError("upstream_unavailable", retryable=True)):
                generate_assistant_reply(self.tenant.pk, run.pk)
            with tenant_context(self.tenant):
                run.refresh_from_db()
                self.assertEqual(run.status, AssistantRun.Status.PENDING)
                self.assertEqual(run.attempts, 1)
                self.assertIsNotNone(run.next_attempt_at)
                run.next_attempt_at = timezone.now() - timedelta(seconds=1)
                run.save(update_fields=["next_attempt_at"])
            with patch("conversations.tasks.dispatch_assistant_run") as dispatch:
                self.assertGreaterEqual(dispatch_pending_assistant_runs(), 1)
            dispatch.assert_any_call(self.tenant.pk, run.pk)
            with patch("conversations.tasks.generate_reply", return_value="Respuesta"):
                generate_assistant_reply(self.tenant.pk, run.pk)
            with tenant_context(self.tenant):
                run.refresh_from_db()
                self.assertEqual(run.status, AssistantRun.Status.COMPLETED)
                self.assertEqual(run.assistant_message.content, "Respuesta")

    def test_worker_does_not_call_model_after_session_revocation(self):
        token = self.create_session().json()["access_token"]
        auth = {"HTTP_AUTHORIZATION": f"Bearer {token}", **self.origin}
        with patch.object(settings, "LLM_CHAT_URL", "http://llm.railway.internal:8000/v1/chat/completions"), patch(
            "conversations.widget_views.dispatch_assistant_run"
        ), patch("conversations.tasks.generate_reply") as llm:
            created = self.client.post(
                "/widget/v1/messages/", data=json.dumps({"content": "Hola"}),
                content_type="application/json", **auth,
            )
            with tenant_context(self.tenant):
                run = AssistantRun.objects.get(user_message_id=created.json()["id"])
            self.assertEqual(self.client.delete("/widget/v1/sessions/current", **auth).status_code, 204)
            generate_assistant_reply(self.tenant.pk, run.pk)
        llm.assert_not_called()
        with tenant_context(self.tenant):
            run.refresh_from_db()
            self.assertEqual(run.status, AssistantRun.Status.FAILED)
            self.assertEqual(run.error_code, "session_inactive")

    def test_pending_run_survives_broker_publish_failure(self):
        token = self.create_session().json()["access_token"]
        with patch.object(settings, "LLM_CHAT_URL", "http://llm.railway.internal:8000/v1/chat/completions"), patch(
            "conversations.tasks.generate_assistant_reply.delay",
            side_effect=ConnectionError("broker unavailable"),
        ), patch("conversations.tasks.logger.exception"):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    "/widget/v1/messages/", data=json.dumps({"content": "Hola"}),
                    content_type="application/json",
                    HTTP_AUTHORIZATION=f"Bearer {token}", **self.origin,
                )
        self.assertEqual(response.status_code, 201)
        with tenant_context(self.tenant):
            run = AssistantRun.objects.get(user_message_id=response.json()["id"])
            self.assertEqual(run.status, AssistantRun.Status.PENDING)
        with patch.object(settings, "LLM_CHAT_URL", "http://llm.railway.internal:8000/v1/chat/completions"), patch(
            "conversations.tasks.generate_assistant_reply.delay"
        ) as publish:
            self.assertGreaterEqual(dispatch_pending_assistant_runs(), 1)
        publish.assert_any_call(self.tenant.pk, run.pk)

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

from .models import Conversation, WidgetSession


class WidgetSessionTests(TenantTestCase):
    def setUp(self):
        self.cache = LocMemCache("widget-session-tests", {})
        cache_patch = patch("conversations.widget_views.cache", self.cache)
        cache_patch.start()
        self.addCleanup(cache_patch.stop)
        for name in ("WIDGET_SESSION_SITE_LIMIT_PER_MINUTE", "WIDGET_SESSION_IP_LIMIT_PER_MINUTE"):
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

import os
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import Client
from django_tenants.test.cases import TenantTestCase
from django_tenants.test.client import TenantClient
from django_tenants.utils import get_public_schema_name, schema_context, tenant_context

from conversations.models import Conversation
from customers.models import Customer, Domain, WidgetSite


class TenantIsolationTests(TenantTestCase):
    @classmethod
    def setup_tenant(cls, tenant):
        tenant.name = "Test A"
        tenant.service_token_hash = make_password("token-for-a")

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with schema_context(get_public_schema_name()):
            cls.other_tenant = Customer.objects.create(
                schema_name="test_b",
                name="Test B",
                service_token_hash=make_password("token-for-b"),
            )
            cls.other_domain = Domain.objects.create(
                tenant=cls.other_tenant,
                domain="tenant-b.test.com",
                is_primary=True,
            )
        settings.ALLOWED_HOSTS.append(cls.other_domain.domain)

    @classmethod
    def tearDownClass(cls):
        connection.set_schema_to_public()
        settings.ALLOWED_HOSTS.remove(cls.other_domain.domain)
        cls.other_domain.delete()
        cls.other_tenant.delete(force_drop=True)
        super().tearDownClass()

    def test_conversations_are_separate_by_schema(self):
        with tenant_context(self.tenant):
            conversation_a = Conversation.objects.create()

        with tenant_context(self.other_tenant):
            self.assertFalse(Conversation.objects.filter(pk=conversation_a.pk).exists())
            conversation_b = Conversation.objects.create()

        with tenant_context(self.tenant):
            self.assertFalse(Conversation.objects.filter(pk=conversation_b.pk).exists())
            self.assertEqual(Conversation.objects.count(), 1)

    def test_domain_routes_to_correct_tenant(self):
        for tenant in (self.tenant, self.other_tenant):
            response = TenantClient(tenant).get("/health/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"status": "ok"})
            self.assertEqual(response.wsgi_request.tenant.schema_name, tenant.schema_name)

    def test_public_bootstrap_is_repeatable(self):
        with schema_context(get_public_schema_name()):
            call_command("bootstrap_public_tenant", domain="localhost", verbosity=0)
            call_command("bootstrap_public_tenant", domain="localhost", verbosity=0)
            public_tenant = Customer.objects.get(schema_name=get_public_schema_name())
            self.assertEqual(Domain.objects.filter(tenant=public_tenant, domain="localhost").count(), 1)

            with connection.cursor() as cursor:
                cursor.execute("SELECT to_regclass('public.conversations_conversation')")
                self.assertIsNone(cursor.fetchone()[0])

    def test_prepare_railway_keeps_domain_owned_by_a_customer(self):
        customer_domain = Domain.objects.get(tenant=self.tenant).domain
        with patch.dict(os.environ, {"RAILWAY_PUBLIC_DOMAIN": customer_domain}):
            with patch("customers.management.commands.prepare_railway.call_command") as mocked:
                call_command("prepare_railway", stdout=StringIO())

        bootstrapped = [call.kwargs["domain"] for call in mocked.call_args_list if call.args[0] == "bootstrap_public_tenant"]
        self.assertEqual(bootstrapped, ["healthcheck.railway.app"])
        self.assertEqual(Domain.objects.get(domain=customer_domain).tenant_id, self.tenant.pk)

    def test_service_api_is_bound_to_customer_and_domain(self):
        client_a = TenantClient(self.tenant)
        client_b = TenantClient(self.other_tenant)
        auth_a = {"HTTP_AUTHORIZATION": "Bearer token-for-a"}
        auth_b = {"HTTP_AUTHORIZATION": "Bearer token-for-b"}

        self.assertEqual(client_a.post("/api/v1/conversations/").status_code, 401)
        created = client_a.post(
            "/api/v1/conversations/", data="{}", content_type="application/json", **auth_a
        )
        self.assertEqual(created.status_code, 201)
        detail_url = created["Location"]
        self.assertEqual(client_a.get(detail_url, **auth_a).json()["id"], created.json()["id"])
        self.assertEqual(client_b.get(detail_url, **auth_a).status_code, 401)
        self.assertEqual(client_b.get(detail_url, **auth_b).status_code, 404)
        self.assertEqual(client_a.get(detail_url, **auth_b).status_code, 401)

    def test_customer_creation_is_idempotent_and_token_rotates(self):
        output = StringIO()
        with schema_context(get_public_schema_name()):
            try:
                call_command(
                    "create_customer",
                    schema="test_c",
                    name="Test C",
                    domain="tenant-c.test.com",
                    stdout=output,
                )
                customer = Customer.objects.get(schema_name="test_c")
                domain = Domain.objects.get(domain="tenant-c.test.com")
                self.assertEqual(domain.tenant_id, customer.pk)
                original_token = output.getvalue().split("Service token (shown once): ")[1].strip()
                self.assertTrue(check_password(original_token, customer.service_token_hash))

                call_command(
                    "create_customer",
                    schema="test_c",
                    name="Test C",
                    domain="tenant-c.test.com",
                    stdout=StringIO(),
                )
                with self.assertRaises(CommandError):
                    call_command(
                        "create_customer",
                        schema="test_c",
                        name="Test C",
                        domain="wrong.test.com",
                    )

                rotated_output = StringIO()
                call_command("rotate_customer_token", schema="test_c", stdout=rotated_output)
                customer.refresh_from_db()
                new_token = rotated_output.getvalue().split("Service token (shown once): ")[1].strip()
                self.assertFalse(check_password(original_token, customer.service_token_hash))
                self.assertTrue(check_password(new_token, customer.service_token_hash))
            finally:
                Domain.objects.filter(domain="tenant-c.test.com").delete()
                created_customer = Customer.objects.filter(schema_name="test_c").first()
                if created_customer:
                    created_customer.delete(force_drop=True)

    def test_widget_bootstrap_resolves_only_registered_site(self):
        with schema_context(get_public_schema_name()):
            call_command("bootstrap_public_tenant", domain="localhost", stdout=StringIO())
            output = StringIO()
            call_command(
                "create_widget_site",
                schema=self.tenant.schema_name,
                origin="https://acme.example.com",
                stdout=output,
            )
            site = WidgetSite.objects.get(tenant=self.tenant)
            self.assertIn(site.public_key, output.getvalue())
            call_command(
                "create_widget_site",
                schema=self.tenant.schema_name,
                origin="https://acme.example.com",
                stdout=StringIO(),
            )
            self.assertEqual(WidgetSite.objects.filter(tenant=self.tenant).count(), 1)

        public_client = Client(HTTP_HOST="localhost")
        url = "/widget/v1/bootstrap"
        response = public_client.get(
            url, {"key": site.public_key}, HTTP_ORIGIN="https://acme.example.com"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"api_base_url": "http://tenant.test.com"})
        self.assertEqual(response["Access-Control-Allow-Origin"], "https://acme.example.com")
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertIn("Origin", response["Vary"])
        self.assertEqual(
            TenantClient(self.tenant).post(
                "/api/v1/conversations/",
                HTTP_AUTHORIZATION=f"Bearer {site.public_key}",
            ).status_code,
            401,
        )

        self.assertEqual(
            public_client.get(url, {"key": site.public_key}, HTTP_ORIGIN="https://other.example.com").status_code,
            404,
        )
        self.assertEqual(public_client.get(url, {"key": site.public_key}).status_code, 404)
        self.assertEqual(
            public_client.get(
                url, {"key": site.public_key}, HTTP_ORIGIN="http://acme.example.com"
            ).status_code,
            404,
        )
        self.assertEqual(
            TenantClient(self.tenant).get(
                url, {"key": site.public_key}, HTTP_ORIGIN="https://acme.example.com"
            ).status_code,
            404,
        )

        with schema_context(get_public_schema_name()):
            site.is_active = False
            site.save(update_fields=["is_active"])
        self.assertEqual(
            public_client.get(url, {"key": site.public_key}, HTTP_ORIGIN="https://acme.example.com").status_code,
            404,
        )

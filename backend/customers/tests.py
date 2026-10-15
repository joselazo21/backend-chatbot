from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django_tenants.test.cases import TenantTestCase
from django_tenants.test.client import TenantClient
from django_tenants.utils import get_public_schema_name, schema_context, tenant_context

from conversations.models import Conversation
from customers.models import Customer, Domain


class TenantIsolationTests(TenantTestCase):
    @classmethod
    def setup_tenant(cls, tenant):
        tenant.name = "Test A"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with schema_context(get_public_schema_name()):
            cls.other_tenant = Customer.objects.create(schema_name="test_b", name="Test B")
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

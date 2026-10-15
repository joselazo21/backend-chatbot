from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import get_public_schema_name

from customers.models import Customer, Domain
from customers.validation import normalize_hostname


class Command(BaseCommand):
    help = "Create the public tenant and its local domain after shared migrations."

    def add_arguments(self, parser):
        parser.add_argument("--domain", default="localhost")

    def handle(self, *args, **options):
        try:
            domain_name = normalize_hostname(options["domain"])
        except ValueError as error:
            raise CommandError(str(error)) from error

        public_tenant, _ = Customer.objects.get_or_create(
            schema_name=get_public_schema_name(),
            defaults={"name": "Public"},
        )
        domain, created = Domain.objects.get_or_create(
            domain=domain_name,
            defaults={"tenant": public_tenant, "is_primary": True},
        )
        if domain.tenant_id != public_tenant.pk:
            raise CommandError(f"Domain {domain_name} already belongs to another tenant")

        action = "created" if created else "already exists"
        self.stdout.write(self.style.SUCCESS(f"Public domain {domain_name} {action}"))

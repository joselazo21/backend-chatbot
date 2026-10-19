import os

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import get_public_schema_name

from customers.models import Domain
from customers.validation import normalize_hostname


class Command(BaseCommand):
    help = "Apply migrations and register the public Railway and healthcheck domains."

    def handle(self, *args, **options):
        domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
        if not domain:
            raise CommandError("RAILWAY_PUBLIC_DOMAIN is required")
        try:
            hostname = normalize_hostname(domain)
        except ValueError as error:
            raise CommandError(str(error)) from error

        call_command("migrate_schemas", shared=True, interactive=False)
        # A customer may own the Railway domain (e.g. a demo customer); do not take it back.
        owner = Domain.objects.select_related("tenant").filter(domain=hostname).first()
        if owner is not None and owner.tenant.schema_name != get_public_schema_name():
            self.stdout.write(
                self.style.WARNING(f"Domain {hostname} belongs to customer {owner.tenant.schema_name}; keeping it")
            )
        else:
            call_command("bootstrap_public_tenant", domain=domain)
        call_command("bootstrap_public_tenant", domain="healthcheck.railway.app")
        call_command("migrate_schemas", interactive=False)

import os

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Apply migrations and register the public Railway and healthcheck domains."

    def handle(self, *args, **options):
        domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
        if not domain:
            raise CommandError("RAILWAY_PUBLIC_DOMAIN is required")

        call_command("migrate_schemas", shared=True, interactive=False)
        call_command("bootstrap_public_tenant", domain=domain)
        call_command("bootstrap_public_tenant", domain="healthcheck.railway.app")
        call_command("migrate_schemas", interactive=False)

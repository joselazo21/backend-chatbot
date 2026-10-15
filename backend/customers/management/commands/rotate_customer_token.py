import secrets

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import get_public_schema_name, schema_context

from customers.models import Customer


class Command(BaseCommand):
    help = "Replace a customer's private service token and show the new value once."

    def add_arguments(self, parser):
        parser.add_argument("--schema", required=True)

    def handle(self, *args, **options):
        schema_name = options["schema"].strip()
        if schema_name == get_public_schema_name():
            raise CommandError("The public tenant has no service token")

        with schema_context(get_public_schema_name()):
            try:
                customer = Customer.objects.get(schema_name=schema_name)
            except Customer.DoesNotExist as error:
                raise CommandError("Unknown customer schema") from error
            token = secrets.token_urlsafe(32)
            customer.service_token_hash = make_password(token)
            customer.save(update_fields=["service_token_hash"])

        self.stdout.write(self.style.SUCCESS(f"Rotated token for {schema_name}"))
        self.stdout.write(f"Service token (shown once): {token}")

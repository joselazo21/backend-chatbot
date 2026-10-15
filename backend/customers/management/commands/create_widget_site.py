from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import get_public_schema_name, schema_context

from customers.models import Customer, WidgetSite
from customers.validation import normalize_origin


class Command(BaseCommand):
    help = "Register a website that may embed a customer's public widget."

    def add_arguments(self, parser):
        parser.add_argument("--schema", required=True)
        parser.add_argument("--origin", required=True)

    def handle(self, *args, **options):
        try:
            origin = normalize_origin(options["origin"])
        except ValueError as error:
            raise CommandError(str(error)) from error

        schema_name = options["schema"].strip()
        if schema_name == get_public_schema_name():
            raise CommandError("The public tenant cannot have a widget site")

        with schema_context(get_public_schema_name()):
            try:
                tenant = Customer.objects.get(schema_name=schema_name)
            except Customer.DoesNotExist as error:
                raise CommandError("Unknown customer schema") from error
            site, created = WidgetSite.objects.get_or_create(
                tenant=tenant, allowed_origin=origin
            )

        action = "created" if created else "already exists"
        self.stdout.write(self.style.SUCCESS(f"Widget site {origin} {action}"))
        self.stdout.write(f"Public widget key: {site.public_key}")

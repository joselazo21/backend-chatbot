import secrets

from django.contrib.auth.hashers import make_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django_tenants.utils import get_public_schema_name, schema_context

from customers.models import Customer, Domain
from customers.validation import normalize_hostname


class Command(BaseCommand):
    help = "Create a customer schema, domain and private service token."

    def add_arguments(self, parser):
        parser.add_argument("--schema", required=True)
        parser.add_argument("--name", required=True)
        parser.add_argument("--domain", required=True)

    def handle(self, *args, **options):
        schema_name = options["schema"].strip()
        name = options["name"].strip()
        try:
            domain_name = normalize_hostname(options["domain"])
        except ValueError as error:
            raise CommandError(str(error)) from error
        if not name or schema_name == get_public_schema_name():
            raise CommandError("Provide a name and a non-public schema")

        with schema_context(get_public_schema_name()):
            existing = Customer.objects.filter(schema_name=schema_name).first()
            if existing:
                if existing.name != name or not Domain.objects.filter(
                    tenant=existing, domain=domain_name
                ).exists():
                    raise CommandError("Existing customer does not match name and domain")
                self.stdout.write(self.style.SUCCESS("Customer already exists; token unchanged"))
                return

            if Domain.objects.filter(domain=domain_name).exists():
                raise CommandError("Domain already belongs to another customer")

            token = secrets.token_urlsafe(32)
            customer = Customer(
                schema_name=schema_name,
                name=name,
                service_token_hash=make_password(token),
            )
            try:
                customer.full_clean()
            except ValidationError as error:
                raise CommandError(str(error)) from error

            with transaction.atomic():
                customer.save()
                Domain.objects.create(tenant=customer, domain=domain_name, is_primary=True)

        self.stdout.write(self.style.SUCCESS(f"Created customer {schema_name} on {domain_name}"))
        self.stdout.write(f"Service token (shown once): {token}")

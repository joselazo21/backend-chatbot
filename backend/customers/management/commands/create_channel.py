import hashlib
import os
import secrets

from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import get_public_schema_name, schema_context

from customers.models import ChannelAccount, Customer


class Command(BaseCommand):
    help = "Register a Telegram bot or one Baileys linked device for a tenant."

    def add_arguments(self, parser):
        parser.add_argument("--schema", required=True)
        parser.add_argument("--provider", required=True, choices=["telegram", "baileys"])
        parser.add_argument("--name", required=True)
        parser.add_argument("--telegram-token-env", default="")

    def handle(self, *args, **options):
        provider = options["provider"]
        token_env = options["telegram_token_env"].strip()
        if provider == "telegram" and (not token_env or not token_env.isidentifier() or not os.getenv(token_env)):
            raise CommandError("Telegram requires --telegram-token-env set to an existing environment variable")
        if provider == "baileys" and token_env:
            raise CommandError("--telegram-token-env is only for Telegram")
        secret = secrets.token_urlsafe(32)
        with schema_context(get_public_schema_name()):
            tenant = Customer.objects.filter(schema_name=options["schema"]).first()
            if tenant is None or tenant.schema_name == get_public_schema_name():
                raise CommandError("Unknown tenant schema")
            if ChannelAccount.objects.filter(tenant=tenant, provider=provider, name=options["name"]).exists():
                raise CommandError("Channel name already exists for this tenant")
            account = ChannelAccount.objects.create(
                tenant=tenant,
                provider=provider,
                name=options["name"],
                secret_digest=hashlib.sha256(secret.encode()).hexdigest(),
                telegram_token_env=token_env,
            )
        self.stdout.write(self.style.SUCCESS(f"Created {provider} channel {account.pk}"))
        self.stdout.write(f"Channel secret (shown once): {secret}")
        if provider == "telegram":
            self.stdout.write("Use this secret as Telegram's secret_token when registering the webhook.")
        else:
            self.stdout.write("Set BAILEYS_CHANNEL_ID and BAILEYS_CHANNEL_SECRET in the bridge environment.")

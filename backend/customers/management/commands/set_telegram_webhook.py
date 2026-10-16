import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.core.management.base import BaseCommand, CommandError
from django_tenants.utils import get_public_schema_name, schema_context

from customers.models import ChannelAccount
from conversations.channels import secret_matches


class Command(BaseCommand):
    help = "Register a channel's public HTTPS webhook with Telegram."

    def add_arguments(self, parser):
        parser.add_argument("--account", required=True)
        parser.add_argument("--base-url", required=True)
        parser.add_argument("--secret-env", default="TELEGRAM_WEBHOOK_SECRET")

    def handle(self, *args, **options):
        base_url = options["base_url"].rstrip("/")
        if not base_url.startswith("https://"):
            raise CommandError("--base-url must use HTTPS")
        secret = os.environ.get(options["secret_env"], "")
        if not secret:
            raise CommandError(f"Environment variable {options['secret_env']} is empty")
        with schema_context(get_public_schema_name()):
            account = ChannelAccount.objects.filter(
                pk=options["account"], provider="telegram", is_active=True
            ).first()
        if account is None or not secret_matches(secret, account.secret_digest):
            raise CommandError("Unknown channel or invalid channel secret")
        token = os.environ.get(account.telegram_token_env, "")
        if not token:
            raise CommandError(f"Environment variable {account.telegram_token_env} is empty")
        request = Request(
            f"https://api.telegram.org/bot{token}/setWebhook",
            data=json.dumps({
                "url": f"{base_url}/channels/telegram/{account.pk}/webhook/",
                "secret_token": secret,
                "allowed_updates": ["message"],
            }).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=15) as response:
                result = json.load(response)
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            raise CommandError(f"Telegram registration failed: {type(error).__name__}") from error
        if not result.get("ok"):
            raise CommandError("Telegram rejected the webhook registration")
        self.stdout.write(self.style.SUCCESS("Telegram webhook registered"))

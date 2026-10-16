import secrets
import uuid

from django.db import models
from django_tenants.models import DomainMixin, TenantMixin


class Customer(TenantMixin):
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    service_token_hash = models.CharField(max_length=128, blank=True)

    auto_create_schema = True
    auto_drop_schema = False

    def __str__(self):
        return self.name


class Domain(DomainMixin):
    pass


def generate_widget_key():
    return "pk_" + secrets.token_urlsafe(24)


class WidgetSite(models.Model):
    tenant = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="widget_sites")
    public_key = models.CharField(max_length=64, unique=True, default=generate_widget_key)
    allowed_origin = models.URLField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "allowed_origin"], name="unique_widget_site_per_tenant"
            )
        ]


class ChannelAccount(models.Model):
    class Provider(models.TextChoices):
        TELEGRAM = "telegram", "Telegram"
        BAILEYS = "baileys", "WhatsApp (Baileys)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="channel_accounts")
    provider = models.CharField(max_length=16, choices=Provider.choices)
    name = models.CharField(max_length=100)
    secret_digest = models.CharField(max_length=64)
    telegram_token_env = models.CharField(max_length=100, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "provider", "name"], name="unique_channel_account_name")
        ]

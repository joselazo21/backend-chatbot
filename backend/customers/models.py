import secrets

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

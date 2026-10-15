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

from urllib.parse import urlsplit, urlunsplit

from django.http import Http404, JsonResponse
from django.views.decorators.http import require_GET
from django_tenants.utils import get_public_schema_name

from customers.models import Domain, WidgetSite
from customers.validation import normalize_origin


@require_GET
def widget_bootstrap(request):
    if request.tenant.schema_name != get_public_schema_name():
        raise Http404

    key = request.GET.get("key", "")
    try:
        origin = normalize_origin(request.headers.get("Origin", ""))
    except ValueError:
        raise Http404 from None
    if not key.startswith("pk_") or len(key) > 64:
        raise Http404

    site = WidgetSite.objects.select_related("tenant").filter(
        public_key=key, allowed_origin=origin, is_active=True
    ).first()
    if site is None:
        raise Http404
    domain = Domain.objects.filter(tenant=site.tenant, is_primary=True).first()
    if domain is None:
        raise Http404

    public_url = urlsplit(request.build_absolute_uri())
    port = f":{public_url.port}" if public_url.port is not None else ""
    api_base_url = urlunsplit((public_url.scheme, f"{domain.domain}{port}", "", "", ""))
    response = JsonResponse({"api_base_url": api_base_url})
    response["Access-Control-Allow-Origin"] = origin
    response["Vary"] = "Origin"
    response["Cache-Control"] = "no-store"
    return response

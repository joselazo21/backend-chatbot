import hashlib
import json
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_tenants.utils import get_public_schema_name

from customers.models import WidgetSite
from customers.validation import normalize_origin

from .models import Conversation, WidgetSession

logger = logging.getLogger(__name__)


def _site_for_request(request):
    if request.tenant.schema_name == get_public_schema_name():
        raise Http404
    origin = _request_origin(request)
    site = WidgetSite.objects.filter(
        tenant=request.tenant, allowed_origin=origin, is_active=True
    ).first()
    if site is None:
        raise Http404
    return site, origin


def _request_origin(request):
    try:
        return normalize_origin(request.headers.get("Origin", ""))
    except ValueError:
        raise Http404 from None


def _cors(response, origin, method, headers):
    response["Access-Control-Allow-Origin"] = origin
    response["Access-Control-Allow-Methods"] = f"{method}, OPTIONS"
    response["Access-Control-Allow-Headers"] = headers
    response["Cache-Control"] = "no-store"
    patch_vary_headers(response, ["Origin"])
    return response


def _within_limit(key, limit):
    if cache.add(key, 1, timeout=60):
        return True
    return cache.incr(key) <= limit


def _session_creation_allowed(site, request):
    ip = request.META.get("REMOTE_ADDR", "")
    ip_digest = hashlib.sha256(ip.encode("utf-8")).hexdigest()[:24]
    try:
        site_ok = _within_limit(
            f"widget-session:site:{site.pk}", settings.WIDGET_SESSION_SITE_LIMIT_PER_MINUTE
        )
        ip_ok = _within_limit(
            f"widget-session:ip:{site.pk}:{ip_digest}",
            settings.WIDGET_SESSION_IP_LIMIT_PER_MINUTE,
        )
    except Exception:
        logger.exception("Widget session rate limiter unavailable")
        return None
    return site_ok and ip_ok


@csrf_exempt
@require_http_methods(["POST", "OPTIONS"])
def create_widget_session(request):
    site, origin = _site_for_request(request)
    if request.method == "OPTIONS":
        return _cors(HttpResponse(status=204), origin, "POST", "Content-Type")

    if request.content_type != "application/json":
        return _cors(JsonResponse({"error": "json_required"}, status=415), origin, "POST", "Content-Type")
    try:
        payload = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return _cors(JsonResponse({"error": "invalid_json"}, status=400), origin, "POST", "Content-Type")
    if not isinstance(payload, dict) or set(payload) != {"key"}:
        return _cors(JsonResponse({"error": "invalid_request"}, status=400), origin, "POST", "Content-Type")
    key = payload["key"]
    if not isinstance(key, str) or len(key) > 64 or not secrets.compare_digest(key, site.public_key):
        raise Http404

    allowed = _session_creation_allowed(site, request)
    if allowed is None:
        return _cors(JsonResponse({"error": "unavailable"}, status=503), origin, "POST", "Content-Type")
    if not allowed:
        response = JsonResponse({"error": "rate_limited"}, status=429)
        response["Retry-After"] = "60"
        return _cors(response, origin, "POST", "Content-Type")

    token = "ws_" + secrets.token_urlsafe(32)
    expires_at = timezone.now() + timedelta(seconds=settings.WIDGET_SESSION_TTL_SECONDS)
    with transaction.atomic():
        conversation = Conversation.objects.create()
        WidgetSession.objects.create(
            conversation=conversation,
            widget_site_id=site.pk,
            token_digest=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            expires_at=expires_at,
        )
    return _cors(
        JsonResponse(
            {
                "conversation_id": str(conversation.pk),
                "access_token": token,
                "token_type": "Bearer",
                "expires_at": expires_at.isoformat(),
            },
            status=201,
        ),
        origin,
        "POST",
        "Content-Type",
    )


@csrf_exempt
@require_http_methods(["GET", "DELETE", "OPTIONS"])
def current_widget_session(request):
    if request.tenant.schema_name == get_public_schema_name():
        raise Http404
    origin = _request_origin(request)
    is_frontend_origin = bool(settings.WIDGET_FRONTEND_ORIGIN) and origin == settings.WIDGET_FRONTEND_ORIGIN
    if is_frontend_origin:
        if not WidgetSite.objects.filter(tenant=request.tenant, is_active=True).exists():
            raise Http404
    else:
        _site_for_request(request)
    if request.method == "OPTIONS":
        return _cors(HttpResponse(status=204), origin, "GET, DELETE", "Authorization")

    parts = request.headers.get("Authorization", "").split()
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].startswith("ws_") or len(parts[1]) > 128:
        response = JsonResponse({"error": "unauthorized"}, status=401)
        response["WWW-Authenticate"] = "Bearer"
        return _cors(response, origin, "GET, DELETE", "Authorization")
    digest = hashlib.sha256(parts[1].encode("utf-8")).hexdigest()
    session = WidgetSession.objects.select_related("conversation").filter(
        token_digest=digest, expires_at__gt=timezone.now()
    ).first()
    site = (
        WidgetSite.objects.filter(
            pk=session.widget_site_id, tenant=request.tenant, is_active=True
        ).first()
        if session is not None
        else None
    )
    if site is None or origin not in (site.allowed_origin, settings.WIDGET_FRONTEND_ORIGIN):
        response = JsonResponse({"error": "unauthorized"}, status=401)
        response["WWW-Authenticate"] = "Bearer"
        return _cors(response, origin, "GET, DELETE", "Authorization")
    if request.method == "DELETE":
        session.delete()
        return _cors(HttpResponse(status=204), origin, "GET, DELETE", "Authorization")
    return _cors(
        JsonResponse(
            {
                "conversation_id": str(session.conversation_id),
                "expires_at": session.expires_at.isoformat(),
            }
        ),
        origin,
        "GET, DELETE",
        "Authorization",
    )

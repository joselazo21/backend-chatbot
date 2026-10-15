from functools import wraps

from django.contrib.auth.hashers import check_password
from django.http import JsonResponse
from django_tenants.utils import get_public_schema_name


def require_service_token(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        parts = request.headers.get("Authorization", "").split()
        token_hash = request.tenant.service_token_hash
        authorized = (
            request.tenant.schema_name != get_public_schema_name()
            and bool(token_hash)
            and len(parts) == 2
            and parts[0].lower() == "bearer"
            and len(parts[1]) <= 128
            and check_password(parts[1], token_hash)
        )
        if not authorized:
            response = JsonResponse({"error": "unauthorized"}, status=401)
            response["WWW-Authenticate"] = "Bearer"
            return response
        return view(request, *args, **kwargs)

    return wrapped

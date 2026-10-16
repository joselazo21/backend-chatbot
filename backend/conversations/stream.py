"""Authenticated SSE stream backed by durable AssistantRun progress."""

import asyncio
import json
from time import monotonic

from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django_tenants.utils import tenant_context

from customers.models import WidgetSite

from .models import AssistantRun, Message, WidgetSession
from .widget_views import _allowed_session_origin, _authenticated_session, _cors, _unauthorized


def _event(name, payload):
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _snapshot(tenant, session_id, run_id, origin):
    with tenant_context(tenant):
        session = WidgetSession.objects.filter(pk=session_id, expires_at__gt=timezone.now()).first()
        if session is None:
            return None
        site = WidgetSite.objects.filter(pk=session.widget_site_id, tenant=tenant, is_active=True).first()
        if site is None or origin not in (site.allowed_origin, settings.WIDGET_FRONTEND_ORIGIN):
            return None
        run = AssistantRun.objects.filter(
            pk=run_id, user_message__conversation_id=session.conversation_id
        ).values("status", "attempts", "streamed_content", "assistant_message_id", "error_code", "retrieved_sources").first()
        return run


async def _events(tenant, session_id, run_id, origin):
    yield "retry: 1000\n\n"
    attempt = None
    offset = 0
    last_ping = monotonic()
    deadline = monotonic() + 120
    while monotonic() < deadline:
        state = await sync_to_async(_snapshot, thread_sensitive=True)(tenant, session_id, run_id, origin)
        if state is None:
            yield _event("error", {"code": "session_inactive"})
            return
        if state["attempts"] != attempt:
            attempt = state["attempts"]
            offset = 0
            yield _event("reset", {"attempt": attempt})
        content = state["streamed_content"]
        if len(content) < offset:
            offset = 0
            yield _event("reset", {"attempt": attempt})
        if len(content) > offset:
            yield _event("delta", {"text": content[offset:]})
            offset = len(content)
        if state["status"] == AssistantRun.Status.COMPLETED:
            yield _event("complete", {"message_id": state["assistant_message_id"], "sources": state["retrieved_sources"]})
            return
        if state["status"] == AssistantRun.Status.FAILED:
            yield _event("error", {"code": state["error_code"] or "generation_failed"})
            return
        if monotonic() - last_ping >= 15:
            yield ": ping\n\n"
            last_ping = monotonic()
        await asyncio.sleep(0.25)
    yield _event("reconnect", {})


@require_http_methods(["GET", "OPTIONS"])
def widget_message_events(request, message_id):
    origin = _allowed_session_origin(request)
    if request.method == "OPTIONS":
        return _cors(HttpResponse(status=204), origin, "GET", "Authorization")
    session = _authenticated_session(request, origin)
    if session is None:
        return _unauthorized(origin, "GET", "Authorization")
    message = Message.objects.filter(
        pk=message_id,
        conversation_id=session.conversation_id,
        role=Message.Role.USER,
    ).first()
    if message is None:
        raise Http404
    run_id = AssistantRun.objects.filter(user_message=message).values_list("pk", flat=True).first()
    if run_id is None:
        return _cors(JsonResponse({"error": "generation_unconfigured"}, status=409), origin, "GET", "Authorization")
    response = StreamingHttpResponse(
        _events(request.tenant, session.pk, run_id, origin), content_type="text/event-stream; charset=utf-8"
    )
    response["X-Accel-Buffering"] = "no"
    return _cors(response, origin, "GET", "Authorization")

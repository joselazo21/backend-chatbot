"""Public Telegram webhook and authenticated Baileys bridge API."""

import json

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django_tenants.utils import get_public_schema_name, schema_context

from customers.models import ChannelAccount

from .channels import accept_inbound, acknowledge_baileys_reply, lease_baileys_reply, secret_matches


def _account(request, account_id, provider, secret):
    # The account is found by id and secret in the public schema, whatever domain received the request.
    with schema_context(get_public_schema_name()):
        account = ChannelAccount.objects.select_related("tenant").filter(
            pk=account_id, provider=provider, is_active=True
        ).first()
    if account is None or not secret_matches(secret, account.secret_digest):
        return None
    return account


def _json(request):
    if request.content_type != "application/json" or len(request.body) > 65536:
        raise ValueError("invalid_request")
    value = json.loads(request.body)
    if not isinstance(value, dict):
        raise ValueError("invalid_request")
    return value


def _bridge_account(request, account_id):
    parts = request.headers.get("Authorization", "").split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return _account(request, account_id, ChannelAccount.Provider.BAILEYS, parts[1])


@csrf_exempt
@require_http_methods(["POST"])
def telegram_webhook(request, account_id):
    account = _account(
        request, account_id, ChannelAccount.Provider.TELEGRAM,
        request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""),
    )
    if account is None:
        return HttpResponse(status=401)
    try:
        payload = _json(request)
        message = payload.get("message")
        if not isinstance(message, dict):
            return HttpResponse(status=200)
        chat = message.get("chat")
        if not isinstance(chat, dict) or chat.get("type") != "private" or not isinstance(chat.get("id"), int):
            return HttpResponse(status=200)
        if not isinstance(message.get("message_id"), int):
            return HttpResponse(status=200)
        external_id = f'{chat["id"]}:{message["message_id"]}'
        if isinstance(message.get("text"), str):
            accept_inbound(account, str(chat["id"]), external_id, message["text"])
        else:
            # Voice notes and audio files; anything else is ignored. Needs transcription configured.
            voice = message.get("voice") or message.get("audio")
            if (
                settings.WHISPER_URL
                and isinstance(voice, dict)
                and isinstance(voice.get("file_id"), str)
                and voice.get("file_size", 0) <= settings.WHISPER_MAX_AUDIO_BYTES
            ):
                accept_inbound(
                    account, str(chat["id"]), external_id, "", telegram_file_id=voice["file_id"]
                )
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid_request"}, status=400)
    return HttpResponse(status=200)


@csrf_exempt
@require_http_methods(["POST"])
def baileys_inbound(request, account_id):
    account = _bridge_account(request, account_id)
    if account is None:
        return HttpResponse(status=401)
    try:
        payload = _json(request)
        chat_id = payload["chat_id"]
        message_id = payload["message_id"]
        content = payload["content"]
        if not isinstance(chat_id, str) or not chat_id.endswith(("@s.whatsapp.net", "@lid")):
            raise ValueError("invalid_chat")
        message, created = accept_inbound(account, chat_id, message_id, content)
    except (KeyError, ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid_request"}, status=400)
    return JsonResponse({"message_id": message.pk, "created": created}, status=201 if created else 200)


@require_http_methods(["GET"])
def baileys_outbound(request, account_id):
    account = _bridge_account(request, account_id)
    if account is None:
        return HttpResponse(status=401)
    item = lease_baileys_reply(account)
    return JsonResponse({"delivery": item})


@csrf_exempt
@require_http_methods(["POST"])
def baileys_ack(request, account_id, delivery_id):
    account = _bridge_account(request, account_id)
    if account is None:
        return HttpResponse(status=401)
    try:
        payload = _json(request)
        if not isinstance(payload.get("lease_token"), str) or not isinstance(payload.get("provider_message_id"), str):
            raise ValueError("invalid_ack")
        accepted = acknowledge_baileys_reply(
            account, delivery_id, payload["lease_token"], payload["provider_message_id"]
        )
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid_request"}, status=400)
    return HttpResponse(status=204 if accepted else 409)

"""Telegram delivery and recovery of durable outbound messages."""

import json
import logging
import os
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from celery import shared_task
from django.db import transaction
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, schema_context, tenant_context

from customers.models import ChannelAccount, Customer

from .models import OutboundChannelMessage

logger = logging.getLogger(__name__)


def _telegram_token(account):
    return os.environ.get(account.telegram_token_env, "") if account.telegram_token_env else ""


def _send_telegram(token, chat_id, content):
    request = Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps({"chat_id": chat_id, "text": content}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=15) as response:
        payload = json.load(response)
    if not payload.get("ok") or not isinstance(payload.get("result"), dict):
        raise ValueError("invalid_provider_response")
    return str(payload["result"].get("message_id", ""))


@shared_task(name="conversations.channel_tasks.send_telegram_reply")
def send_telegram_reply(tenant_id, delivery_id):
    with schema_context(get_public_schema_name()):
        tenant = Customer.objects.filter(pk=tenant_id).first()
    if tenant is None:
        return
    with tenant_context(tenant):
        with transaction.atomic():
            delivery = (
                OutboundChannelMessage.objects.select_for_update()
                .select_related("assistant_run__assistant_message", "assistant_run__user_message__conversation__channel_binding")
                .filter(pk=delivery_id)
                .first()
            )
            if delivery is None or delivery.status not in ("pending", "leased"):
                return
            now = timezone.now()
            if delivery.status == "leased" and delivery.leased_until and delivery.leased_until > now:
                return
            if delivery.next_attempt_at and delivery.next_attempt_at > now:
                return
            binding = delivery.assistant_run.user_message.conversation.channel_binding
            account_id = binding.channel_account_id
            chat_id = binding.external_chat_id
            content = delivery.assistant_run.assistant_message.content
            sent_offset = delivery.sent_offset
            with schema_context(get_public_schema_name()):
                account = ChannelAccount.objects.filter(pk=account_id, tenant=tenant, provider="telegram", is_active=True).first()
            if account is None:
                delivery.status = "failed"
                delivery.last_error = "account_inactive"
                delivery.save(update_fields=["status", "last_error"])
                return
            token = _telegram_token(account)
            if not token:
                delivery.status = "failed"
                delivery.last_error = "token_missing"
                delivery.save(update_fields=["status", "last_error"])
                return
            if delivery.attempts >= 5:
                delivery.status = "failed"
                delivery.last_error = "attempts_exhausted"
                delivery.save(update_fields=["status", "last_error"])
                return
            delivery.status = "leased"
            delivery.attempts += 1
            delivery.leased_until = now + timedelta(seconds=120)
            delivery.save(update_fields=["status", "attempts", "leased_until"])
        try:
            while sent_offset < len(content):
                chunk = content[sent_offset:sent_offset + 4000]
                provider_id = _send_telegram(token, chat_id, chunk)
                sent_offset += len(chunk)
                with transaction.atomic():
                    delivery = OutboundChannelMessage.objects.select_for_update().get(pk=delivery_id)
                    if delivery.status != "leased":
                        return
                    delivery.sent_offset = sent_offset
                    delivery.provider_message_id = provider_id
                    delivery.leased_until = timezone.now() + timedelta(seconds=120)
                    delivery.save(update_fields=["sent_offset", "provider_message_id", "leased_until"])
        except (HTTPError, URLError, TimeoutError, ValueError, OSError) as error:
            logger.warning("Telegram delivery failed: %s", type(error).__name__)
            with transaction.atomic():
                delivery = OutboundChannelMessage.objects.select_for_update().get(pk=delivery_id)
                if delivery.status == "leased":
                    delivery.status = "failed" if delivery.attempts >= 5 else "pending"
                    delivery.last_error = "provider_error"
                    delivery.leased_until = None
                    delivery.next_attempt_at = timezone.now() + timedelta(seconds=10 * 3 ** (delivery.attempts - 1))
                    delivery.save(update_fields=["status", "last_error", "leased_until", "next_attempt_at"])
            return
        with transaction.atomic():
            delivery = OutboundChannelMessage.objects.select_for_update().get(pk=delivery_id)
            if delivery.status == "leased":
                delivery.status = "sent"
                delivery.leased_until = None
                delivery.save(update_fields=["status", "leased_until"])


@shared_task(name="conversations.channel_tasks.dispatch_pending_channel_replies")
def dispatch_pending_channel_replies():
    with schema_context(get_public_schema_name()):
        accounts = list(ChannelAccount.objects.filter(provider="telegram", is_active=True).select_related("tenant"))
    now = timezone.now()
    for account in accounts:
        with tenant_context(account.tenant):
            ids = list(
                OutboundChannelMessage.objects.filter(
                    assistant_run__user_message__conversation__channel_binding__channel_account_id=account.pk,
                    status__in=["pending", "leased"],
                )
                .order_by("created_at")
                .values_list("pk", "status", "next_attempt_at", "leased_until")[:100]
            )
        for delivery_id, status, due, lease in ids:
            if (due is None or due <= now) and (status == "pending" or lease is None or lease <= now):
                try:
                    send_telegram_reply.delay(account.tenant_id, delivery_id)
                except Exception:
                    logger.exception("Could not enqueue Telegram reply")

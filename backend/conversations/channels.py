"""Channel-neutral ingestion and durable outbound delivery."""

import hashlib
import hmac
import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django_tenants.utils import tenant_context

from .models import (
    AssistantRun,
    AudioAttachment,
    ChannelConversation,
    Conversation,
    InboundChannelMessage,
    Message,
    OutboundChannelMessage,
)
from .tasks import dispatch_assistant_run

logger = logging.getLogger(__name__)


def secret_matches(raw_secret, digest):
    if not raw_secret or len(raw_secret) > 256 or not digest:
        return False
    return hmac.compare_digest(hashlib.sha256(raw_secret.encode()).hexdigest(), digest)


def accept_inbound(account, external_chat_id, external_message_id, content, *, telegram_file_id=""):
    """Return (message, created); a repeated provider event cannot create a second run."""
    if not all(isinstance(value, str) for value in (external_chat_id, external_message_id, content)):
        raise ValueError("invalid_message")
    content = content.strip()
    # A voice note has no text yet; the worker fills it in after transcription.
    if telegram_file_id and (not isinstance(telegram_file_id, str) or len(telegram_file_id) > 255 or content):
        raise ValueError("invalid_message")
    if (
        not external_chat_id or len(external_chat_id) > 255
        or not external_message_id or len(external_message_id) > 255
        or (not content and not telegram_file_id) or len(content) > 4000 or "\x00" in content
    ):
        raise ValueError("invalid_message")
    try:
        content.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("invalid_message") from error
    with tenant_context(account.tenant):
        for attempt in range(3):
            try:
                return _accept_once(account, external_chat_id, external_message_id, content, telegram_file_id)
            except IntegrityError:
                # Concurrent deliveries can race when creating the chat or event.
                if attempt == 2:
                    raise


def _accept_once(account, external_chat_id, external_message_id, content, telegram_file_id=""):
    with transaction.atomic():
        existing = InboundChannelMessage.objects.filter(
            channel_account_id=account.pk, external_message_id=external_message_id
        ).select_related("message").first()
        if existing:
            return existing.message, False
        binding = ChannelConversation.objects.filter(
            channel_account_id=account.pk, external_chat_id=external_chat_id
        ).first()
        if binding is None:
            conversation = Conversation.objects.create()
            binding = ChannelConversation.objects.create(
                conversation=conversation,
                channel_account_id=account.pk,
                external_chat_id=external_chat_id,
            )
        message = Message.objects.create(
            conversation=binding.conversation, role=Message.Role.USER, content=content
        )
        InboundChannelMessage.objects.create(
            channel_account_id=account.pk,
            external_message_id=external_message_id,
            message=message,
        )
        if telegram_file_id:
            AudioAttachment.objects.create(message=message, telegram_file_id=telegram_file_id)
        if settings.LLM_CHAT_URL:
            run = AssistantRun.objects.create(user_message=message)
            transaction.on_commit(lambda: dispatch_assistant_run(account.tenant_id, run.pk))
        return message, True


def queue_channel_reply(run):
    """Called in the tenant schema after the assistant message is committed."""
    binding = ChannelConversation.objects.filter(conversation_id=run.user_message.conversation_id).first()
    if binding:
        OutboundChannelMessage.objects.get_or_create(assistant_run=run)


def lease_baileys_reply(account):
    """Lease one reply so a restarted bridge can resume delivery."""
    now = timezone.now()
    with tenant_context(account.tenant):
        with transaction.atomic():
            candidate = (
                OutboundChannelMessage.objects.select_for_update(skip_locked=True, of=("self",))
                .filter(assistant_run__user_message__conversation__channel_binding__channel_account_id=account.pk)
                .filter(status__in=[OutboundChannelMessage.Status.PENDING, OutboundChannelMessage.Status.LEASED])
                .order_by("created_at")
                .select_related("assistant_run__assistant_message", "assistant_run__user_message__conversation__channel_binding")
            )
            for delivery in candidate[:20]:
                if delivery.status == OutboundChannelMessage.Status.LEASED and delivery.leased_until and delivery.leased_until > now:
                    continue
                if delivery.next_attempt_at and delivery.next_attempt_at > now:
                    continue
                if delivery.attempts >= 5:
                    delivery.status = OutboundChannelMessage.Status.FAILED
                    delivery.save(update_fields=["status"])
                    continue
                delivery.status = OutboundChannelMessage.Status.LEASED
                delivery.attempts += 1
                delivery.lease_token = uuid.uuid4()
                delivery.leased_until = now + timedelta(seconds=60)
                delivery.save(update_fields=["status", "attempts", "lease_token", "leased_until"])
                binding = delivery.assistant_run.user_message.conversation.channel_binding
                return {
                    "id": delivery.pk,
                    "lease_token": str(delivery.lease_token),
                    "chat_id": binding.external_chat_id,
                    "content": delivery.assistant_run.assistant_message.content,
                }
    return None


def acknowledge_baileys_reply(account, delivery_id, lease_token, provider_message_id):
    try:
        parsed_token = uuid.UUID(lease_token)
    except (ValueError, TypeError):
        return False
    with tenant_context(account.tenant):
        with transaction.atomic():
            delivery = (
                OutboundChannelMessage.objects.select_for_update(of=("self",))
                .filter(pk=delivery_id, assistant_run__user_message__conversation__channel_binding__channel_account_id=account.pk)
                .first()
            )
            if delivery is None or delivery.status != OutboundChannelMessage.Status.LEASED or delivery.lease_token != parsed_token:
                return False
            delivery.status = OutboundChannelMessage.Status.SENT
            delivery.provider_message_id = provider_message_id[:255]
            delivery.leased_until = None
            delivery.lease_token = None
            delivery.save(update_fields=["status", "provider_message_id", "leased_until", "lease_token"])
            return True

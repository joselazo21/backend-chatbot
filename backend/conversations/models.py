import uuid

from django.db import models


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return str(self.id)


class WidgetSession(models.Model):
    conversation = models.OneToOneField(
        Conversation, on_delete=models.CASCADE, related_name="widget_session"
    )
    widget_site_id = models.PositiveBigIntegerField(db_index=True)
    token_digest = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)


class Message(models.Model):
    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=16, choices=Role.choices)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["conversation", "id"])]


class AudioAttachment(models.Model):
    """Audio waiting to be transcribed; it is deleted as soon as the transcript is stored."""

    message = models.OneToOneField(Message, on_delete=models.CASCADE, related_name="audio")
    # Web and worker do not share a disk, so widget audio travels through the database.
    data = models.BinaryField(null=True, blank=True)
    content_type = models.CharField(max_length=64, blank=True)
    # Telegram voice notes are fetched by the worker from this provider file id instead.
    telegram_file_id = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class AssistantRun(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    user_message = models.OneToOneField(
        Message, on_delete=models.CASCADE, related_name="assistant_run"
    )
    assistant_message = models.OneToOneField(
        Message,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generated_by_run",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=40, blank=True)
    streamed_content = models.TextField(blank=True)
    retrieved_sources = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["status", "next_attempt_at"])]


class ChannelConversation(models.Model):
    conversation = models.OneToOneField(
        Conversation, on_delete=models.CASCADE, related_name="channel_binding"
    )
    channel_account_id = models.UUIDField()
    external_chat_id = models.CharField(max_length=255)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["channel_account_id", "external_chat_id"],
                name="unique_channel_external_chat",
            )
        ]


class InboundChannelMessage(models.Model):
    channel_account_id = models.UUIDField()
    external_message_id = models.CharField(max_length=255)
    message = models.OneToOneField(Message, on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["channel_account_id", "external_message_id"],
                name="unique_channel_inbound_message",
            )
        ]


class OutboundChannelMessage(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        LEASED = "leased", "Leased"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    assistant_run = models.OneToOneField(AssistantRun, on_delete=models.CASCADE)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    leased_until = models.DateTimeField(null=True, blank=True)
    lease_token = models.UUIDField(null=True, blank=True)
    provider_message_id = models.CharField(max_length=255, blank=True)
    sent_offset = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["status", "next_attempt_at"])]

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

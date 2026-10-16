import uuid
from pathlib import Path

from django.db import connection, models
from pgvector.django import VectorField


def document_path(instance, filename):
    """Retained for the initial migration; new uploads live in PostgreSQL."""
    suffix = Path(filename).suffix.lower()
    return f"knowledge/{connection.schema_name}/{instance.pk}{suffix}"


class Document(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        READY = "ready", "Ready"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    filename = models.CharField(max_length=255)
    source = models.BinaryField(null=True, blank=True)
    size_bytes = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=40, blank=True)
    embedding_model = models.CharField(max_length=255, blank=True)
    embedding_dimensions = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["status", "next_attempt_at"])]


class Chunk(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="chunks")
    ordinal = models.PositiveIntegerField()
    page = models.PositiveSmallIntegerField(null=True, blank=True)
    content = models.TextField()
    embedding = VectorField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["document", "ordinal"], name="knowledge_chunk_order")]

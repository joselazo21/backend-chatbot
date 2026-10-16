"""Tenant-scoped, retryable document ingestion."""

import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django_tenants.utils import get_public_schema_name, schema_context, tenant_context

from customers.models import Customer

from .embeddings import EmbeddingError, embed_texts
from .models import Chunk, Document
from .processing import DocumentError, extract_pages, split_pages

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3


def dispatch_document(tenant_id, document_id):
    try:
        process_document.delay(tenant_id, str(document_id))
        return True
    except Exception:
        logger.exception("Could not enqueue document", extra={"tenant_id": tenant_id, "document_id": str(document_id)})
        return False


def _fail(document_id, code, retryable):
    with transaction.atomic():
        document = Document.objects.defer("source").select_for_update().filter(pk=document_id).first()
        if document is None or document.status != Document.Status.PROCESSING:
            return
        document.status = Document.Status.QUEUED if retryable and document.attempts < MAX_ATTEMPTS else Document.Status.FAILED
        if document.status == Document.Status.FAILED:
            document.source = None
        document.error_code = code
        document.started_at = None
        document.next_attempt_at = (
            timezone.now() + timedelta(seconds=10 * 3 ** (document.attempts - 1))
            if document.status == Document.Status.QUEUED else None
        )
        document.save(update_fields=["status", "source", "error_code", "started_at", "next_attempt_at", "updated_at"])


@shared_task(name="knowledge.tasks.process_document")
def process_document(tenant_id, document_id):
    if not settings.EMBEDDING_URL:
        return
    with schema_context(get_public_schema_name()):
        tenant = Customer.objects.filter(pk=tenant_id).first()
    if tenant is None or tenant.schema_name == get_public_schema_name():
        return
    with tenant_context(tenant):
        with transaction.atomic():
            document = Document.objects.select_for_update().filter(pk=document_id).first()
            if document is None or document.status != Document.Status.QUEUED:
                return
            if document.next_attempt_at and document.next_attempt_at > timezone.now():
                return
            document.status = Document.Status.PROCESSING
            document.attempts += 1
            document.started_at = timezone.now()
            document.save(update_fields=["status", "attempts", "started_at", "updated_at"])
        try:
            raw = bytes(document.source or b"")
            pieces = split_pages(extract_pages(document.filename, raw))
            rows = []
            dimensions = None
            for start in range(0, len(pieces), 16):
                batch = pieces[start:start + 16]
                vectors = embed_texts([content for _, content in batch])
                Document.objects.filter(pk=document_id, status=Document.Status.PROCESSING).update(started_at=timezone.now())
                for (page, content), vector in zip(batch, vectors, strict=True):
                    if dimensions is None:
                        dimensions = len(vector)
                    elif dimensions != len(vector):
                        raise EmbeddingError("embedding_dimensions_changed", retryable=False)
                    rows.append(Chunk(document_id=document_id, ordinal=len(rows), page=page, content=content, embedding=vector))
        except DocumentError as error:
            _fail(document_id, error.code, False)
            return
        except EmbeddingError as error:
            _fail(document_id, error.code, error.retryable)
            return
        except Exception:
            logger.exception("Unexpected document processing error", extra={"tenant_id": tenant_id, "document_id": document_id})
            _fail(document_id, "internal_error", True)
            return
        with transaction.atomic():
            document = Document.objects.defer("source").select_for_update().filter(pk=document_id).first()
            if document is None or document.status != Document.Status.PROCESSING:
                return
            document.chunks.all().delete()
            Chunk.objects.bulk_create(rows, batch_size=100)
            document.status = Document.Status.READY
            document.embedding_model = settings.EMBEDDING_MODEL
            document.embedding_dimensions = dimensions
            document.source = None
            document.error_code = ""
            document.started_at = None
            document.next_attempt_at = None
            document.save(update_fields=["status", "embedding_model", "embedding_dimensions", "source", "error_code", "started_at", "next_attempt_at", "updated_at"])


@shared_task(name="knowledge.tasks.dispatch_pending_documents")
def dispatch_pending_documents():
    if not settings.EMBEDDING_URL:
        return 0
    with schema_context(get_public_schema_name()):
        tenants = list(Customer.objects.exclude(schema_name=get_public_schema_name()))
    now = timezone.now()
    dispatched = 0
    for tenant in tenants:
        with tenant_context(tenant):
            with transaction.atomic():
                stale = Document.objects.defer("source").select_for_update().filter(
                    status=Document.Status.PROCESSING,
                    started_at__lt=now - timedelta(minutes=10),
                )
                for document in stale[:100]:
                    document.status = (
                        Document.Status.FAILED if document.attempts >= MAX_ATTEMPTS
                        else Document.Status.QUEUED
                    )
                    if document.status == Document.Status.FAILED:
                        document.source = None
                    document.started_at = None
                    document.next_attempt_at = None
                    document.error_code = "worker_timeout"
                    document.save(update_fields=["status", "source", "started_at", "next_attempt_at", "error_code", "updated_at"])
            due = Document.objects.filter(status=Document.Status.QUEUED).filter(
                Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now)
            ).order_by("created_at").values_list("pk", flat=True)[:100]
            for document_id in due:
                if dispatch_document(tenant.pk, document_id):
                    dispatched += 1
    return dispatched

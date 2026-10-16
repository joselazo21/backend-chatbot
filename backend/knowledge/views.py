from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from customers.auth import require_service_token

from .models import Document
from .processing import ALLOWED_SUFFIXES, MAX_DOCUMENT_BYTES
from .tasks import dispatch_document


def _serialize(document):
    return {
        "id": str(document.pk),
        "filename": document.filename,
        "size_bytes": document.size_bytes,
        "status": document.status,
        "error_code": document.error_code,
        "chunk_count": document.chunks.count() if document.status == Document.Status.READY else 0,
        "created_at": document.created_at.isoformat(),
    }


@csrf_exempt
@require_service_token
@require_http_methods(["GET", "POST"])
def documents(request):
    if request.method == "GET":
        items = Document.objects.order_by("-created_at")[:100]
        return JsonResponse({"documents": [_serialize(item) for item in items]})
    if not settings.EMBEDDING_URL:
        return JsonResponse({"error": "embeddings_unconfigured"}, status=503)
    if request.content_type != "multipart/form-data":
        return JsonResponse({"error": "multipart_required"}, status=415)
    upload = request.FILES.get("file")
    if upload is None:
        return JsonResponse({"error": "file_required"}, status=400)
    filename = upload.name.replace("\\", "/").split("/")[-1]
    if (
        not filename
        or len(filename) > 255
        or Path(filename).suffix.lower() not in ALLOWED_SUFFIXES
        or upload.size < 1
        or upload.size > MAX_DOCUMENT_BYTES
    ):
        return JsonResponse({"error": "invalid_document"}, status=400)
    with transaction.atomic():
        document = Document.objects.create(
            filename=filename, file=upload, size_bytes=upload.size
        )
        tenant_id = request.tenant.pk
        transaction.on_commit(lambda: dispatch_document(tenant_id, document.pk))
    response = JsonResponse(_serialize(document), status=202)
    response["Location"] = reverse("document_detail", args=[document.pk])
    return response


@csrf_exempt
@require_service_token
@require_http_methods(["GET", "DELETE"])
def document_detail(request, document_id):
    document = Document.objects.filter(pk=document_id).first()
    if document is None:
        raise Http404
    if request.method == "GET":
        return JsonResponse(_serialize(document))
    with transaction.atomic():
        storage = document.file.storage
        path = document.file.name
        document.delete()
        transaction.on_commit(lambda: storage.delete(path))
    return HttpResponse(status=204)

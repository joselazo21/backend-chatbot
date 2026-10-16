from django.conf import settings
from pgvector.django import CosineDistance

from .embeddings import embed_texts
from .models import Chunk, Document


def retrieve_context(question):
    """Return top tenant-local chunks and stable source identifiers."""
    if not settings.EMBEDDING_URL or not Document.objects.filter(
        status=Document.Status.READY, embedding_model=settings.EMBEDDING_MODEL
    ).exists():
        return [], []
    vector = embed_texts([question])[0]
    matches = list(
        Chunk.objects.filter(
            document__status=Document.Status.READY,
            document__embedding_model=settings.EMBEDDING_MODEL,
            document__embedding_dimensions=len(vector),
        )
        .select_related("document")
        .defer("document__source")
        .annotate(distance=CosineDistance("embedding", vector))
        .order_by("distance", "pk")[:4]
    )
    sources = [
        {"document_id": str(chunk.document_id), "filename": chunk.document.filename, "page": chunk.page, "chunk": chunk.ordinal}
        for chunk in matches
    ]
    if not matches:
        return [], []
    lines = [
        f"[{index}] {source['filename']}"
        + (f" (página {source['page']})" if source["page"] else "")
        + f": {chunk.content}"
        for index, (chunk, source) in enumerate(zip(matches, sources, strict=True), start=1)
    ]
    return lines, sources

import tempfile
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.files.uploadedfile import SimpleUploadedFile
from django_tenants.test.cases import TenantTestCase
from django_tenants.test.client import TenantClient
from django_tenants.utils import get_public_schema_name, schema_context, tenant_context

from customers.models import Customer

from .models import Chunk, Document
from .retrieval import retrieve_context
from .tasks import process_document


class KnowledgeTests(TenantTestCase):
    @classmethod
    def setup_tenant(cls, tenant):
        tenant.name = "Knowledge test"
        tenant.service_token_hash = make_password("service-secret")

    def test_upload_process_retrieve_and_delete_are_tenant_scoped(self):
        client = TenantClient(self.tenant)
        auth = {"HTTP_AUTHORIZATION": "Bearer service-secret"}
        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root), patch.object(
            settings, "EMBEDDING_URL", "https://embed.example.com/v1/embeddings"
        ), patch.object(settings, "EMBEDDING_MODEL", "test-model"), patch(
            "knowledge.views.dispatch_document"
        ):
            response = client.post(
                "/api/v1/documents/",
                {"file": SimpleUploadedFile("manual.md", b"Horario: lunes a viernes de 9 a 5.")},
                **auth,
            )
            self.assertEqual(response.status_code, 202)
            document_id = response.json()["id"]
            self.assertEqual(response.json()["status"], "queued")
            self.assertEqual(client.get("/api/v1/documents/").status_code, 401)

            with patch("knowledge.tasks.embed_texts", return_value=[[1.0, 0.0]]):
                process_document(self.tenant.pk, document_id)
                process_document(self.tenant.pk, document_id)
            with tenant_context(self.tenant):
                document = Document.objects.get(pk=document_id)
                self.assertEqual(document.status, Document.Status.READY)
                self.assertEqual(document.embedding_dimensions, 2)
                self.assertEqual(Chunk.objects.filter(document=document).count(), 1)
                with patch("knowledge.retrieval.embed_texts", return_value=[[1.0, 0.0]]):
                    context, sources = retrieve_context("¿Cuál es el horario?")
                self.assertIn("Horario", context[0])
                self.assertEqual(sources[0]["document_id"], document_id)

            with schema_context(get_public_schema_name()):
                other = Customer.objects.create(schema_name="rag_other", name="Other")
            try:
                with tenant_context(other):
                    self.assertFalse(Document.objects.exists())
                    self.assertFalse(Chunk.objects.exists())
            finally:
                with schema_context(get_public_schema_name()):
                    other.delete(force_drop=True)

            detail = client.get(f"/api/v1/documents/{document_id}/", **auth)
            self.assertEqual(detail.json()["chunk_count"], 1)
            self.assertEqual(client.delete(f"/api/v1/documents/{document_id}/", **auth).status_code, 204)
            with tenant_context(self.tenant):
                self.assertFalse(Document.objects.filter(pk=document_id).exists())

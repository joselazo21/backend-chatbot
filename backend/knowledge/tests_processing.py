import io
import json
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase

from .embeddings import EmbeddingError, embed_texts
from .processing import DocumentError, extract_pages, split_pages


class ProcessingTests(SimpleTestCase):
    def test_text_is_split_with_overlap(self):
        pages = extract_pages("manual.md", ("palabra " * 400).encode())
        chunks = split_pages(pages)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(page is None and len(content) <= 1200 for page, content in chunks))
        self.assertIn(chunks[0][-1].split()[-1], chunks[1][1])

    def test_rejects_invalid_or_empty_document(self):
        for filename, raw, code in (
            ("image.png", b"x", "invalid_document"),
            ("scan.pdf", b"not a pdf", "invalid_document"),
            ("empty.txt", b"  ", "no_extractable_text"),
            ("bad.txt", b"\xff", "invalid_encoding"),
        ):
            with self.subTest(filename=filename), self.assertRaises(DocumentError) as caught:
                extract_pages(filename, raw)
            self.assertEqual(caught.exception.code, code)

    def test_embeddings_are_ordered_and_validated(self):
        response = {"data": [
            {"index": 1, "embedding": [0.0, 1.0]},
            {"index": 0, "embedding": [1.0, 0.0]},
        ]}
        with patch.object(settings, "EMBEDDING_URL", "https://embed.example.com/v1/embeddings"), patch.object(
            settings, "EMBEDDING_MODEL", "model"
        ), patch("knowledge.embeddings.urlopen", return_value=io.BytesIO(json.dumps(response).encode())):
            self.assertEqual(embed_texts(["uno", "dos"]), [[1.0, 0.0], [0.0, 1.0]])

    def test_rejects_zero_vector(self):
        response = {"data": [{"index": 0, "embedding": [0.0, 0.0]}]}
        with patch.object(settings, "EMBEDDING_URL", "https://embed.example.com/v1/embeddings"), patch.object(
            settings, "EMBEDDING_MODEL", "model"
        ), patch("knowledge.embeddings.urlopen", return_value=io.BytesIO(json.dumps(response).encode())):
            with self.assertRaises(EmbeddingError):
                embed_texts(["uno"])

    def test_accepts_dimensions_above_ann_index_limit_for_exact_search(self):
        response = {"data": [{"index": 0, "embedding": [1.0] * 3072}]}
        with patch.object(settings, "EMBEDDING_URL", "https://embed.example.com/v1/embeddings"), patch.object(
            settings, "EMBEDDING_MODEL", "model"
        ), patch("knowledge.embeddings.urlopen", return_value=io.BytesIO(json.dumps(response).encode())):
            self.assertEqual(len(embed_texts(["uno"])[0]), 3072)

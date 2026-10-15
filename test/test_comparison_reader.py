"""Reader endpoints must render registered PDFs without exposing file paths."""
import re
import tempfile
import unittest
from pathlib import Path

import pymupdf
from fastapi.testclient import TestClient

from pdf2zh.comparison import app, latest_pair, reader_html


class ComparisonReaderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.source = self.root / "paper.pdf"
        self.target = self.root / "paper-mono.pdf"
        for path, count in ((self.source, 2), (self.target, 1)):
            with pymupdf.open() as doc:
                for number in range(count):
                    doc.new_page().insert_text((72, 72), f"Page {number + 1}")
                doc.save(path)
        self.client = TestClient(app)

    def tearDown(self):
        self.directory.cleanup()

    def token(self, target=None):
        html = reader_html(self.source, target)
        return re.search(r"view/([a-f0-9]+)", html)[1]

    def test_paired_document_metadata_and_page_rendering(self):
        token = self.token(self.target)
        data = self.client.get(f"/document/{token}").json()
        self.assertEqual(len(data["pages"]["original"]), 2)
        self.assertEqual(len(data["pages"]["translated"]), 1)
        self.assertNotIn(str(self.root), str(data))
        rendered = self.client.get(f"/page/{token}/translated/1.png")
        self.assertEqual(rendered.status_code, 200)
        self.assertTrue(rendered.content.startswith(b"\x89PNG"))
        text = self.client.get(f"/text/{token}/original/1").json()
        self.assertEqual(text["spans"][0]["text"], "Page 1")
        download = self.client.get(f"/download/{token}/translated")
        self.assertEqual(download.content, self.target.read_bytes())
        self.assertIn("attachment", download.headers["content-disposition"])
        self.assertEqual(self.client.get(f"/download/{token}/dual").status_code, 404)
        self.assertEqual(self.client.get(f"/download/{token}/original").status_code, 404)

    def test_original_only_and_invalid_requests(self):
        token = self.token()
        self.assertNotIn("translated", self.client.get(f"/document/{token}").json()["pages"])
        for url in (
            f"/page/{token}/translated/1.png",
            f"/page/{token}/original/0.png",
            f"/page/{token}/original/3.png",
            f"/page/{token}/other/1.png",
            "/document/unregistered",
            "/assets/config.json",
            f"/download/{token}/translated",
            f"/download/{token}/other",
        ):
            self.assertEqual(self.client.get(url).status_code, 404, url)

    def test_latest_pair_and_non_pdf_upload(self):
        self.assertEqual(latest_pair(self.root), (self.source, self.target))
        self.assertIn("Word", reader_html(self.root / "paper.docx"))
        self.assertIn("view/empty", reader_html())


if __name__ == "__main__":
    unittest.main()

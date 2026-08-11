from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from scripts.manage_brochures import (
    BrochureError,
    BrochureSource,
    ensure_brochure_pdf,
    extract_pdf_text,
    load_brochure_sources,
    manage_brochures,
    verify_pdf,
)


PDF_BYTES = b"%PDF-1.4\n% offline brochure fixture\n"


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *unused: object) -> None:
        self.close()


def make_source(local_path: str = "private/brochure.pdf") -> dict[str, str]:
    return {
        "id": "example-2026",
        "title": "Example brochure",
        "edition": "2026",
        "local_path": local_path,
        "url": "https://example.test/brochure.pdf",
        "sha256": hashlib.sha256(PDF_BYTES).hexdigest(),
    }


def write_config(repository_root: Path, source: dict[str, str]) -> None:
    config_path = repository_root / "config/elective_rules.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"schema_version": 2, "sources": [source]}),
        encoding="utf-8",
    )


class BrochureRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository_root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_loads_valid_source_registry(self) -> None:
        write_config(self.repository_root, make_source())

        sources = load_brochure_sources(self.repository_root)

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].source_id, "example-2026")
        self.assertEqual(sources[0].local_path, Path("private/brochure.pdf"))

    def test_rejects_source_path_outside_repository(self) -> None:
        write_config(self.repository_root, make_source("../brochure.pdf"))

        with self.assertRaisesRegex(BrochureError, "outside"):
            load_brochure_sources(self.repository_root)


class BrochureFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository_root = Path(self.temporary_directory.name)
        self.source = BrochureSource(
            source_id="example-2026",
            title="Example brochure",
            edition="2026",
            local_path=Path("private/brochure.pdf"),
            url="https://example.test/brochure.pdf",
            sha256=hashlib.sha256(PDF_BYTES).hexdigest(),
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_verifies_pdf_header_and_hash(self) -> None:
        pdf_path = self.repository_root / self.source.local_path
        pdf_path.parent.mkdir(parents=True)
        pdf_path.write_bytes(PDF_BYTES)

        metadata = verify_pdf(pdf_path, self.source.sha256)

        self.assertEqual(metadata["sha256"], self.source.sha256)
        self.assertEqual(metadata["size_bytes"], len(PDF_BYTES))

    def test_downloads_missing_pdf_and_verifies_before_retaining(self) -> None:
        def opener(*unused: object, **unused_keywords: object) -> FakeResponse:
            return FakeResponse(PDF_BYTES)

        status, pdf_path, metadata = ensure_brochure_pdf(
            self.source,
            self.repository_root,
            timeout_seconds=1,
            opener=opener,
        )

        self.assertEqual(status, "downloaded")
        self.assertEqual(pdf_path.read_bytes(), PDF_BYTES)
        self.assertEqual(metadata["sha256"], self.source.sha256)

    def test_failed_forced_download_preserves_existing_file(self) -> None:
        pdf_path = self.repository_root / self.source.local_path
        pdf_path.parent.mkdir(parents=True)
        existing_bytes = b"%PDF-1.4\nexisting content\n"
        pdf_path.write_bytes(existing_bytes)

        def opener(*unused: object, **unused_keywords: object) -> FakeResponse:
            return FakeResponse(b"not a pdf")

        with self.assertRaisesRegex(BrochureError, "not a PDF"):
            ensure_brochure_pdf(
                self.source,
                self.repository_root,
                timeout_seconds=1,
                force=True,
                opener=opener,
            )

        self.assertEqual(pdf_path.read_bytes(), existing_bytes)


class EmbeddedTextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_extracts_text_with_page_markers(self) -> None:
        class Page:
            def __init__(self, text: str | None) -> None:
                self.text = text

            def extract_text(self) -> str | None:
                return self.text

        class Reader:
            pages = [Page("First page"), Page(None), Page("Third page")]

        text_path = self.root / "text/example.txt"
        metadata = extract_pdf_text(
            self.root / "example.pdf",
            text_path,
            reader_factory=lambda unused: Reader(),
        )

        extracted = text_path.read_text(encoding="utf-8")
        self.assertEqual(metadata["page_count"], 3)
        self.assertEqual(metadata["pages_with_text"], 2)
        self.assertIn("===== PAGE 1 =====\nFirst page", extracted)
        self.assertIn("===== PAGE 2 =====\n", extracted)
        self.assertIn("===== PAGE 3 =====\nThird page", extracted)

    def test_rejects_pdf_without_embedded_text(self) -> None:
        class EmptyPage:
            def extract_text(self) -> None:
                return None

        class Reader:
            pages = [EmptyPage()]

        text_path = self.root / "text/example.txt"
        with self.assertRaisesRegex(BrochureError, "no extractable"):
            extract_pdf_text(
                self.root / "example.pdf",
                text_path,
                reader_factory=lambda unused: Reader(),
            )

        self.assertFalse(text_path.exists())


class ManageBrochuresTests(unittest.TestCase):
    def test_verify_only_uses_no_downloader_or_pdf_parser(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = make_source()
            write_config(root, source)
            pdf_path = root / source["local_path"]
            pdf_path.parent.mkdir(parents=True)
            pdf_path.write_bytes(PDF_BYTES)

            def should_not_run(*unused: object, **unused_keywords: object) -> None:
                raise AssertionError("Network or parser should not be called")

            summary = manage_brochures(
                root,
                verify_only=True,
                opener=should_not_run,
                reader_factory=should_not_run,
            )

            self.assertEqual(summary["status"], "ok")
            self.assertEqual(summary["verified_count"], 1)
            self.assertEqual(summary["downloaded_count"], 0)
            self.assertEqual(summary["extracted_count"], 0)


if __name__ == "__main__":
    unittest.main()

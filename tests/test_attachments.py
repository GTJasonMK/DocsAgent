from __future__ import annotations

import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from docsagent.attachments import MAX_DOCUMENT_SECTIONS, MAX_EXTRACTED_CHARS, inspect_attachment


class AttachmentTests(unittest.TestCase):
    def test_docx_extracts_text_with_document_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "note.docx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("word/document.xml", "<w:document xmlns:w='urn'><w:body><w:p><w:r><w:t>Hello</w:t></w:r></w:p></w:body></w:document>")
            result = inspect_attachment(path)
            self.assertEqual(result["kind"], "docx")
            self.assertIn("Hello", result["text"])
            self.assertEqual(result["evidence"][0]["locator"], "document 1")

    def test_xlsx_extracts_sheet_values(self):
        try:
            import openpyxl
        except ImportError:
            self.skipTest("openpyxl is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "table.xlsx"
            workbook = openpyxl.Workbook()
            workbook.active.title = "Summary"
            workbook.active.append(["name", "value"])
            workbook.active.append(["DocsAgent", 1])
            workbook.save(path)
            workbook.close()
            result = inspect_attachment(path)
            self.assertIn("DocsAgent", result["text"])
            self.assertEqual(result["evidence"][0]["locator"], "sheet Summary")

    def test_unsupported_and_oversized_files_fail_explicitly(self):
        with tempfile.TemporaryDirectory() as temporary:
            unsupported = Path(temporary) / "secret.bin"
            unsupported.write_bytes(b"x")
            with self.assertRaisesRegex(ValueError, "unsupported"):
                inspect_attachment(unsupported)

    def test_image_metadata_is_not_reported_as_image_analysis(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "picture.png"
            image.write_bytes(b"not a real image")
            with self.assertRaisesRegex(RuntimeError, "vision-capable model"):
                inspect_attachment(image)

    def test_scanned_pdf_does_not_return_an_empty_success(self):
        try:
            from pypdf import PdfWriter
        except ImportError:
            self.skipTest("pypdf is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scan.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            with path.open("wb") as target:
                writer.write(target)
            with self.assertRaisesRegex(RuntimeError, "no extractable text"):
                inspect_attachment(path)

    def test_pptx_slides_are_extracted_in_numeric_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "deck.pptx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("ppt/slides/slide10.xml", "<p:sld xmlns:p='urn:presentation' xmlns:a='urn:drawing'><a:t>Ten</a:t></p:sld>")
                archive.writestr("ppt/slides/slide2.xml", "<p:sld xmlns:p='urn:presentation' xmlns:a='urn:drawing'><a:t>Two</a:t></p:sld>")
            result = inspect_attachment(path)
            self.assertIn("Two", result["sections"][0]["text"])
            self.assertIn("Ten", result["sections"][1]["text"])

    def test_office_section_separators_obey_the_global_text_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "long-deck.pptx"
            slide = "<p:sld xmlns:p='urn:presentation' xmlns:a='urn:drawing'><a:t>" + "x" * (MAX_EXTRACTED_CHARS // 2) + "</a:t></p:sld>"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("ppt/slides/slide1.xml", slide)
                archive.writestr("ppt/slides/slide2.xml", slide)

            result = inspect_attachment(path)

        self.assertEqual(len(result["text"]), MAX_EXTRACTED_CHARS)
        self.assertTrue(result["truncated"])

    def test_audio_transcript_is_bounded_and_marks_separator_truncation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "recording.mp3"
            path.write_bytes(b"audio")
            segment_text = "x" * (MAX_EXTRACTED_CHARS // MAX_DOCUMENT_SECTIONS)
            segments = [types.SimpleNamespace(text=segment_text, start=index, end=index + 1) for index in range(MAX_DOCUMENT_SECTIONS)]
            fake_model = types.SimpleNamespace(transcribe=lambda _: (iter(segments), None))
            fake_module = types.ModuleType("faster_whisper")
            fake_module.WhisperModel = lambda *args, **kwargs: fake_model
            probe = types.SimpleNamespace(returncode=0, stdout='{"format":{"duration":"30"}}')
            with patch("docsagent.attachments.subprocess.run", return_value=probe):
                with patch.dict(sys.modules, {"faster_whisper": fake_module}):
                    result = inspect_attachment(path)

        self.assertEqual(len(result["text"]), MAX_EXTRACTED_CHARS)
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["evidence"]), MAX_DOCUMENT_SECTIONS)
        self.assertLessEqual(sum(len(item["text"]) for item in result["evidence"]), MAX_EXTRACTED_CHARS)


if __name__ == "__main__":
    unittest.main()

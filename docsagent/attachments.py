from __future__ import annotations

import json
import os
import subprocess
import zipfile
from pathlib import Path
from xml.etree import ElementTree


MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
MAX_EXTRACTED_CHARS = 50000
MAX_DOCUMENT_SECTIONS = 200
MAX_XML_MEMBER_BYTES = 20 * 1024 * 1024
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".docx", ".xlsx", ".pptx", ".wav", ".mp3", ".m4a", ".mp4", ".mov", ".webm"}


def inspect_attachment(path: Path) -> dict:
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"unsupported attachment type: {path.suffix or 'unknown'}")
    size = path.stat().st_size
    if size > MAX_ATTACHMENT_BYTES:
        raise ValueError("attachment exceeds the 50 MB limit")
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _pdf(path, size)
    if suffix == ".docx":
        return _office_xml(path, size, "docx")
    if suffix == ".xlsx":
        return _xlsx(path, size)
    if suffix == ".pptx":
        return _office_xml(path, size, "pptx")
    if suffix in {".wav", ".mp3", ".m4a"}:
        return _media(path, size, "audio")
    if suffix in {".mp4", ".mov", ".webm"}:
        return _media(path, size, "video")
    raise RuntimeError("image analysis requires a vision-capable model; this runtime only extracts text and media metadata")


def _pdf(path: Path, size: int) -> dict:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF support requires the pypdf package") from exc
    reader = PdfReader(str(path))
    pages = []
    truncated = len(reader.pages) > MAX_DOCUMENT_SECTIONS
    total_chars = 0
    for number, page in enumerate(reader.pages[:MAX_DOCUMENT_SECTIONS], 1):
        text = page.extract_text() or ""
        remaining = MAX_EXTRACTED_CHARS - total_chars
        pages.append({"locator": f"page {number}", "text": text[:remaining]})
        total_chars += min(len(text), remaining)
        truncated = truncated or len(text) > remaining
        if total_chars >= MAX_EXTRACTED_CHARS:
            truncated = truncated or number < len(reader.pages)
            break
    extracted = "\n\n".join(item["text"] for item in pages)
    if not extracted.strip():
        raise RuntimeError("PDF contains no extractable text; OCR is not available")
    return {"path": str(path), "kind": "pdf", "bytes": size, "pages": pages, "text": extracted[:MAX_EXTRACTED_CHARS], "truncated": truncated or len(extracted) > MAX_EXTRACTED_CHARS, "evidence": [{"locator": item["locator"]} for item in pages]}


def _office_xml(path: Path, size: int, kind: str) -> dict:
    texts: list[dict] = []
    with zipfile.ZipFile(path) as archive:
        if kind == "docx":
            names = ["word/document.xml"] if "word/document.xml" in archive.namelist() else []
        else:
            names = [name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")]
            names.sort(key=lambda name: int(Path(name).stem.removeprefix("slide")) if Path(name).stem.removeprefix("slide").isdigit() else MAX_DOCUMENT_SECTIONS)
        truncated = len(names) > MAX_DOCUMENT_SECTIONS
        names = names[:MAX_DOCUMENT_SECTIONS]
        total_chars = 0
        for index, name in enumerate(names, 1):
            info = archive.getinfo(name)
            if info.file_size > MAX_XML_MEMBER_BYTES:
                raise ValueError(f"attachment XML section exceeds the {MAX_XML_MEMBER_BYTES} byte limit: {name}")
            root = ElementTree.fromstring(archive.read(name))
            values = [node.text or "" for node in root.iter() if node.tag.endswith("}t") and node.text]
            text = " ".join(" ".join(values).split())
            remaining = MAX_EXTRACTED_CHARS - total_chars
            texts.append({"locator": f"{'document' if kind == 'docx' else 'slide'} {index}", "text": text[:remaining]})
            total_chars += min(len(text), remaining)
            if total_chars >= MAX_EXTRACTED_CHARS:
                truncated = truncated or index < len(names)
                break
    content = "\n\n".join(item["text"] for item in texts)
    truncated = truncated or len(content) > MAX_EXTRACTED_CHARS
    return {"path": str(path), "kind": kind, "bytes": size, "text": content[:MAX_EXTRACTED_CHARS], "sections": texts, "truncated": truncated, "evidence": [{"locator": item["locator"]} for item in texts]}


def _xlsx(path: Path, size: int) -> dict:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise RuntimeError("XLSX support requires the openpyxl package") from exc
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheets = []
    truncated = False
    total_chars = 0
    try:
        worksheets = workbook.worksheets
        truncated = len(worksheets) > MAX_DOCUMENT_SECTIONS
        for worksheet in worksheets[:MAX_DOCUMENT_SECTIONS]:
            rows = []
            sheet_truncated = False
            for row in worksheet.iter_rows(values_only=True):
                values = ["" if value is None else str(value) for value in row]
                if any(values):
                    rows.append("\t".join(values))
                if len(rows) >= 2000:
                    sheet_truncated = True
                    break
            remaining = MAX_EXTRACTED_CHARS - total_chars
            text = "\n".join(rows)
            sheets.append({"locator": f"sheet {worksheet.title}", "text": text[:remaining], "truncated": sheet_truncated or len(text) > remaining})
            total_chars += min(len(text), remaining)
            if total_chars >= MAX_EXTRACTED_CHARS:
                truncated = True
                break
    finally:
        workbook.close()
    content = "\n\n".join(item["text"] for item in sheets)
    return {"path": str(path), "kind": "xlsx", "bytes": size, "text": content[:MAX_EXTRACTED_CHARS], "sheets": sheets, "truncated": truncated or len(content) > MAX_EXTRACTED_CHARS, "evidence": [{"locator": item["locator"]} for item in sheets]}


def _media(path: Path, size: int, kind: str) -> dict:
    try:
        completed = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)], capture_output=True, text=True, timeout=10, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("audio/video inspection requires ffprobe") from exc
    if completed.returncode != 0:
        raise RuntimeError("ffprobe could not read the media file")
    try:
        duration = float(json.loads(completed.stdout)["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("ffprobe returned no usable media duration") from exc
    if duration > 1800:
        raise ValueError("audio/video duration exceeds the 30 minute analysis limit")
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("audio/video analysis requires the optional faster-whisper package") from exc
    model = WhisperModel(os.environ.get("DOCSAGENT_WHISPER_MODEL", "base"), device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(path))
    transcript = []
    evidence = []
    total_chars = 0
    truncated = False
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        separator = " " if transcript else ""
        remaining = MAX_EXTRACTED_CHARS - total_chars - len(separator)
        if remaining <= 0:
            truncated = True
            break
        extracted = text[:remaining]
        transcript.append(separator + extracted)
        total_chars += len(separator) + len(extracted)
        if len(evidence) < MAX_DOCUMENT_SECTIONS:
            evidence.append({"locator": f"{segment.start:.3f}-{segment.end:.3f}s", "text": extracted})
        else:
            truncated = True
        if len(extracted) < len(text):
            truncated = True
            break
    return {"path": str(path), "kind": kind, "analysis_scope": "audio track only" if kind == "video" else "audio transcription", "bytes": size, "duration_seconds": duration, "text": "".join(transcript), "evidence": evidence, "transcribed": True, "truncated": truncated}

import io
import mimetypes
import os
import zipfile
from urllib.parse import unquote, urlparse

import requests

SUPPORTED_IMAGES = {"image/png", "image/jpeg", "image/webp"}
PDF_MIME = "application/pdf"
DOC_MIME = "application/msword"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
OLE_MIME = "application/x-ole-storage"
GENERIC_MIMES = {"application/octet-stream", "binary/octet-stream", "application/zip"}
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


def _is_url(source: str) -> bool:
    return urlparse(str(source)).scheme in ("http", "https")


def _sniff_mime(data: bytes):
    """Detect file type from magic bytes (more reliable than extensions/headers)."""
    if data.startswith(b"%PDF"):
        return PDF_MIME
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"PK\x03\x04"):
        # .docx/.pptx are zip containers; tell them apart by their main part.
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = set(zf.namelist())
        except zipfile.BadZipFile:
            return None
        if "word/document.xml" in names:
            return DOCX_MIME
        if "ppt/presentation.xml" in names:
            return PPTX_MIME
        return None
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        # Legacy .doc/.xls/.ppt all share the OLE2 container.
        return OLE_MIME
    return None


def _load_source(source: str, timeout: int = 30):
    """Return (bytes, mime_type) for a local path or an http(s) URL."""
    if _is_url(source):
        resp = requests.get(source, timeout=timeout, stream=True)
        resp.raise_for_status()

        chunks, size = [], 0
        for chunk in resp.iter_content(chunk_size=8192):
            size += len(chunk)
            if size > MAX_DOWNLOAD_BYTES:
                raise ValueError(f"File too large (> {MAX_DOWNLOAD_BYTES} bytes): {source}")
            chunks.append(chunk)
        data = b"".join(chunks)

        header_mime = resp.headers.get("Content-Type", "").split(";")[0].strip().lower() or None
        guessed_mime = mimetypes.guess_type(urlparse(source).path)[0]
    else:
        if not os.path.exists(source):
            raise ValueError(f"File not found: {source}")
        with open(source, "rb") as f:
            data = f.read()
        header_mime = None
        guessed_mime = mimetypes.guess_type(source)[0]

    # S3 often serves uploads as octet-stream, which says nothing about the file.
    if header_mime in GENERIC_MIMES:
        header_mime = None

    mime_type = _sniff_mime(data)
    if mime_type == OLE_MIME:
        # Magic bytes can't tell .doc from .xls/.ppt; trust the declared type, assume .doc if none.
        mime_type = header_mime or guessed_mime or DOC_MIME
    return data, mime_type or header_mime or guessed_mime


def _output_key(source: str, prefix: str = "") -> str:
    """Build the S3 key for the markdown output from the source's file name."""
    path = unquote(urlparse(source).path) if _is_url(source) else source
    stem = os.path.splitext(os.path.basename(path))[0] or "document"
    return f"{prefix}{stem}.md"

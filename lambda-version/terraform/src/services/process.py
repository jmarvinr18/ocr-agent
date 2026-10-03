import base64
import io
import mimetypes
import os
from urllib.parse import urlparse

import requests

SUPPORTED_IMAGES = {"image/png", "image/jpeg", "image/webp"}
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


def _is_url(source: str) -> bool:
    return urlparse(str(source)).scheme in ("http", "https")


def _sniff_mime(data: bytes):
    """Detect file type from magic bytes (more reliable than extensions/headers)."""
    if data.startswith(b"%PDF"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
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

    mime_type = _sniff_mime(data) or header_mime or guessed_mime
    return data, mime_type
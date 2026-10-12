import base64
import hashlib
import hmac
import json
import os
import re
import uuid

import boto3
import pymupdf
from botocore.config import Config
from botocore.exceptions import ClientError

from services.llm import (
    EMBEDDED_IMAGE_PROMPT,
    IMAGE_FORMATS,
    _invoke_bedrock_document,
    _invoke_bedrock_image,
)
from services.process import (
    DOC_MIME,
    DOCX_MIME,
    MAX_DOWNLOAD_BYTES,
    PDF_MIME,
    PPTX_MIME,
    SUPPORTED_IMAGES,
    _load_source,
    _output_key,
)

# Regional endpoint + SigV4 so presigned URLs work from the browser without redirects.
s3 = boto3.client(
    "s3",
    region_name="ap-southeast-1",
    endpoint_url="https://s3.ap-southeast-1.amazonaws.com",
    config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
)

OUTPUT_BUCKET = os.getenv("OUTPUT_BUCKET")
OUTPUT_PREFIX = os.getenv("OUTPUT_PREFIX", "markdown/")
UPLOAD_PREFIX = os.getenv("UPLOAD_PREFIX", "uploads/")
UPLOAD_API_KEY = os.getenv("UPLOAD_API_KEY", "")
LINK_TTL_SECONDS = int(os.getenv("LINK_TTL_SECONDS", "3600"))

UPLOAD_TYPES = {
    "pdf": PDF_MIME,
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "doc": DOC_MIME,
    "docx": DOCX_MIME,
    "pptx": PPTX_MIME,
}


def _image_describer():
    """Describe images embedded in Office files, once per distinct image."""
    cache = {}

    def describe(blob, mime_type):
        if mime_type not in IMAGE_FORMATS:  # e.g. EMF/WMF/TIFF, which Bedrock can't read
            return ""
        digest = hashlib.sha1(blob).hexdigest()
        if digest not in cache:
            try:
                cache[digest] = _invoke_bedrock_image(
                    base64.b64encode(blob).decode(), mime_type, prompt=EMBEDDED_IMAGE_PROMPT
                )
            except ClientError as e:
                # One unreadable picture (too large, corrupt) shouldn't fail the whole document.
                print(f"SKIPPED EMBEDDED IMAGE ({mime_type}): {e}")
                cache[digest] = ""
        return cache[digest]

    return describe


def _to_markdown(data, mime_type):
    if mime_type in SUPPORTED_IMAGES:
        return _invoke_bedrock_image(base64.b64encode(data).decode(), mime_type)

    if mime_type == PDF_MIME:
        pages = []
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            for i, page in enumerate(doc):
                image_b64 = base64.b64encode(page.get_pixmap(dpi=200).tobytes("png")).decode()
                response = _invoke_bedrock_image(image_b64, "image/png")
                print(f"PAGE {i + 1} RESPONSE: {response}")
                pages.append(f"<!-- page {i + 1} -->\n\n{response}")
        return "\n\n".join(pages)

    if mime_type in (DOCX_MIME, PPTX_MIME):
        # Imported here so a layer without python-docx/python-pptx only breaks
        # Office files, not the whole function.
        from services.office import docx_to_markdown, pptx_to_markdown

        convert = docx_to_markdown if mime_type == DOCX_MIME else pptx_to_markdown
        return convert(data, _image_describer())

    if mime_type == DOC_MIME:
        return _invoke_bedrock_document(data, "doc")

    raise ValueError(f"Unsupported file type: {mime_type}")


def _process(source, output_key=None):
    """Convert one document to Markdown and save it to S3. Returns where it was saved."""
    if not OUTPUT_BUCKET:
        raise ValueError("OUTPUT_BUCKET environment variable is not set")

    data, mime_type = _load_source(source)
    print(f"MIME TYPE: {mime_type}")

    markdown = _to_markdown(data, mime_type).strip() + "\n"

    key = output_key or _output_key(source, OUTPUT_PREFIX)
    s3.put_object(
        Bucket=OUTPUT_BUCKET,
        Key=key,
        Body=markdown.encode("utf-8"),
        ContentType="text/markdown; charset=utf-8",
    )
    print(f"SAVED: s3://{OUTPUT_BUCKET}/{key}")

    return {
        "bucket": OUTPUT_BUCKET,
        "key": key,
        "s3_uri": f"s3://{OUTPUT_BUCKET}/{key}",
        "mime_type": mime_type,
    }


# --- HTTP API (Lambda function URL, used by the upload UI) -----------------


def _http_response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _presigned_get(key, **params):
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": OUTPUT_BUCKET, "Key": key, **params},
        ExpiresIn=LINK_TTL_SECONDS,
    )


def _create_upload(body):
    """Return a presigned POST the browser uses to upload straight to S3."""
    filename = os.path.basename(str(body.get("filename", ""))).strip()
    ext = os.path.splitext(filename)[1].lstrip(".").lower()
    if ext not in UPLOAD_TYPES:
        raise ValueError(f"Unsupported file type: .{ext or '?'}")

    safe_name = re.sub(r"[^\w.\- ()]", "_", filename)
    key = f"{UPLOAD_PREFIX}{uuid.uuid4().hex}/{safe_name}"
    content_type = UPLOAD_TYPES[ext]

    upload = s3.generate_presigned_post(
        Bucket=OUTPUT_BUCKET,
        Key=key,
        Fields={"Content-Type": content_type},
        Conditions=[
            {"Content-Type": content_type},
            ["content-length-range", 1, MAX_DOWNLOAD_BYTES],
        ],
        ExpiresIn=900,
    )
    return {"key": key, "url": upload["url"], "fields": upload["fields"]}


def _process_upload(body):
    key = str(body.get("key", ""))
    if not key.startswith(UPLOAD_PREFIX) or ".." in key:
        raise ValueError("Invalid upload key")

    result = _process(_presigned_get(key))
    return {
        **result,
        "document_key": key,
        "document_url": _presigned_get(key),
        # Served as plain text so the link opens in the browser instead of downloading.
        "markdown_url": _presigned_get(
            result["key"], ResponseContentType="text/plain; charset=utf-8"
        ),
        "expires_in": LINK_TTL_SECONDS,
    }


HTTP_ACTIONS = {"create_upload": _create_upload, "process": _process_upload}


def _handle_http(event):
    if event["requestContext"]["http"]["method"] != "POST":
        return _http_response(405, {"error": "Use POST"})

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if UPLOAD_API_KEY and not hmac.compare_digest(headers.get("x-api-key", ""), UPLOAD_API_KEY):
        return _http_response(401, {"error": "Invalid access key"})

    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")

    try:
        body = json.loads(raw)
        action = HTTP_ACTIONS.get(body.get("action"))
        if action is None:
            return _http_response(400, {"error": f"Unknown action: {body.get('action')}"})
        return _http_response(200, action(body))
    except (ValueError, json.JSONDecodeError) as e:
        return _http_response(400, {"error": str(e)})
    except Exception as e:
        print(f"ERROR: {e!r}")
        return _http_response(500, {"error": "Processing failed. Check the Lambda logs for details."})


def lambda_handler(event, context):

    """
        Parse the document content (local path or URL) into Markdown and save
        it to S3 as a .md file.

        Supported: PDF, PNG, JPEG, WEBP, DOC, DOCX, PPTX.

        Invoked directly with {"source": ..., "output_key"?: ...}, or over the
        function URL by the upload UI (see _handle_http).
    """

    if "http" in event.get("requestContext", {}):
        return _handle_http(event)

    source = event["source"]
    print(f"SOURCE: {source}")

    if not source:
        return None

    return _process(source, event.get("output_key"))

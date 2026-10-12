import base64
import hashlib
import os

import boto3
import pymupdf
from botocore.exceptions import ClientError

from services.llm import (
    EMBEDDED_IMAGE_PROMPT,
    IMAGE_FORMATS,
    _invoke_bedrock_document,
    _invoke_bedrock_image,
)
from services.office import docx_to_markdown, pptx_to_markdown
from services.process import (
    DOC_MIME,
    DOCX_MIME,
    PDF_MIME,
    PPTX_MIME,
    SUPPORTED_IMAGES,
    _load_source,
    _output_key,
)

s3 = boto3.client("s3", region_name="ap-southeast-1")

OUTPUT_BUCKET = os.getenv("OUTPUT_BUCKET")
OUTPUT_PREFIX = os.getenv("OUTPUT_PREFIX", "markdown/")


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

    if mime_type == DOCX_MIME:
        return docx_to_markdown(data, _image_describer())

    if mime_type == PPTX_MIME:
        return pptx_to_markdown(data, _image_describer())

    if mime_type == DOC_MIME:
        return _invoke_bedrock_document(data, "doc")

    raise ValueError(f"Unsupported file type: {mime_type}")


def lambda_handler(event, context):

    """
        Parse the document content (local path or URL) into Markdown and save
        it to S3 as a .md file.

        Supported: PDF, PNG, JPEG, WEBP, DOC, DOCX, PPTX.
    """

    source = event["source"]
    print(f"SOURCE: {source}")

    if not source:
        return None

    if not OUTPUT_BUCKET:
        raise ValueError("OUTPUT_BUCKET environment variable is not set")

    data, mime_type = _load_source(source)
    print(f"MIME TYPE: {mime_type}")

    markdown = _to_markdown(data, mime_type).strip() + "\n"

    key = event.get("output_key") or _output_key(source, OUTPUT_PREFIX)
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

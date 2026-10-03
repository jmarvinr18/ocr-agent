import base64
import io
import boto3
import os
from urllib.parse import urlparse
from services.llm import _invoke_bedrock_image
from services.process import _load_source
import pymupdf
from urllib.parse import urlparse
from pathlib import Path

s3 = boto3.client("s3", region_name="ap-southeast-1")
textract_client = boto3.client("textract", region_name="ap-southeast-1")
comprehend = boto3.client("comprehend", region_name="ap-southeast-1")

SUPPORTED_IMAGES = {"image/png", "image/jpeg", "image/webp"}
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

def lambda_handler(event, context):

    """
        Parse the document content (local path or URL) by analyzing its images
        with Bedrock and extracting text.
    """

    source = event["source"]
    print(f"SOURCEsdfs: {source}")

    if not source:
        return None

    data, mime_type = _load_source(source)
    results = []

    if mime_type in SUPPORTED_IMAGES:
        image_b64 = base64.b64encode(data).decode()
        response = _invoke_bedrock_image(image_b64, mime_type)

        print(f"BEDROCK RESPONSE: {response}")
        results.append(response)

    elif mime_type == "application/pdf":
        with pymupdf.open(stream=data, filetype="pdf") as doc:
            pages = [page.get_pixmap(dpi=200).tobytes("png") for page in doc]

        for i, page in enumerate(pages):
            image_b64 = base64.b64encode(page).decode()
            response = _invoke_bedrock_image(image_b64, "image/png")
            print(f"BEDROCK RESPONSE: {response}")
            print(f"PAGE {i + 1} RESPONSE: {response}")
            results.append(response)

    else:
        raise ValueError(f"Unsupported file type: {mime_type}")

    return results

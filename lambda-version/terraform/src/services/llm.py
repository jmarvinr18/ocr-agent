import base64
import os

import boto3
from botocore.config import Config

MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "apac.amazon.nova-lite-v1:0")
MAX_TOKENS = int(os.getenv("BEDROCK_MAX_TOKENS", "5000"))

# Long pages can take a while to transcribe; the default 60s read timeout is too short.
bedrock = boto3.client(
    "bedrock-runtime",
    region_name="ap-southeast-1",
    config=Config(read_timeout=300, retries={"max_attempts": 5, "mode": "adaptive"}),
)

# Converse expects the bare format name, not the MIME type.
IMAGE_FORMATS = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/webp": "webp",
    "image/gif": "gif",
}

PROMPT = (
    "You are an expert AI assistant, you are tasked with extracting the entire text from a document. The document can be simple, complex, or even scanned, this shouldn't matter to you."
    "Examine the document page by page, when you come across text, extract it as is. If you come across images, replace them with a very detailed description of the image while taking into consideration the context around it."
    "When you come across tables, reproduce them as Markdown tables. If a table is too complex for a Markdown table, describe it in enough detail that someone will understand the table without seeing it."
    "Make sure to keep the structure of the document using Markdown: use # headings for sections and subsections, - for bullet points and 1. for numbered lists. If there are any headers, footers, page numbers, remove them."
    "If there are line breaks mid-sentence, please remove it and display the entire sentence or paragraph in smooth flow."
    "If there's table that is cut to the next page and has no header, please use the header of the table from the previous page."
    "The final output should be clean, well-structured Markdown that represents the content of the document as closely as possible to how a human would see it with their eyes when reading the document. Don't say anything else and don't wrap the output in a code block, just output the Markdown you extracted from the document."
    "Here is the document:"
)

EMBEDDED_IMAGE_PROMPT = (
    "This image is embedded in a document. If it contains text, transcribe the text as is, keeping its structure as Markdown. "
    "Otherwise, give a very detailed description of the image. Don't say anything else and don't wrap the output in a code block."
)


def _converse(content):
    response = bedrock.converse(
        modelId=MODEL_ID,
        messages=[{"role": "user", "content": content}],
        inferenceConfig={"maxTokens": MAX_TOKENS, "temperature": 0},
    )

    blocks = response["output"]["message"]["content"]
    return "".join(block.get("text", "") for block in blocks)


def _invoke_bedrock_image(image_b64, mime_type, prompt=PROMPT):
    """Send one page image to Bedrock with the extraction prompt and return the text."""
    image_format = IMAGE_FORMATS.get(mime_type)
    if image_format is None:
        raise ValueError(f"Unsupported image type for Bedrock: {mime_type}")

    return _converse([
        {"text": prompt},
        {
            "image": {
                "format": image_format,
                "source": {"bytes": base64.b64decode(image_b64)},
            }
        },
    ])


def _invoke_bedrock_document(data, document_format):
    """Send a whole document (e.g. legacy .doc) to Bedrock and return the extracted text."""
    return _converse([
        {"text": PROMPT},
        {
            "document": {
                "format": document_format,
                "name": "document",
                "source": {"bytes": data},
            }
        },
    ])

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
    "You are an expert AI assistant, you are tasked with extracting the entire text from any PDF document. The document can be simple, complex, or even scanned, this shouldn't matter to you."
    "You will be given the entire PDF as input. Start examining the document page by page, when you come across text, extract it as is don't convert it into another format like HTML or Markdown. If you come across images, replace them with a very detailed description of the image while taking into consideration the context around it."
    "When you come across tables, describe them too like the image. The description should be very detailed and in a way that someone will understand the table without seeing it."
    "Make sure to keep the structure of the document, if there are sections, subsections, bullet points, or numbered lists, make sure to keep them as is. If there are any headers, footers, page numbers, remove them."
    "If there are line breaks mid-sentence, please remove it and display the entire sentence or paragraph in smooth flow."
    "If there's table that is cut to the next page and has no header, please use the header of the table from the previous page."
    "The final output should be a clean, well-structured text that represents the content of the entire PDF document as closely as possible to how a human would see it with their eyes when reading the document. Don't say anything else, just output the text you extracted from the PDF."
    "Here is the PDF:"
)


def _invoke_bedrock_image(image_b64, mime_type):
    """Send one page image to Bedrock with the extraction prompt and return the text."""
    image_format = IMAGE_FORMATS.get(mime_type)
    if image_format is None:
        raise ValueError(f"Unsupported image type for Bedrock: {mime_type}")

    response = bedrock.converse(
        modelId=MODEL_ID,
        messages=[
            {
                "role": "user",
                "content": [
                    {"text": PROMPT},
                    {
                        "image": {
                            "format": image_format,
                            "source": {"bytes": base64.b64decode(image_b64)},
                        }
                    },
                ],
            }
        ],
        inferenceConfig={"maxTokens": MAX_TOKENS, "temperature": 0},
    )

    content = response["output"]["message"]["content"]
    return "".join(block.get("text", "") for block in content)

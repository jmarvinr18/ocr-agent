
import base64
import io
import mimetypes
import os
from urllib.parse import urlparse
import requests
from pdf2image import convert_from_bytes

from langchain_core.messages import HumanMessage

# Windows needs the Poppler bin folder; on Linux/Docker poppler-utils is on PATH, so None is fine.
POPPLER_PATH = os.getenv("POPPLER_PATH", r"C:\poppler-25.12.0\Library\bin" if os.name == "nt" else None)
SUPPORTED_IMAGES = {"image/png", "image/jpeg", "image/webp"}
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


class DocumentParser():

    def __init__(self, llm):
        self.llm = llm


    def _invoke_bedrock_image(self, image_b64, mime_type):
        message = HumanMessage(
            content=[
                {
                    "type": "text",
                    "text": (
                        "You are an expert AI assistant, you are tasked with extracting the entire text from any PDF document. The document can be simple, complex, or even scanned, this shouldn't matter to you."
                        "You will be given the entire PDF as input. Start examining the document page by page, when you come across text, extract it as is don't convert it into another format like HTML or Markdown. If you come across images, replace them with a very detailed description of the image while taking into consideration the context around it."
                        "When you come across tables, describe them too like the image. The description should be very detailed and in a way that someone will understand the table without seeing it."
                        "Make sure to keep the structure of the document, if there are sections, subsections, bullet points, or numbered lists, make sure to keep them as is. If there are any headers, footers, page numbers, remove them."
                        "If there are line breaks mid-sentence, please remove it and display the entire sentence or paragraph in smooth flow."
                        "If there's table that is cut to the next page and has no header, please use the header of the table from the previous page."
                        "The final output should be a clean, well-structured text that represents the content of the entire PDF document as closely as possible to how a human would see it with their eyes when reading the document. Don't say anything else, just output the text you extracted from the PDF."
                        "Here is the PDF:"
                    ),
                },
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": image_b64,
                    },
                },
            ]
        )

        return self.llm.invoke([message]) 


    @staticmethod
    def _encode_image(image_path):
        with open(image_path, "rb") as f:
            return base64.b64encode(f.read()).decode()

            

    @staticmethod
    def _is_url(source: str) -> bool:
        return urlparse(str(source)).scheme in ("http", "https")


    @staticmethod
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


    def _load_source(self, source: str, timeout: int = 30):
        """Return (bytes, mime_type) for a local path or an http(s) URL."""
        if self._is_url(source):
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

        mime_type = self._sniff_mime(data) or header_mime or guessed_mime
        return data, mime_type

    def parse_document(self, source):
        """
            Parse the document content (local path or URL) by analyzing its images
            with Bedrock and extracting text.
        """
        print(f"SOURCEsdfs: {source}")

        if not source:
            return None

        data, mime_type = self._load_source(source)
        results = []

        if mime_type in SUPPORTED_IMAGES:
            image_b64 = base64.b64encode(data).decode()
            response = self._invoke_bedrock_image(image_b64, mime_type)

            print(f"BEDROCK RESPONSE: {response}")
            results.append(response.content)

        elif mime_type == "application/pdf":
            pages = convert_from_bytes(
                data,
                dpi=200,
                fmt="png",
                poppler_path=POPPLER_PATH,
            )

            for i, page in enumerate(pages):
                buffer = io.BytesIO()
                page.save(buffer, format="PNG")

                image_b64 = base64.b64encode(buffer.getvalue()).decode()
                response = self._invoke_bedrock_image(image_b64, "image/png")
                print(f"BEDROCK RESPONSE: {response}")
                print(f"PAGE {i + 1} RESPONSE: {response.content}")
                results.append(response.content)

        else:
            raise ValueError(f"Unsupported file type: {mime_type}")

        return results
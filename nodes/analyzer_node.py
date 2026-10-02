from states.agent_state import AgentState
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field
from typing import Literal
import fitz  # PyMuPDF
import base64

import re

from tools.document import DocumentParser, SUPPORTED_IMAGES


class DocumentType(BaseModel):
    """Whether a document is plain text or has meaningful visual content."""
    reason: str = Field(description="One or two sentences on what visual content was or was not found")
    binary_score: Literal["plain", "multimodal"] = Field(description="'plain' or 'multimodal'")

class AnalyzerNode:


    def __init__(self, tool, llm):
        self.SOURCE_RE = re.compile(r"https?://\S+|[\w.\-/\\:]+\.(?:pdf|png|jpe?g|webp)\b", re.IGNORECASE)
        self.MIN_IMAGE_AREA_RATIO = 0.02  # images smaller than 2% of the page (icons, bullets) are ignored
        self.MIN_VECTOR_PATHS = 20        # this many vector paths on a page usually means a chart or diagram
        self.MAX_VISION_PAGES = 6         # pages sent to the vision model for the final call
        self.VISION_DPI = 100
        self.tool = tool
        self.llm = llm


    def analyze(self, state: AgentState):
        """Finds the file in the request and stores it in state for the router and parsers."""
        print("---CALL ANALYZER AGENT--")
        return {"source": self._find_source(state)}

    def _find_source(self, state):
        """Use state['source'] if set, otherwise pull the first URL or file path out of the user's messages."""
        if state.get("source"):
            return state["source"]
        for message in state["messages"]:
            if isinstance(message, HumanMessage) and isinstance(message.content, str):
                match = self.SOURCE_RE.search(message.content)
                if match:
                    return match.group(0).rstrip(").,;'\"")
        return None    

    def _pdf_candidate_pages(self, data):
        """Return the indexes of pages that hold embedded images or vector graphics, most visual first."""
        doc = fitz.open(stream=data, filetype="pdf")
        candidates = []
        for page in doc:
            page_area = page.rect.width * page.rect.height
            image_ratio = 0.0
            for info in page.get_image_info():
                bbox = fitz.Rect(info["bbox"]) & page.rect
                image_ratio += (bbox.width * bbox.height) / page_area
            vector_paths = len(page.get_drawings())
            print(f"PAGE {page.number + 1}: image area {image_ratio:.0%}, vector paths {vector_paths}, text chars {len(page.get_text().strip())}")
            if image_ratio >= self.MIN_IMAGE_AREA_RATIO or vector_paths >= self.MIN_VECTOR_PATHS:
                candidates.append((image_ratio + vector_paths / 1000, page.number))
        candidates.sort(reverse=True)
        return doc, [number for _, number in candidates]    

    def _classify_with_vision(self, images):
        """Ask the vision model to judge a list of (label, base64 png/jpeg/webp, mime) images."""
        content = [{
            "type": "text",
            "text": (
                "You are checking whether a document needs a multimodal parser or plain OCR.\n"
                "Tag it 'multimodal' if any page has visual content that carries information not written out as text: "
                "charts, graphs, plots, diagrams, flowcharts, illustrations, photos, maps, infographics, or annotated figures.\n"
                "Tag it 'plain' if the pages only hold text, including text tables, forms with filled-in text, "
                "and scanned or photographed pages of text. Logos, letterheads, signatures, stamps, seals, QR or bar codes, "
                "page borders and decorative lines do NOT make a document multimodal.\n"
                "Look at each page image below and decide for the document as a whole."
            ),
        }]
        for label, image_b64, mime_type in images:
            content.append({"type": "text", "text": label})
            content.append({"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": image_b64}})

        return self.llm.with_structured_output(DocumentType).invoke([HumanMessage(content=content)])

    def analyze_document(self, state):
        """
            Determines whether the uploaded file contains plain text only or contains mixed image and text that needs parsing.
            Args:
                state (messages): The current state
            Returns:
                str: 'plain' or 'multimodal'
        """

        print("--ANALYZING DOCUMENT--")

        source = self._find_source(state)
        # print(f"SOURCE: {source}")
        if not source:
            print("---NO FILE FOUND IN THE REQUEST, DEFAULTING TO MULTIMODAL---")
            return "multimodal"

        data, mime_type = DocumentParser(self.llm)._load_source(source)
        print(f"MIME TYPE: {mime_type}")

        if mime_type in SUPPORTED_IMAGES:
            images = [("Image:", base64.b64encode(data).decode(), mime_type)]

        elif mime_type == "application/pdf":
            doc, candidates = self._pdf_candidate_pages(data)
            if not candidates:
                # No embedded images and no vector graphics on any page, so there is nothing visual to parse.
                print("---DECISION: FILE IS ONLY PLAIN (no images or graphics on any page)---")
                return "plain"

            images = []
            for number in sorted(candidates[:self.MAX_VISION_PAGES]):
                png = doc[number].get_pixmap(dpi=self.VISION_DPI).tobytes("png")
                images.append((f"Page {number + 1}:", base64.b64encode(png).decode(), "image/png"))

        else:
            raise ValueError(f"Unsupported file type: {mime_type}")

        result = self._classify_with_vision(images)
        print(f"CLASIFICATION VISION RESPONSE: {result}")

        if result.binary_score == "plain":
            print("---DECISION: FILE IS ONLY PLAIN---")
        else:
            print("---DECISION: FILE IS MULTIMODAL---")

        return result.binary_score    
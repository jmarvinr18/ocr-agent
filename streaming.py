"""Turns a LangGraph run into the event stream vendor-portal-api's `stream_sse` reads.

Each yielded dict becomes one `data: {...}` line (BedrockAgentCoreApp does the SSE framing):

    {"delta": "..."}                        answer text, token by token
    {"reply": "...", "citations": [...],    once at the end: the whole answer plus
     "model": "...", "usage": {...},        what the API stores for source links
     "toolCalls": 0}                        and cost tracking
"""

import re

# Only these nodes write the answer. Tokens from the grader (a conditional edge that runs
# inside "retrieve_tool") and from "rewrite" are internal and never reach the vendor.
ANSWER_NODES = {"agent", "generate", "multimodal_parser", "plaintext_parser"}

OPEN_TAG, CLOSE_TAG = "<thinking>", "</thinking>"
THINKING_RE = re.compile(r"<thinking>.*?</thinking>", re.DOTALL)

MAX_CITATIONS = 10


class ThinkingFilter:
    """Drops Nova's inline `<thinking>...</thinking>` from streamed text.

    langchain-aws strips these tags from whole responses but not from stream chunks, and a
    tag can be split across chunks, so text that might be the start of a tag is held back
    until the next chunk settles it.
    """

    def __init__(self):
        self._buf = ""
        self._inside = False

    def feed(self, text: str) -> str:
        self._buf += text
        out = []
        while True:
            if self._inside:
                end = self._buf.find(CLOSE_TAG)
                if end == -1:
                    # Keep only what could be the start of the closing tag.
                    self._buf = self._buf[-(len(CLOSE_TAG) - 1) :]
                    break
                self._buf = self._buf[end + len(CLOSE_TAG) :]
                self._inside = False
            else:
                start = self._buf.find(OPEN_TAG)
                if start != -1:
                    out.append(self._buf[:start])
                    self._buf = self._buf[start + len(OPEN_TAG) :]
                    self._inside = True
                    continue
                keep = _partial_suffix(self._buf, OPEN_TAG)
                out.append(self._buf[: len(self._buf) - keep])
                self._buf = self._buf[len(self._buf) - keep :]
                break
        return "".join(out)

    def flush(self) -> str:
        rest, self._buf = ("" if self._inside else self._buf), ""
        return rest


def _partial_suffix(text: str, tag: str) -> int:
    """Length of the longest end of `text` that is a start of `tag`."""
    for k in range(min(len(text), len(tag) - 1), 0, -1):
        if text.endswith(tag[:k]):
            return k
    return 0


def _chunk_text(content) -> str:
    """Text of a chat-model chunk; Bedrock Converse sends a list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _citation(doc) -> dict | None:
    """A knowledge-base document as a `document` citation."""
    meta = getattr(doc, "metadata", None) or {}
    source = meta.get("source_metadata") or {}
    uri = (
        (meta.get("location") or {}).get("s3Location", {}).get("uri")
        or source.get("x-amz-bedrock-kb-source-uri")
        or meta.get("source")
    )
    if not uri:
        return None
    return {"type": "document", "id": uri, "label": uri.rstrip("/").rsplit("/", 1)[-1]}


async def stream_answer(graph, inputs: dict, config: dict, model: str):
    """Yield `{"delta": ...}` events while the graph runs, then the final object."""
    filters: dict[str, ThinkingFilter] = {}
    citations: dict[str, dict] = {}
    input_tokens = output_tokens = tool_calls = 0
    started = False  # leading whitespace (e.g. left after a thinking block) is dropped

    def emit(text: str):
        nonlocal started
        if not started:
            text = text.lstrip()
            started = bool(text)
        return text

    async for event in graph.astream_events(inputs, config=config, version="v2"):
        kind = event["event"]
        node = event.get("metadata", {}).get("langgraph_node")

        if kind == "on_chat_model_stream" and node in ANSWER_NODES:
            text = _chunk_text(event["data"]["chunk"].content)
            if text:
                piece = emit(filters.setdefault(event["run_id"], ThinkingFilter()).feed(text))
                if piece:
                    yield {"delta": piece}

        elif kind == "on_chat_model_end":
            if node in ANSWER_NODES and event["run_id"] in filters:
                piece = emit(filters.pop(event["run_id"]).flush())
                if piece:
                    yield {"delta": piece}
            usage = getattr(event["data"].get("output"), "usage_metadata", None) or {}
            input_tokens += usage.get("input_tokens", 0)
            output_tokens += usage.get("output_tokens", 0)

        elif kind == "on_tool_start":
            tool_calls += 1

        elif kind == "on_retriever_end":
            for doc in event["data"].get("output") or []:
                citation = _citation(doc)
                if citation:
                    citations.setdefault(citation["id"], citation)

    state = await graph.aget_state(config)
    messages = state.values.get("messages") or []
    last = messages[-1] if messages else None
    reply = getattr(last, "content", last) if last is not None else ""
    reply = THINKING_RE.sub("", _chunk_text(reply)).strip()

    yield {
        "reply": reply,
        "citations": list(citations.values())[:MAX_CITATIONS],
        "model": model,
        "usage": {"inputTokens": input_tokens, "outputTokens": output_tokens},
        "toolCalls": tool_calls,
    }

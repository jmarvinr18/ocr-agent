from typing_extensions import TypedDict
from typing import Annotated, Sequence, Literal
from pydantic import BaseModel, Field
from langgraph.graph.message import add_messages 
from langchain_core.messages import AnyMessage

class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    source: str = Field(..., description="Local file path or http(s) URL of a PDF/PNG/JPEG/WEBP")
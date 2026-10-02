from langchain_core.messages import SystemMessage, HumanMessage,AnyMessage
from states.agent_state import AgentState
from tools.document import DocumentParser


class MultimodalParserNode:

    def __init__(self, tool, llm):
        self.tool = tool
        self.llm = llm

    def parse(self, state: AgentState):
        """Parses a multimodal document page by page with the vision model, then answers the user's question from it."""

        parser = DocumentParser(self.llm).parse_document(state["source"])
        print("---CALL PARSER AGENT--")

        pages = parser
        document_text = "\n\n".join(
            f"--- Page {i} ---\n{page if isinstance(page, str) else ''.join(b.get('text', '') for b in page if isinstance(b, dict))}"
            for i, page in enumerate(pages, 1)
        )

        response = self.llm.invoke([
            SystemMessage(content="Answer the user's question using only the document below.\n\n" + document_text),
            HumanMessage(content=state["messages"][0].content),
        ])
        print(f"RESPONSE FROM PARSER AGENT: {response.content}")
        return {"messages": [response]}    
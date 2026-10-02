from states.agent_state import AgentState
from nodes.multimodal_parser_node import MultimodalParserNode


class PlainTextParserNode:

    def __init__(self, tool, llm):
        self.tool = tool
        self.llm = llm

    def parse(self, state: AgentState):
        """
            Use AWS Texttract and Comprehend to extract text from the document
        """
        print(f"SIMPLE EXTRACT TOOL IS SELECTED")
        # TODO: Textract + Comprehend. Until then fall back to the vision parser so plain docs still get an answer.
        return MultimodalParserNode(self.tool, self.llm).parse(state)

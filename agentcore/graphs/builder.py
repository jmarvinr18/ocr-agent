# from dotenv import load_dotenv
# load_dotenv()

# from config import load_secrets
# load_secrets()
import os
from langgraph.graph import StateGraph, START, END
# from llms.groq import GroqLLM
from agentcore.states.agent_state import AgentState
from langgraph.prebuilt import ToolNode
from agentcore.tools.document import DocumentParser
from agentcore.nodes.multimodal_parser_node import MultimodalParserNode
from agentcore.nodes.plaintext_parser_node import PlainTextParserNode
from agentcore.nodes.analyzer_node import AnalyzerNode

from langgraph.prebuilt import tools_condition
from agentcore.llms.bedrock import BedrockLLM
from langgraph.checkpoint.memory import MemorySaver
from langchain_mcp_adapters.client import MultiServerMCPClient


class GraphBuilder:
    def __init__(self, llm):

        self.graph = StateGraph(AgentState)
        self.llm = llm

    async def build_graph(self):
        """
        Build a graph to generate blogs based on topic
        """

        document_tools = DocumentParser(self.llm)

        tools = [document_tools.parse_document]

        toolnode = ToolNode(tools, handle_tool_errors=True)
        multimodal_parser = MultimodalParserNode(tools, self.llm)
        plaintext_parser = PlainTextParserNode(tools, self.llm)
        analyzer = AnalyzerNode(tools, self.llm)

        # NODES =======================================================================================
        self.graph.add_node("extract_text_tool", toolnode)
        self.graph.add_node("plaintext_parser", plaintext_parser.parse)
        self.graph.add_node("analyzer_agent", analyzer.analyze)
        self.graph.add_node("multimodal_parser", multimodal_parser.parse)        

        # EDGES =======================================================================================
        self.graph.add_edge(START, "analyzer_agent")
        self.graph.add_conditional_edges(
            "analyzer_agent",
            analyzer.analyze_document,
            {"plain": "plaintext_parser", "multimodal": "multimodal_parser"},
        )
        self.graph.add_conditional_edges(
            "multimodal_parser",
            tools_condition,
            {"tools": "extract_text_tool", END: END},  # no tool call → go to the parser
        )
        self.graph.add_edge("plaintext_parser", END)  # send the OCR result back to the analyzer

        return self.graph

    async def setup_graph(self):
        await self.build_graph()

        return self.graph.compile(checkpointer=MemorySaver())


## Below code is for the langsmith, langgraph studio
## (point langgraph.json at "graphs/builder.py:make_graph")
async def make_graph():
    graph_builder = GraphBuilder(BedrockLLM().get_llm())
    await graph_builder.build_graph()
    return graph_builder.graph.compile()

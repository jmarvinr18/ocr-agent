# agent.py
import asyncio

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from agentcore.graphs.builder import GraphBuilder   # your existing graph
from agentcore.llms.bedrock import BedrockLLM
from agentcore.streaming import stream_answer

app = BedrockAgentCoreApp()
llm = BedrockLLM().get_llm()
graph = None
_graph_lock = asyncio.Lock()


async def get_graph():
    """Build the graph on first request (MCP tools must be loaded with await)."""
    global graph
    if graph is None:
        async with _graph_lock:
            if graph is None:
                graph = await GraphBuilder(llm).setup_graph()
    return graph

@app.entrypoint
async def invoke(payload, context):

    print(f"PAYLOAD: {payload}")

    graph = await get_graph()

    inputs = {"messages": [{"role": "user", "content": payload["message"]}]}
    config = {"configurable": {"thread_id": context.session_id}}

    # Returning an async generator makes AgentCore answer with text/event-stream.
    # Send {"stream": false} for the old single JSON response.
    if payload.get("stream", True):
        return stream_answer(graph, inputs, config, model=llm.model_id)

    result = await graph.ainvoke(inputs, config=config)
    return {
        "result": result["messages"][-1].content,
        "messages": [m.model_dump(mode="json") for m in result["messages"]],
    }

if __name__ == "__main__":
    app.run()
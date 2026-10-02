import os
from dotenv import load_dotenv
from langchain_aws import ChatBedrockConverse

class BedrockLLM:
    def __init__(self):
        load_dotenv()

    def get_llm(self, model_name="apac.amazon.nova-lite-v1:0"):

        try:

            llm = ChatBedrockConverse(
                model_id=model_name,
                region_name="ap-southeast-1",
                # Nova is an Amazon model; with provider="anthropic" langchain-aws
                # couldn't verify streaming support and fell back to non-streaming.
                provider="amazon",
                disable_streaming=False,
            )

            return llm
        except Exception as e:
            raise ValueError("Error occurred with exception: {e}")

        
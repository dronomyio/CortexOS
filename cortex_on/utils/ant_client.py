from pydantic_ai.models.anthropic import AnthropicModel
from anthropic import AsyncAnthropic
import os
from dotenv import load_dotenv

load_dotenv()

def get_client():
    api_key = os.getenv("ANTHROPIC_API_KEY")

    client = AsyncAnthropic(api_key=api_key, 
                         max_retries=3,
                         timeout=10000)
    return client

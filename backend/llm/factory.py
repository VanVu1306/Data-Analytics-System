"""Construct the configured cloud LLM provider."""
from __future__ import annotations

from backend.config import Settings
from backend.llm.base import LLMClient
from backend.llm.deepseek_client import DeepSeekClient
from backend.llm.gpt_client import GPTClient


def create_cloud_llm(settings: Settings) -> LLMClient:
    provider = settings.llm_provider
    if provider == "deepseek":
        return DeepSeekClient(settings)
    if provider == "openai":
        return GPTClient(settings)
    raise ValueError(
        f"Unsupported LLM_PROVIDER {provider!r}. Choose 'deepseek' or 'openai'."
    )

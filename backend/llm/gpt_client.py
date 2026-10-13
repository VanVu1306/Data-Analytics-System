"""
backend/llm/gpt_client.py
-------------------------
OpenAI GPT client using the OpenAI Chat Completions API.
"""
from __future__ import annotations

from typing import Optional

from backend.config import Settings
from backend.llm.base import LLMClient


class GPTClient(LLMClient):
    name = "openai"

    def __init__(self, settings: Settings):
        self._api_key = settings.openai_api_key
        self._model = settings.openai_model

    def is_available(self) -> bool:
        return bool(self._api_key)

    def complete(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        from openai import OpenAI

        client = OpenAI(api_key=self._api_key)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs = {"model": model or self._model, "messages": messages}
        if max_tokens is not None:
            kwargs["max_completion_tokens"] = max_tokens
        response = client.chat.completions.create(**kwargs)

        content = response.choices[0].message.content
        if content is None:
            raise RuntimeError("OpenAI returned an empty message.")
        return content.strip()

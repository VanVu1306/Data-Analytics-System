"""
backend/llm/deepseek_client.py
------------------------------
DeepSeek-Chat qua OpenAI SDK (API tương thích OpenAI).
Dùng cho Stage 1 (SQL) và làm fallback cho Stage 3 (viz code).
"""
from __future__ import annotations

from typing import Optional

from backend.config import Settings
from backend.llm.base import LLMClient


class DeepSeekClient(LLMClient):
    name = "deepseek"

    def __init__(self, settings: Settings):
        self._api_key = settings.deepseek_api_key
        self._base_url = settings.deepseek_base_url
        self._model = settings.deepseek_model

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
        from openai import OpenAI  # import lười: chỉ cần khi thật sự gọi API

        client = OpenAI(api_key=self._api_key, base_url=self._base_url)
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs = {"model": model or self._model, "messages": messages}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        response = client.chat.completions.create(**kwargs)
        return response.choices[0].message.content.strip()

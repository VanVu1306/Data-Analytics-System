"""
backend/llm/base.py
-------------------
Giao diện chung cho mọi LLM provider. Agents chỉ biết `LLMClient`,
không biết provider cụ thể (DeepSeek, Ollama, ...).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class LLMError(RuntimeError):
    """Lỗi khi gọi LLM (kế thừa RuntimeError để giữ tương thích hành vi cũ)."""


class LLMClient(ABC):
    name: str = "llm"

    @abstractmethod
    def is_available(self) -> bool:
        """Provider có sẵn sàng không (có key / server đang chạy)."""

    @abstractmethod
    def complete(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Gửi prompt, trả về text. Raise LLMError/RuntimeError nếu lỗi."""

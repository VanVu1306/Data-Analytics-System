"""
backend/llm/ollama_client.py
----------------------------
Wrapper gọi Ollama local API (Llama3.2) cho Stage 3 (Text-to-Python).
Dữ liệu thật không rời máy khi dùng provider này.
"""
from __future__ import annotations

import json
from typing import List, Optional

import requests

from backend.config import Settings
from backend.llm.base import LLMClient, LLMError


class OllamaClient(LLMClient):
    name = "ollama"

    def __init__(self, settings: Settings):
        self._base_url = settings.ollama_base_url.rstrip("/")
        self._default_model = settings.ollama_model
        self._timeout = settings.ollama_timeout
        self._max_tokens = settings.ollama_max_tokens
        self._temperature = settings.ollama_temperature
        self._top_p = settings.ollama_top_p
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    # ── Health / models ────────────────────────────────────────────────────
    @property
    def default_model(self) -> str:
        return self._default_model

    def is_available(self) -> bool:
        """Ollama server có đang chạy không."""
        try:
            r = requests.get(f"{self._base_url}/api/tags", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def list_models(self) -> List[str]:
        """Liệt kê các model đã pull về."""
        try:
            r = requests.get(f"{self._base_url}/api/tags", timeout=5)
            data = r.json()
            return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []

    # ── Completion ─────────────────────────────────────────────────────────
    def complete(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ) -> str:
        payload = {
            "model": model or self._default_model,
            "prompt": prompt,
            "stream": stream,
            "options": {
                "num_predict": max_tokens if max_tokens is not None else self._max_tokens,
                "temperature": temperature if temperature is not None else self._temperature,
                "top_p": self._top_p,
            },
        }
        if system:
            payload["system"] = system

        try:
            response = self._session.post(
                f"{self._base_url}/api/generate", json=payload, timeout=self._timeout
            )
            response.raise_for_status()
            text = _parse_response(response).strip()
            if not text:
                raise LLMError("Empty response from Ollama")
            return text
        except requests.exceptions.ConnectionError:
            raise LLMError(
                f"Cannot connect to Ollama at {self._base_url}. "
                "Make sure Ollama is running: 'ollama serve'"
            )
        except requests.exceptions.Timeout:
            raise LLMError(f"Ollama request timed out after {self._timeout}s")
        except requests.exceptions.HTTPError as e:
            raise LLMError(f"Ollama HTTP error: {e}")


# ── Response parsing (giữ nguyên logic bản gốc) ────────────────────────────

def _parse_response(response: requests.Response) -> str:
    """Parse response từ Ollama (JSON đơn hoặc NDJSON streaming)."""
    text = response.text.strip()
    if not text:
        return ""
    try:
        return _extract_text(response.json())
    except ValueError:
        lines = [line for line in text.splitlines() if line.strip()]
        for line in reversed(lines):
            try:
                t = _extract_text(json.loads(line))
                if t:
                    return t
            except ValueError:
                continue
        return text


def _extract_text(data) -> str:
    """Trích text từ response của Ollama (nhiều format)."""
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        if "response" in data:
            return str(data["response"])
        if "choices" in data and isinstance(data["choices"], list) and data["choices"]:
            choice = data["choices"][0]
            if isinstance(choice, dict):
                return choice.get("text", choice.get("content", ""))
        if "message" in data and isinstance(data["message"], dict):
            return data["message"].get("content", "")
    return str(data) if data else ""

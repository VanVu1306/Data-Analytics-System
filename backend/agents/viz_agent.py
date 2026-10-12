"""
backend/agents/viz_agent.py
---------------------------
VizAgent (Stage 3 — Local-Python-Viz): câu hỏi + DataFrame -> code matplotlib.

Chuỗi fallback (giữ nguyên hành vi bản gốc):
    mock?            -> mock code
    use_ollama:
        Ollama chạy  -> Llama3 local
                          code hợp lệ        -> dùng
                          code không hợp lệ  -> mock
                          lỗi gọi Ollama     -> DeepSeek (nếu có key) -> mock
        Ollama không chạy                    -> DeepSeek (nếu có key) -> mock
    ngược lại        -> DeepSeek (nếu có key) -> mock

Agent chỉ SINH code; việc chạy code do tool `render_chart` đảm nhiệm.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from backend.config import Settings
from backend.llm import LLMClient
from backend.prompts import VIZ_CLOUD_SYSTEM_PROMPT, build_viz_prompt
from backend.tools import ToolRegistry, clean_code, is_valid_code


@dataclass
class VizCodeGeneration:
    code: str
    prompt: str
    source: str  # "ollama" | "deepseek" | "mock"


class VizAgent:
    def __init__(
        self,
        local_llm: LLMClient,
        cloud_llm: LLMClient,
        tools: ToolRegistry,
        settings: Settings,
    ):
        self._local = local_llm
        self._cloud = cloud_llm
        self._tools = tools
        self._settings = settings

    # ── Public ─────────────────────────────────────────────────────────────
    def generate_code(
        self,
        question: str,
        df: pd.DataFrame,
        use_mock: bool = False,
        use_ollama: bool = True,
        model: Optional[str] = None,
    ) -> VizCodeGeneration:
        prompt = build_viz_prompt(question, df, sample_rows=self._settings.viz_sample_rows)

        if use_mock:
            return self._mock(df, question, prompt)

        if use_ollama:
            if self._local.is_available():
                try:
                    raw = self._local.complete(prompt, model=model)
                    code = clean_code(raw)
                    if is_valid_code(code):
                        return VizCodeGeneration(code, prompt, self._local.name)
                    return self._mock(
                        df, question, prompt + "\n\n[Ollama returned invalid code, using mock]"
                    )
                except Exception as e:
                    err_msg = f"\n\n[Ollama error: {e}]"
                    return self._cloud_or_mock(
                        df, question, prompt, err_msg,
                        cloud_suffix=" → fallback DeepSeek",
                        mock_suffix=" → fallback Mock",
                    )
            err_msg = "\n\n[Ollama server not running at localhost:11434]"
            return self._cloud_or_mock(
                df, question, prompt, err_msg,
                cloud_suffix=" → using DeepSeek",
                mock_suffix=" → using Mock",
            )

        # Dùng DeepSeek trực tiếp
        if self._cloud.is_available():
            try:
                return VizCodeGeneration(self._cloud_code(prompt), prompt, self._cloud.name)
            except Exception as e:
                return self._mock(df, question, f"{prompt}\n\n[DeepSeek error: {e}]")

        return self._mock(df, question, prompt)

    # ── Helpers ────────────────────────────────────────────────────────────
    def _cloud_code(self, prompt: str) -> str:
        raw = self._cloud.complete(
            prompt,
            system=VIZ_CLOUD_SYSTEM_PROMPT,
            temperature=self._settings.viz_cloud_temperature,
            max_tokens=self._settings.viz_cloud_max_tokens,
        )
        return clean_code(raw)

    def _cloud_or_mock(
        self,
        df: pd.DataFrame,
        question: str,
        prompt: str,
        err_msg: str,
        cloud_suffix: str,
        mock_suffix: str,
    ) -> VizCodeGeneration:
        if self._cloud.is_available():
            try:
                code = self._cloud_code(prompt)
                return VizCodeGeneration(code, prompt + err_msg + cloud_suffix, self._cloud.name)
            except Exception:
                pass
        return self._mock(df, question, prompt + err_msg + mock_suffix)

    def _mock(self, df: pd.DataFrame, question: str, prompt: str) -> VizCodeGeneration:
        code = self._tools.get("mock_viz_code").run(df=df, question=question)
        return VizCodeGeneration(code, prompt, "mock")

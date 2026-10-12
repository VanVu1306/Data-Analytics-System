"""
backend/tools/base.py
---------------------
Tool = hành động xác định (không gọi LLM): đọc schema, chạy SQL, vẽ biểu đồ...
Agents quyết định *gọi tool nào*, ToolRegistry là nơi tra cứu tool theo tên.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List


class Tool(ABC):
    name: str = ""
    description: str = ""

    @abstractmethod
    def run(self, **kwargs) -> Any:
        ...

    def __call__(self, **kwargs) -> Any:
        return self.run(**kwargs)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> "ToolRegistry":
        if not tool.name:
            raise ValueError("Tool must define a non-empty `name`")
        self._tools[tool.name] = tool
        return self

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(f"Tool '{name}' is not registered. Available: {self.names()}")

    def names(self) -> List[str]:
        return sorted(self._tools)

    def describe(self) -> Dict[str, str]:
        return {n: t.description for n, t in sorted(self._tools.items())}

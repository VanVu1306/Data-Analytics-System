"""Fixtures dùng chung: DB demo tạm, LLM giả, container giả."""
import dataclasses
import importlib.util
import json
from pathlib import Path
from typing import List, Optional

import pytest

from backend.config import load_settings
from backend.container import build_container
from backend.llm import LLMClient
from backend.tools import resolve_chart_plan

ROOT = Path(__file__).resolve().parent.parent


def _is_planner_call(system) -> bool:
    return bool(system) and system.startswith("You are the planner")


def _default_plan(prompt: str) -> str:
    """Kế hoạch của một "LLM planner ngoan": một bước query, vẽ biểu đồ khi câu hỏi yêu cầu (nhận biết bằng intent)."""
    question = prompt.split("Question: ", 1)[-1].rsplit("\n\nJSON plan:", 1)[0]
    chart = resolve_chart_plan(question, "detect")
    return json.dumps({
        "steps": [{"op": "query"}],
        "chart": {"requested": chart.requested, "type": chart.chart_type, "goal": chart.goal},
        "clarification": None,
    })


class FakeLLM(LLMClient):
    """LLM giả: ghi lại các lần gọi, trả về response cố định hoặc raise lỗi.

    Lời gọi của Planner (system prompt "You are the planner...") được tách riêng: ghi vào `planner_calls` (không vào
    `calls`, nên các test đếm lời gọi SQL/answer/viz không bị ảnh hưởng) và trả `planner_response`; nếu không đặt thì
    trả kế hoạch mặc định "một bước query" (+ biểu đồ khi câu hỏi yêu cầu).
    """

    def __init__(self, name="fake", available=True, response="", raises: Optional[Exception] = None,
                 planner_response: Optional[str] = None, planner_raises: Optional[Exception] = None):
        self.name = name
        self.available = available
        self.response = response
        self.raises = raises
        self.planner_response = planner_response
        self.planner_raises = planner_raises
        self.calls: List[dict] = []
        self.planner_calls: List[dict] = []
        self._script = None

    def script(self, *responses):
        """Các lời gọi KHÔNG phải planner lần lượt trả từng phần tử (Exception thì raise)."""
        self._script = iter(responses)
        return self

    def is_available(self) -> bool:
        return self.available

    def complete(self, prompt, *, system=None, model=None, temperature=None, max_tokens=None):
        call = dict(prompt=prompt, system=system, model=model, temperature=temperature, max_tokens=max_tokens)
        if _is_planner_call(system):
            self.planner_calls.append(call)
            if self.planner_raises or self.raises:
                raise self.planner_raises or self.raises
            return self.planner_response if self.planner_response is not None else _default_plan(prompt)
        self.calls.append(call)
        if self._script is not None:
            item = next(self._script)
            if isinstance(item, Exception):
                raise item
            return item
        if self.raises:
            raise self.raises
        return self.response


class FakeOllama(FakeLLM):
    default_model = "llama3.2"

    def __init__(self, models=None, **kw):
        super().__init__(name="ollama", **kw)
        self._models = models or ["llama3.2"]

    def list_models(self):
        return self._models


@pytest.fixture(scope="session")
def demo_db(tmp_path_factory) -> str:
    """Tạo DB demo vào thư mục tạm bằng chính scripts/create_demo_db.py."""
    spec = importlib.util.spec_from_file_location("create_demo_db", ROOT / "scripts" / "create_demo_db.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    db_dir = tmp_path_factory.mktemp("demo_db")
    mod.DB_DIR = str(db_dir)
    mod.DB_PATH = str(db_dir / "sales_demo.db")
    mod.create_db()
    return mod.DB_PATH


@pytest.fixture
def settings(demo_db, tmp_path):
    # deepseek_api_key rỗng để test không phụ thuộc biến môi trường của máy; dataset_dir nằm trong thư mục tạm
    return dataclasses.replace(
        load_settings(), db_path=demo_db, deepseek_api_key="", dataset_dir=str(tmp_path / "datasets")
    )


@pytest.fixture
def make_container(settings):
    def _make(deepseek=None, ollama=None, **settings_overrides):
        s = dataclasses.replace(settings, **settings_overrides) if settings_overrides else settings
        return build_container(
            s,
            deepseek=deepseek or FakeLLM(name="deepseek", available=False),
            ollama=ollama or FakeOllama(available=False),
        )

    return _make

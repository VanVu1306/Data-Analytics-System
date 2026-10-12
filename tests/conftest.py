"""Fixtures dùng chung: DB demo tạm, LLM giả, container giả."""
import dataclasses
import importlib.util
from pathlib import Path
from typing import List, Optional

import pytest

from backend.config import load_settings
from backend.container import build_container
from backend.llm import LLMClient

ROOT = Path(__file__).resolve().parent.parent


class FakeLLM(LLMClient):
    """LLM giả: ghi lại các lần gọi, trả về response cố định hoặc raise lỗi."""

    def __init__(self, name="fake", available=True, response="", raises: Optional[Exception] = None):
        self.name = name
        self.available = available
        self.response = response
        self.raises = raises
        self.calls: List[dict] = []

    def is_available(self) -> bool:
        return self.available

    def complete(self, prompt, *, system=None, model=None, temperature=None, max_tokens=None):
        self.calls.append(
            dict(prompt=prompt, system=system, model=model, temperature=temperature, max_tokens=max_tokens)
        )
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
def settings(demo_db):
    # deepseek_api_key rỗng để test không phụ thuộc biến môi trường của máy
    return dataclasses.replace(load_settings(), db_path=demo_db, deepseek_api_key="")


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

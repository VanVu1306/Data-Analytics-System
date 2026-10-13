"""Kiểm tra chuỗi fallback của agents (bảo đảm hành vi giữ nguyên sau refactor)."""
import pandas as pd

from tests.conftest import FakeLLM, FakeOllama

VALID_CODE = "import matplotlib.pyplot as plt\nplt.figure(figsize=(10, 6))\nplt.plot([1, 2, 3])\nplt.tight_layout()"
DF = pd.DataFrame({"region_name": ["North", "South"], "total_revenue": [10.0, 20.0]})


# ── SQLAgent ────────────────────────────────────────────────────────────────

def test_sql_agent_mock_when_no_key(make_container):
    c = make_container()  # deepseek không available
    gen = c.sql_agent.generate("Revenue by region")
    assert gen.source == "mock" and "regions" in gen.sql
    assert "/* Answer the following: Revenue by region */" in gen.prompt
    assert "CREATE TABLE orders" in gen.prompt


def test_sql_agent_uses_llm_and_cleans_output(make_container):
    ds = FakeLLM(name="deepseek", response="```sql\nSELECT 1 AS one;\n```")
    c = make_container(deepseek=ds)
    gen = c.sql_agent.generate("anything")
    assert gen.sql == "SELECT 1 AS one" and gen.source == "deepseek"
    assert ds.calls[0]["temperature"] == 0 and ds.calls[0]["max_tokens"] == 512
    assert "SQL assistant" in ds.calls[0]["system"]


def test_sql_agent_mock_flag_skips_llm(make_container):
    ds = FakeLLM(name="deepseek", response="SELECT 1")
    c = make_container(deepseek=ds)
    gen = c.sql_agent.generate("Revenue by region", use_mock=True)
    assert gen.source == "mock" and ds.calls == []


def test_sql_agent_llm_error_returns_comment(make_container):
    c = make_container(deepseek=FakeLLM(name="deepseek", raises=RuntimeError("quota")))
    gen = c.sql_agent.generate("x")
    assert gen.sql == "-- Error calling DeepSeek: quota" and gen.source == "error"


def test_sql_agent_error_uses_configured_provider_name(make_container):
    c = make_container(deepseek=FakeLLM(name="openai", raises=RuntimeError("quota")))
    gen = c.sql_agent.generate("x")
    assert gen.sql == "-- Error calling OpenAI: quota" and gen.source == "error"


# ── VizAgent ────────────────────────────────────────────────────────────────

def test_viz_agent_mock_flag(make_container):
    ol, ds = FakeOllama(response=VALID_CODE), FakeLLM(name="deepseek", response=VALID_CODE)
    c = make_container(deepseek=ds, ollama=ol)
    gen = c.viz_agent.generate_code("bar", DF, use_mock=True)
    assert gen.source == "mock" and ol.calls == [] and ds.calls == []


def test_viz_agent_ollama_success_and_model_passthrough(make_container):
    ol = FakeOllama(response="```python\n" + VALID_CODE + "\n```")
    c = make_container(ollama=ol)
    gen = c.viz_agent.generate_code("bar", DF, model="llama3.2:1b")
    assert gen.source == "ollama" and gen.code == VALID_CODE
    assert ol.calls[0]["model"] == "llama3.2:1b"


def test_viz_agent_ollama_invalid_code_goes_straight_to_mock(make_container):
    ol = FakeOllama(response="sorry, I cannot")
    ds = FakeLLM(name="deepseek", response=VALID_CODE)
    c = make_container(deepseek=ds, ollama=ol)
    gen = c.viz_agent.generate_code("bar", DF)
    assert gen.source == "mock" and ds.calls == []  # không thử DeepSeek, như bản gốc
    assert gen.prompt.endswith("[Ollama returned invalid code, using mock]")


def test_viz_agent_ollama_error_falls_back_to_deepseek(make_container):
    ol = FakeOllama(raises=RuntimeError("timeout"))
    ds = FakeLLM(name="deepseek", response=VALID_CODE)
    c = make_container(deepseek=ds, ollama=ol)
    gen = c.viz_agent.generate_code("bar", DF)
    assert gen.source == "deepseek" and gen.code == VALID_CODE
    assert gen.prompt.endswith("[Ollama error: timeout] → fallback DeepSeek")


def test_viz_agent_ollama_error_without_key_falls_back_to_mock(make_container):
    c = make_container(ollama=FakeOllama(raises=RuntimeError("timeout")))
    gen = c.viz_agent.generate_code("bar", DF)
    assert gen.source == "mock" and gen.prompt.endswith("→ fallback Mock")


def test_viz_agent_ollama_down(make_container):
    ds = FakeLLM(name="deepseek", response=VALID_CODE)
    c = make_container(deepseek=ds, ollama=FakeOllama(available=False))
    gen = c.viz_agent.generate_code("bar", DF)
    assert gen.source == "deepseek"
    assert "[Ollama server not running at localhost:11434] → using DeepSeek" in gen.prompt

    c = make_container(ollama=FakeOllama(available=False))
    gen = c.viz_agent.generate_code("bar", DF)
    assert gen.source == "mock" and gen.prompt.endswith("→ using Mock")


def test_viz_agent_use_ollama_false_goes_to_deepseek(make_container):
    ol, ds = FakeOllama(response=VALID_CODE), FakeLLM(name="deepseek", response=VALID_CODE)
    c = make_container(deepseek=ds, ollama=ol)
    gen = c.viz_agent.generate_code("bar", DF, use_ollama=False)
    assert gen.source == "deepseek" and ol.calls == []
    assert ds.calls[0]["temperature"] == 0.1 and ds.calls[0]["max_tokens"] == 800

    ds_err = FakeLLM(name="deepseek", raises=RuntimeError("boom"))
    c = make_container(deepseek=ds_err)
    gen = c.viz_agent.generate_code("bar", DF, use_ollama=False)
    assert gen.source == "mock" and gen.prompt.endswith("[DeepSeek error: boom]")

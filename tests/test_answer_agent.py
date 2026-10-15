"""Giai đoạn 7: Answer Agent dùng chung cho query-only, EDA-only và query + EDA; grounding theo bằng chứng;
trạng thái sampled/truncated/lỗi; template fallback nhất quán; tinh gọn tools."""
import io
import json

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.container import get_container
from backend.main import create_app
from backend.tools import (
    AnswerEvidence,
    EdaPayload,
    QueryEvidence,
    answer_is_grounded,
    build_evidence_context,
    describe_payload,
    is_vietnamese,
    missing_caveats,
    template_answer,
)
from backend.tools.answer_evidence import QUERY_CLARIFICATION, QUERY_ERROR
from tests.conftest import FakeLLM

CSV = ("ten,diem,lop\n" + "\n".join(f"{n},{d},{l}" for n, d, l in [
    ("An", 8, "A"), ("Binh", 6, "A"), ("Chi", "", "B"), ("Dung", 9, "B"), ("An", 8, "A")])).encode()
Q = "Điểm theo lớp"
DF = pd.DataFrame({"lop": ["A", "B"], "diem_tb": [7.33, 9.0]})


def dq_payload(**kw) -> EdaPayload:
    base = dict(
        operation="data_quality", table="diem", columns=["ten", "diem", "lop"],
        summary={"n_rows": 5, "columns_with_nulls": ["diem"], "total_null_cells": 1, "duplicate_rows": 1,
                 "duplicate_basis": "mọi cột", "columns_with_blanks": []},
        data=pd.DataFrame({"column": ["ten", "diem"], "null_count": [0, 1]}),
    )
    base.update(kw)
    return EdaPayload(**base)


def failed_payload() -> EdaPayload:
    return EdaPayload(operation="numeric_summary", table="diem", status="not_applicable",
                      error="Bảng không có cột số phù hợp.", exact=True)


@pytest.fixture
def agent_env(make_container):
    def _make(llm=None, **kw):
        llm = llm or FakeLLM(name="deepseek")
        c = make_container(deepseek=llm, **kw)
        return c, llm
    return _make


# ── Bằng chứng & ngữ cảnh gửi LLM ───────────────────────────────────────────
def test_evidence_kinds_and_interpretable():
    assert AnswerEvidence().is_empty and AnswerEvidence().kind == "none"
    q = AnswerEvidence.from_query(DF)
    e = AnswerEvidence(eda=[dq_payload()])
    both = AnswerEvidence(query=q.query, eda=[dq_payload()])
    assert (q.kind, e.kind, both.kind) == ("query", "eda", "query+eda")
    assert q.interpretable and e.interpretable and both.interpretable
    assert not AnswerEvidence.from_query(pd.DataFrame()).interpretable          # 0 dòng
    assert not AnswerEvidence(eda=[failed_payload()]).interpretable             # mọi EDA lỗi
    assert not AnswerEvidence(query=QueryEvidence(status=QUERY_ERROR)).interpretable


def test_context_contains_only_present_branches_and_flags():
    ctx = build_evidence_context(AnswerEvidence(eda=[dq_payload(sampled=True, exact=False, truncated=True)]), 20)
    assert "[QUERY RESULT]" not in ctx.text            # nhánh không chạy: không có dữ liệu giả
    assert "[EDA RESULT EA] operation=data_quality; table=diem" in ctx.text
    assert "sampled=true" in ctx.text and "truncated=true" in ctx.text and "exact=false" in ctx.text
    ctx = build_evidence_context(AnswerEvidence.from_query(DF, truncated=True), 20)
    assert "[EDA RESULT" not in ctx.text and "NOT the complete answer set" in ctx.text
    assert "lop,diem_tb" in ctx.text and "A,7.33" in ctx.text


def test_context_marks_failed_and_missing_parts():
    ev = AnswerEvidence(query=QueryEvidence(status=QUERY_ERROR), eda=[dq_payload(), failed_payload()])
    text = build_evidence_context(ev, 20).text
    assert "[QUERY RESULT] Not available" in text and "status=not_applicable" in text and "FAILED" not in text
    err = EdaPayload(operation="correlation", table="diem", status="error", error="hết thời gian")
    assert "FAILED" in build_evidence_context(AnswerEvidence(eda=[err]), 20).text


def test_context_limits_rows_cells_and_summary():
    big = pd.DataFrame({"x": range(100), "s": ["y" * 500] * 100})
    ctx = build_evidence_context(AnswerEvidence.from_query(big), 5)
    assert "rows shown below: 5" in ctx.text and ctx.text.count("…") == 5 and "y" * 81 not in ctx.text
    p = dq_payload(summary={"stats": [{"column": f"c{i}", "mean": i * 1.5} for i in range(400)]},
                   data=pd.DataFrame({"n": range(50)}))
    text = build_evidence_context(AnswerEvidence(eda=[p]), 8).text
    assert "(cut)" in text and "rows shown below: 8 of 50" in text and len(text) < 3500


# ── Kiểm tra bám số liệu trên bằng chứng EDA ────────────────────────────────
def test_grounding_uses_eda_payload_numbers():
    ev = AnswerEvidence(eda=[dq_payload()])
    assert answer_is_grounded("Bảng có 5 dòng, 1 dòng trùng lặp và 1 ô NULL.", "Kiểm tra chất lượng", ev)
    assert not answer_is_grounded("Bảng có 5 dòng, tức 20% trùng lặp.", "Kiểm tra chất lượng", ev)  # tự tính %
    assert not answer_is_grounded("Bảng có 12 dòng.", "Kiểm tra chất lượng", ev)


def test_grounding_combines_query_and_eda_but_not_unseen_rows():
    ev = AnswerEvidence(query=AnswerEvidence.from_query(DF).query, eda=[dq_payload()])
    assert answer_is_grounded("Lớp B có điểm 9.0; bảng gốc có 5 dòng.", "q", ev)
    assert not answer_is_grounded("Tổng điểm là 16.33.", "q", ev)                 # số tự tính
    wide = pd.DataFrame({"n": list(range(1000, 1050))})
    assert not answer_is_grounded("Giá trị cuối là 1049.", "q", AnswerEvidence.from_query(wide), max_rows=5)


# ── Answer Agent: ba loại bằng chứng ────────────────────────────────────────
def test_query_only_llm_answer(agent_env):
    llm = FakeLLM(name="deepseek", response="Lớp B có điểm trung bình 9.0.")
    c, llm = agent_env(llm)
    g = c.answer_agent.generate(Q, AnswerEvidence.from_query(DF))
    assert (g.source, g.fallback_reason) == ("deepseek", None) and g.text == "Lớp B có điểm trung bình 9.0."
    assert "[QUERY RESULT]" in llm.calls[0]["prompt"] and "[EDA RESULT" not in llm.calls[0]["prompt"]
    assert "ONLY the evidence" in llm.calls[0]["system"]


def test_eda_only_llm_answer(agent_env):
    c, llm = agent_env(FakeLLM(name="deepseek", response="Bảng diem có 5 dòng và 1 dòng trùng lặp."))
    g = c.answer_agent.generate("Kiểm tra chất lượng bảng diem", AnswerEvidence(eda=[dq_payload()]))
    assert g.source == "deepseek" and g.fallback_reason is None
    assert "[QUERY RESULT]" not in llm.calls[0]["prompt"] and "operation=data_quality" in llm.calls[0]["prompt"]


def test_query_plus_eda_one_call_in_plan_order(agent_env):
    c, llm = agent_env(FakeLLM(name="deepseek", response="Lớp B có điểm 9.0; bảng có 1 dòng trùng lặp."))
    ev = AnswerEvidence(query=AnswerEvidence.from_query(DF).query,
                        eda=[dq_payload(), dq_payload(operation="table_overview", summary={"n_rows": 5, "n_columns": 3})])
    g = c.answer_agent.generate("Điểm theo lớp và kiểm tra chất lượng", ev)
    assert g.source == "deepseek" and len(llm.calls) == 1
    prompt = llm.calls[0]["prompt"]
    assert prompt.index("[QUERY RESULT]") < prompt.index("EDA RESULT EA") < prompt.index("EDA RESULT EB")
    assert "operation=data_quality" in prompt.split("EDA RESULT EB")[0]


# ── Trạng thái: truncated / sampled / lỗi ───────────────────────────────────
def test_truncated_query_gets_caveat_when_llm_omits_it(agent_env):
    c, _ = agent_env(FakeLLM(name="deepseek", response="Danh sách đơn hàng."))
    g = c.answer_agent.generate("Liệt kê", AnswerEvidence.from_query(DF, truncated=True))
    assert g.source == "deepseek" and "chưa đầy đủ" in g.text
    c, _ = agent_env(FakeLLM(name="deepseek", response="Kết quả bị cắt bớt nên chưa đầy đủ."))
    g = c.answer_agent.generate("Liệt kê", AnswerEvidence.from_query(DF, truncated=True))
    assert g.text.count("đầy đủ") == 1                                   # LLM đã nhắc: không nối thêm


def test_sampled_eda_caveat_and_partial_failure_not_hidden(agent_env):
    c, _ = agent_env(FakeLLM(name="deepseek", response="Bảng diem có 5 dòng."))
    ev = AnswerEvidence(eda=[dq_payload(sampled=True, exact=False), failed_payload()])
    g = c.answer_agent.generate("Phân tích bảng diem", ev)
    assert g.source == "deepseek"
    assert "ước tính trên mẫu" in g.text                                  # không trình bày số ước tính như đầy đủ
    assert "Bảng diem: Bảng không có cột số phù hợp." in g.text           # lỗi operation 2 không bị che dù operation 1 ok
    c, _ = agent_env(FakeLLM(name="deepseek",
                             response="Ước tính trên mẫu: 5 dòng; phần thống kê số không áp dụng được cho bảng này."))
    g = c.answer_agent.generate("Phân tích bảng diem", ev)
    assert g.text.count("không áp dụng") == 1 and "Lưu ý" not in g.text


def test_query_failed_but_eda_ok_is_reported(agent_env):
    c, llm = agent_env(FakeLLM(name="deepseek", response="Bảng diem có 5 dòng."))
    ev = AnswerEvidence(query=QueryEvidence(status=QUERY_ERROR), eda=[dq_payload()])
    g = c.answer_agent.generate("Điểm theo lớp và kiểm tra bảng diem", ev)
    assert "Not available" in llm.calls[0]["prompt"] and "Phần truy vấn không thực hiện được" in g.text
    ev = AnswerEvidence(query=QueryEvidence(status=QUERY_CLARIFICATION), eda=[dq_payload()])
    assert "cần làm rõ" in c.answer_agent.generate("Điểm theo lớp?", ev).text


def test_missing_caveats_is_pure_and_language_aware():
    ev = AnswerEvidence(query=QueryEvidence(df=DF, truncated=True), eda=[failed_payload()])
    vi, en = missing_caveats("ok", ev, True), missing_caveats("ok", ev, False)
    assert len(vi) == 2 == len(en) and "Lưu ý" in vi[0] and en[0].startswith("Note:")
    assert missing_caveats("cut off; could not run it", ev, False) == []


# ── Fallback template: cùng lý do cho cả ba loại bằng chứng ─────────────────
EVIDENCE = {
    "query": lambda: AnswerEvidence.from_query(DF),
    "eda": lambda: AnswerEvidence(eda=[dq_payload()]),
    "query+eda": lambda: AnswerEvidence(query=AnswerEvidence.from_query(DF).query, eda=[dq_payload()]),
}


@pytest.mark.parametrize("kind", list(EVIDENCE))
def test_template_fallback_reasons_are_consistent(agent_env, kind):
    ev = EVIDENCE[kind]()
    expected = template_answer(Q, ev)
    cases = [
        (FakeLLM(name="deepseek", response="Có 9999 dòng."), True, "ungrounded_numbers"),
        (FakeLLM(name="deepseek", response="   "), True, "empty_answer"),
        (FakeLLM(name="deepseek", raises=RuntimeError("quota")), True, "llm_error: RuntimeError: quota"),
        (FakeLLM(name="deepseek", available=False), True, "llm_unavailable"),
        (FakeLLM(name="deepseek", response="Có 5 dòng."), False, "mock"),
    ]
    for llm, use_llm, reason in cases:
        c, _ = agent_env(llm)
        g = c.answer_agent.generate(Q, ev, use_llm=use_llm)
        assert (g.source, g.fallback_reason) == ("template", reason), (kind, reason)
        assert g.text == expected
        if not use_llm or not llm.available:
            assert llm.calls == []


def test_no_data_never_calls_llm(agent_env):
    c, llm = agent_env(FakeLLM(name="deepseek", response="Có 5 dòng."))
    for ev in (AnswerEvidence.from_query(pd.DataFrame()), AnswerEvidence(eda=[failed_payload()])):
        g = c.answer_agent.generate(Q, ev)
        assert (g.source, g.fallback_reason) == ("template", "no_data")
    assert llm.calls == []
    assert "không có dòng" in template_answer(Q, AnswerEvidence.from_query(pd.DataFrame()))


# ── Template: query / EDA / kết hợp ─────────────────────────────────────────
def test_template_combines_parts_in_order_and_language():
    ev = EVIDENCE["query+eda"]()
    parts = template_answer("Điểm theo lớp và kiểm tra chất lượng", ev).split("\n\n")
    assert parts[0].startswith("Truy vấn trả về 2 dòng") and "1 dòng trùng lặp" in parts[1]
    en = template_answer("Scores by class and data quality", ev).split("\n\n")
    assert en[0].startswith("The query returned 2 rows") and "1 duplicate rows" in en[1]
    assert template_answer(Q, EVIDENCE["eda"]()) == describe_payload(dq_payload(), True)


def test_language_helper_is_unified():
    assert is_vietnamese("Tổng doanh thu?") and is_vietnamese("tong quan du lieu")
    assert not is_vietnamese("Revenue by region")
    # ASCII tiếng Việt: câu trả lời mẫu của query cũng dùng tiếng Việt
    assert template_answer("tong quan du lieu", AnswerEvidence.from_query(DF)).startswith("Truy vấn trả về")


def test_tool_inventory_after_phase7(make_container):
    names = make_container().tools.names()
    assert "answer_template" in names and "template_answer" not in names and "eda_answer" not in names
    import backend.tools as t
    for gone in ("EdaAnswerTool", "TemplateAnswerTool", "summarize_payload", "is_vi_question"):
        assert not hasattr(t, gone)


# ── Qua API / Orchestrator ──────────────────────────────────────────────────
@pytest.fixture
def env(make_container):
    def _make(llm=None, **kw):
        llm = llm or FakeLLM(name="deepseek", available=False)
        c = make_container(deepseek=llm, **kw)
        app = create_app()
        app.dependency_overrides[get_container] = lambda: c
        client = TestClient(app)
        r = client.post("/api/datasets", files={"file": ("diem.csv", io.BytesIO(CSV))})
        assert r.status_code == 201
        return client, c, r.json()["dataset_id"]
    return _make


def ask(client, ds, question, **kw):
    r = client.post("/api/query", json={"question": question, "dataset_id": ds, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def plan(*steps):
    return json.dumps({"steps": list(steps), "chart": {"requested": False, "type": None, "goal": None},
                       "clarification": None})


def test_api_combined_turn_has_single_answer_call_after_all_results(env):
    llm = FakeLLM(name="deepseek", planner_response=plan({"op": "query"}, {"op": "data_quality", "table": "diem"}))
    client, c, ds = env(llm)
    llm.script("SELECT lop, COUNT(*) AS n FROM diem GROUP BY lop ORDER BY lop",
               "Lớp A có 3 dòng, lớp B có 2 dòng; bảng có 1 dòng trùng lặp.")
    llm.available = True
    d = ask(client, ds, "Số dòng theo lớp và kiểm tra trùng lặp bảng diem")
    assert d["status"] == "ok" and len(llm.calls) == 2                      # SQL + đúng MỘT lời gọi trả lời
    prompt = llm.calls[1]["prompt"]
    assert "[QUERY RESULT]" in prompt and "EDA RESULT EA" in prompt and "duplicate_rows" in prompt
    assert d["answer"] == {"text": "Lớp A có 3 dòng, lớp B có 2 dòng; bảng có 1 dòng trùng lặp.",
                           "source": "deepseek", "fallback_reason": None}
    # bảng query, SQL/bằng chứng, bảng EDA là các phần riêng; `eda[].text` vẫn là mô tả xác định của operation
    assert d["table"]["rows"] == [["A", 3], ["B", 2]] and d["evidence"]["sql"].startswith("SELECT")
    assert d["eda"][0]["table_data"] and "1 dòng trùng lặp" in d["eda"][0]["text"]
    assert "SELECT" not in prompt                                            # SQL không được gửi lại cho LLM trả lời


def test_api_eda_only_uses_llm_when_available_and_template_otherwise(env):
    llm = FakeLLM(name="deepseek", response="Bảng diem có 5 dòng và 1 dòng trùng lặp.",
                  planner_response=plan({"op": "data_quality", "table": "diem"}))
    client, c, ds = env(llm)
    question = "Kiểm tra giá trị thiếu và trùng lặp của bảng diem"
    d = ask(client, ds, question)
    assert d["answer"]["source"] == "deepseek" and d["answer"]["fallback_reason"] is None and d["table"] is None
    llm.available = False
    d = ask(client, ds, question)
    assert d["answer"]["source"] == "template" and d["answer"]["fallback_reason"] == "llm_unavailable"
    assert "1 dòng trùng lặp" in d["answer"]["text"] and d["eda"][0]["status"] == "ok"
    llm.available = True
    d = ask(client, ds, question, use_mock_sql=True)
    assert d["answer"]["fallback_reason"] == "mock"


def test_api_ungrounded_eda_answer_falls_back_with_payload_intact(env):
    llm = FakeLLM(name="deepseek", response="Có 40% dòng bị thiếu.",
                  planner_response=plan({"op": "data_quality", "table": "diem"}))
    client, _, ds = env(llm)
    d = ask(client, ds, "Kiểm tra giá trị thiếu bảng diem")
    assert d["answer"]["source"] == "template" and d["answer"]["fallback_reason"] == "ungrounded_numbers"
    assert "40" not in d["answer"]["text"] and d["eda"][0]["summary"]["duplicate_rows"] == 1


def test_api_partial_eda_failure_is_visible_in_answer(env):
    steps = ({"op": "data_quality", "table": "diem"}, {"op": "numeric_summary", "table": "diem", "columns": ["ten"]})
    llm = FakeLLM(name="deepseek", response="Bảng diem có 5 dòng.", planner_response=plan(*steps))
    client, _, ds = env(llm)
    d = ask(client, ds, "Kiểm tra chất lượng và thống kê mô tả cột ten của bảng diem")
    statuses = [e["status"] for e in d["eda"]]
    assert statuses[0] == "ok" and statuses[1] != "ok" and d["status"] == "ok"
    assert d["answer"]["source"] == "deepseek" and d["eda"][1]["error"] in d["answer"]["text"]


def test_api_query_failure_keeps_eda_and_says_so(env):
    llm = FakeLLM(name="deepseek", planner_response=plan({"op": "query"}, {"op": "data_quality", "table": "diem"}))
    llm.script("SELECT cot_khong_co FROM diem", "Bảng diem có 5 dòng.")
    client, _, ds = env(llm)
    d = ask(client, ds, "Điểm theo lớp và kiểm tra chất lượng bảng diem")
    assert d["status"] == "sql_error" and d["eda"][0]["status"] == "ok" and d["table"] is None
    assert d["answer"]["source"] == "deepseek" and "Not available" in llm.calls[1]["prompt"]
    assert "Phần truy vấn không thực hiện được" in d["answer"]["text"]     # LLM không nhắc -> được nối thêm
    llm.script("SELECT cot_khong_co FROM diem", RuntimeError("boom"))     # LLM trả lời lỗi -> template nêu cả hai phần
    d = ask(client, ds, "Điểm theo lớp và kiểm tra chất lượng bảng diem")
    assert d["answer"]["source"] == "template" and "1 dòng trùng lặp" in d["answer"]["text"]
    assert "Phần truy vấn không thực hiện được" in d["answer"]["text"]


def test_api_empty_result_and_sql_error_without_eda(env):
    client, _, ds = env(FakeLLM(name="deepseek", response="SELECT * FROM diem WHERE 1 = 0"))
    d = ask(client, ds, "Cho xem điểm")
    assert d["status"] == "empty_result" and "không có dòng" in d["answer"]["text"]
    assert d["answer"]["fallback_reason"] == "no_data"
    client, _, ds = env(FakeLLM(name="deepseek", response="SELECT cot_khong_co FROM diem"))
    d = ask(client, ds, "Cho xem điểm")
    assert d["status"] == "sql_error" and d["answer"] is None


def test_api_answer_agent_crash_does_not_lose_other_parts(env, monkeypatch):
    llm = FakeLLM(name="deepseek", response="SELECT lop FROM diem")
    client, c, ds = env(llm)

    def boom(*a, **k):
        raise RuntimeError("agent down")

    monkeypatch.setattr(c.answer_agent, "generate", boom)
    d = ask(client, ds, "Cho xem lớp", show_sql=True)
    assert d["status"] == "ok" and d["table"] and d["evidence"]["sql"] == "SELECT lop FROM diem"
    assert d["answer"]["source"] == "template" and d["answer"]["fallback_reason"].startswith("error: RuntimeError")

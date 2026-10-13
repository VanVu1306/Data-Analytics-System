"""Giai đoạn 3: nhận diện yêu cầu vẽ/SQL, câu trả lời mẫu, kiểm tra bám số liệu, câu trả lời khi kết quả bị cắt."""
import pandas as pd
import pytest

from backend.tools import (
    answer_is_grounded,
    detect_chart_request,
    detect_sql_request,
    resolve_chart_plan,
    template_answer,
)
from tests.conftest import FakeLLM


# ── Nhận diện yêu cầu biểu đồ ────────────────────────────────────────────────
@pytest.mark.parametrize(
    "q, expected_type",
    [
        ("Vẽ biểu đồ doanh thu theo tháng", None),
        ("plot revenue by region", None),
        ("Show revenue by region as a chart", None),
        ("Visualize the monthly revenue", None),
        ("biểu đồ tròn tỷ lệ doanh thu theo vùng", "pie chart"),
        ("draw a pie chart of revenue by region", "pie chart"),
        ("vẽ biểu đồ cột doanh thu theo tháng", "bar chart"),
        ("line chart of revenue over time", "line chart"),
        ("vẽ đồ thị đường doanh thu", "line chart"),
        ("scatter plot of price vs quantity", "scatter plot"),
        ("vẽ biểu đồ phân tán giữa giá và số lượng", "scatter plot"),
    ],
)
def test_detects_chart_requests_and_types(q, expected_type):
    plan = detect_chart_request(q)
    assert plan is not None and plan.requested and plan.origin == "prompt" and plan.chart_type == expected_type


@pytest.mark.parametrize(
    "q",
    [
        "Doanh thu theo tháng năm 2024",
        "Revenue by region",
        "Cột nào có nhiều giá trị rỗng nhất?",       # 'cột' = column, không phải biểu đồ cột
        "Đường dẫn của tệp là gì?",
        "How many rows are in the plotted_points table?",
        "Không cần vẽ biểu đồ, chỉ cho tôi số liệu",
        "no chart please, just the numbers",
        "Please don't plot anything",
    ],
)
def test_no_chart_when_not_requested(q):
    assert detect_chart_request(q) is None
    assert resolve_chart_plan(q).requested is False


def test_resolve_chart_plan_combines_option_and_prompt():
    assert resolve_chart_plan("Revenue by region").requested is False  # mặc định: không vẽ
    assert resolve_chart_plan("Plot revenue", "none").requested is False  # 'Không vẽ' thắng
    p = resolve_chart_plan("Revenue by region", "auto")
    assert p.requested and p.chart_type is None and p.origin == "option"
    p = resolve_chart_plan("biểu đồ tròn doanh thu", "auto")
    assert p.requested and p.chart_type == "pie chart"  # loại nêu trong câu hỏi được ưu tiên khi chọn Tự chọn
    p = resolve_chart_plan("Revenue by region", "bar chart")
    assert p.requested and p.chart_type == "bar chart" and p.origin == "option"
    assert resolve_chart_plan("Plot revenue", "detect").origin == "prompt"


def test_detect_sql_request():
    assert detect_sql_request("Show me the SQL for revenue by region")
    assert detect_sql_request("Cho xem câu lệnh truy vấn đã dùng")
    assert not detect_sql_request("Revenue by region")
    assert not detect_sql_request("Doanh thu theo vùng")


# ── Câu trả lời mẫu ──────────────────────────────────────────────────────────
def test_template_answers():
    one = pd.DataFrame({"total": [1234567.5]})
    assert "total = 1,234,567.5" in template_answer("Tổng doanh thu?", one)
    assert template_answer("Tổng doanh thu?", one).startswith("Kết quả:")
    assert template_answer("Total revenue?", one).startswith("The result is")

    many = pd.DataFrame({"region": ["A", "B", "C"], "rev": [10, 20, 30]})
    t = template_answer("Revenue by region", many)
    assert "3 rows × 2 columns" in t and "region = A" in t and "rev = 10" in t

    assert "không có dòng" in template_answer("Doanh thu theo vùng", pd.DataFrame())
    assert "no rows" in template_answer("revenue", None)
    assert "cắt bớt" in template_answer("Doanh thu theo vùng", many, truncated=True)


def test_template_answer_handles_nulls_and_wide_rows():
    df = pd.DataFrame({f"c{i}": [i] for i in range(10)})
    df["c0"] = [None]
    t = template_answer("x", df)
    assert "c0 = NULL" in t and t.endswith("; ....") or "; ..." in t


# ── Kiểm tra bám số liệu ─────────────────────────────────────────────────────
DF = pd.DataFrame({"region": ["Bắc", "Nam"], "revenue": [1234567.891, 98000.0], "month": ["2024-03", "2024-04"]})


@pytest.mark.parametrize(
    "answer",
    [
        "Vùng Bắc có doanh thu 1234567.89.",
        "Vùng Bắc có doanh thu 1,234,567.89 và vùng Nam là 98,000.",
        "Doanh thu của Nam là 98.000.",             # dấu chấm hàng nghìn kiểu VN
        "Có 2 vùng, doanh thu cao nhất ở tháng 2024-03.",
        "1. Bắc: 1234567.89\n2. Nam: 98000",           # số thứ tự danh sách không bị coi là số liệu
        "Không có thông tin nào về tăng trưởng.",
    ],
)
def test_grounded_answers_accepted(answer):
    assert answer_is_grounded(answer, "Doanh thu theo vùng?", DF, max_rows=20)


@pytest.mark.parametrize(
    "answer",
    [
        "Tổng doanh thu là 1,332,567.89.",   # số tự tính
        "Doanh thu Bắc tăng 25% so với năm trước.",
        "Doanh thu khoảng 1.2 triệu.",       # làm tròn/đổi đơn vị
        "Vùng Bắc có 7 cửa hàng.",
    ],
)
def test_ungrounded_answers_rejected(answer):
    assert not answer_is_grounded(answer, "Doanh thu theo vùng?", DF, max_rows=20)


def test_numbers_beyond_sent_rows_are_not_grounded():
    df = pd.DataFrame({"n": list(range(1000, 1050))})
    assert answer_is_grounded("Giá trị đầu là 1000.", "q", df, max_rows=5)
    assert not answer_is_grounded("Giá trị cuối là 1049.", "q", df, max_rows=5)  # LLM không được thấy dòng này
    assert answer_is_grounded("Có 50 dòng.", "q", df, max_rows=5)  # tổng số dòng được phép


def test_numbers_from_question_are_allowed():
    assert answer_is_grounded("Top 5 sản phẩm bán chạy nhất:", "Top 5 sản phẩm", pd.DataFrame({"a": ["x"]}), 20)


# ── Kết quả bị cắt ───────────────────────────────────────────────────────────
def test_truncated_result_is_flagged_in_answer(make_container):
    ds = FakeLLM(name="deepseek", response="")
    c = make_container(deepseek=ds, query_max_rows=50)
    responses = iter(["SELECT order_id FROM orders", "Danh sách mã đơn hàng."])
    ds.complete = lambda prompt, **kw: next(responses)
    from backend.pipeline import PipelineRequest

    r = c.orchestrator.run(PipelineRequest(question="Liệt kê đơn hàng"))
    assert r.truncated and "chưa đầy đủ" in r.answer and r.answer_source == "deepseek"

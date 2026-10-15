"""
backend/config.py
-----------------
Nơi DUY NHẤT đọc cấu hình (biến môi trường / file .env).
Mọi module khác nhận `Settings` qua tham số, không tự gọi os.getenv.

Giá trị mặc định giữ nguyên như bản gốc (app.py / modules/*).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    """Loader .env tối giản (KEY=VALUE mỗi dòng). Không ghi đè biến đã có sẵn."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env_str(name: str, default: str) -> str:
    return os.getenv(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    # ── Database ───────────────────────────────────────────────────────────
    db_path: str

    # ── Cloud LLM (Stage 1: SQL; fallback cho Stage 3) ─────────────────────
    llm_provider: str
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    openai_api_key: str
    openai_model: str
    sql_temperature: float
    sql_max_tokens: int
    viz_cloud_temperature: float
    viz_cloud_max_tokens: int

    # ── Ollama (Stage 3: viz code, chạy local) ─────────────────────────────
    ollama_base_url: str
    ollama_model: str
    ollama_timeout: int
    ollama_max_tokens: int
    ollama_temperature: float
    ollama_top_p: float

    # ── Dataset upload / truy vấn ──────────────────────────────────────────
    dataset_dir: str            # nơi backend lưu mỗi dataset upload (data.db + meta.json)
    max_upload_mb: int
    import_timeout_seconds: float
    max_rows_per_table: int
    query_timeout_seconds: float
    query_max_rows: int

    # ── Visualization ──────────────────────────────────────────────────────
    viz_sample_rows: int  # số dòng mẫu đưa vào prompt Stage 3
    chart_dpi: int        # DPI khi render figure -> PNG

    # ── Trả lời text (giai đoạn 3) ─────────────────────────────────────────
    answer_max_rows: int          # số dòng kết quả tối đa gửi cho LLM để viết câu trả lời
    answer_max_tokens: int
    answer_temperature: float

    # ── EDA (giai đoạn 4) ──────────────────────────────────────────────────
    eda_max_columns: int          # số cột tối đa mỗi operation
    eda_top_k: int                # số giá trị phổ biến tối đa mỗi cột phân loại
    eda_sample_rows: int          # số dòng tối đa đưa sang pandas (quartile/tương quan); vượt -> lấy mẫu
    eda_timeout_seconds: float    # ngân sách thời gian cho một operation
    eda_corr_max_columns: int     # số cột số tối đa cho ma trận tương quan
    eda_max_tables: int           # số bảng tối đa khi một câu hỏi EDA áp dụng cho cả dataset

    # ── Planner (giai đoạn 6) ──────────────────────────────────────────────
    planner_mode: str             # "llm" (mặc định): LLM lập kế hoạch thống nhất | "rules": chỉ dùng quy tắc (không gọi LLM planner)
    planner_max_tokens: int       # số token tối đa cho phản hồi kế hoạch JSON

    # ── Biểu đồ (giai đoạn 5) ──────────────────────────────────────────────
    viz_mode: str                 # "spec" (mặc định): bộ vẽ xác định, allowlist loại chart | "code": LLM sinh code matplotlib (cũ, exec)
    chart_max_categories: int     # số nhóm tối đa trên bar/count plot (vượt thì lấy top)
    chart_pie_max_slices: int     # số lát tối đa của pie (phần còn lại gộp "Khác")
    chart_max_points: int         # số điểm tối đa của line/scatter (vượt thì lấy mẫu)
    chart_sample_rows: int        # số dòng tối đa lấy từ DB để vẽ phân phối (KDE/violin/box)
    chart_hist_bins: int          # số bin histogram khi gộp bằng SQL

    @property
    def has_deepseek_key(self) -> bool:
        return bool(self.deepseek_api_key)

    @property
    def has_cloud_llm_key(self) -> bool:
        if self.llm_provider == "openai":
            return bool(self.openai_api_key)
        return bool(self.deepseek_api_key)


def load_settings() -> Settings:
    """Đọc .env (nếu có) rồi dựng Settings từ biến môi trường."""
    _load_env_file(PROJECT_ROOT / ".env")
    return Settings(
        db_path=_env_str("NL2VIZ_DB_PATH", str(PROJECT_ROOT / "demo_db" / "sales_demo.db")),
        llm_provider=_env_str("LLM_PROVIDER", "deepseek").strip().lower(),
        deepseek_api_key=_env_str("DEEPSEEK_API_KEY", ""),
        deepseek_base_url=_env_str("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        deepseek_model=_env_str("DEEPSEEK_MODEL", "deepseek-chat"),
        openai_api_key=_env_str("OPENAI_API_KEY", ""),
        openai_model=_env_str("OPENAI_MODEL", "gpt-5.6"),
        sql_temperature=_env_float("SQL_TEMPERATURE", 0.0),
        sql_max_tokens=_env_int("SQL_MAX_TOKENS", 512),
        viz_cloud_temperature=_env_float("VIZ_CLOUD_TEMPERATURE", 0.1),
        viz_cloud_max_tokens=_env_int("VIZ_CLOUD_MAX_TOKENS", 800),
        ollama_base_url=_env_str("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        ollama_model=_env_str("OLLAMA_MODEL", "llama3.2"),
        ollama_timeout=_env_int("OLLAMA_TIMEOUT", 120),
        ollama_max_tokens=_env_int("OLLAMA_MAX_TOKENS", 512),
        ollama_temperature=_env_float("OLLAMA_TEMPERATURE", 0.1),
        ollama_top_p=_env_float("OLLAMA_TOP_P", 0.9),
        dataset_dir=_env_str("NL2VIZ_DATASET_DIR", str(PROJECT_ROOT / "data" / "datasets")),
        max_upload_mb=_env_int("NL2VIZ_MAX_UPLOAD_MB", 50),
        import_timeout_seconds=_env_float("NL2VIZ_IMPORT_TIMEOUT", 60.0),
        max_rows_per_table=_env_int("NL2VIZ_MAX_ROWS_PER_TABLE", 200_000),
        query_timeout_seconds=_env_float("NL2VIZ_QUERY_TIMEOUT", 10.0),
        query_max_rows=_env_int("NL2VIZ_QUERY_MAX_ROWS", 1000),
        viz_sample_rows=_env_int("VIZ_SAMPLE_ROWS", 3),
        chart_dpi=_env_int("CHART_DPI", 100),
        answer_max_rows=_env_int("ANSWER_MAX_ROWS", 20),
        answer_max_tokens=_env_int("ANSWER_MAX_TOKENS", 300),
        answer_temperature=_env_float("ANSWER_TEMPERATURE", 0.0),
        eda_max_columns=_env_int("EDA_MAX_COLUMNS", 50),
        eda_top_k=_env_int("EDA_TOP_K", 10),
        eda_sample_rows=_env_int("EDA_SAMPLE_ROWS", 50_000),
        eda_timeout_seconds=_env_float("EDA_TIMEOUT", 20.0),
        eda_corr_max_columns=_env_int("EDA_CORR_MAX_COLUMNS", 15),
        eda_max_tables=_env_int("EDA_MAX_TABLES", 10),
        planner_mode="rules" if _env_str("PLANNER_MODE", "llm").strip().lower() == "rules" else "llm",
        planner_max_tokens=_env_int("PLANNER_MAX_TOKENS", 700),
        viz_mode=(_env_str("VIZ_MODE", "spec").strip().lower() if _env_str("VIZ_MODE", "spec").strip().lower() in ("spec", "code") else "spec"),
        chart_max_categories=_env_int("CHART_MAX_CATEGORIES", 30),
        chart_pie_max_slices=_env_int("CHART_PIE_MAX_SLICES", 8),
        chart_max_points=_env_int("CHART_MAX_POINTS", 2000),
        chart_sample_rows=_env_int("CHART_SAMPLE_ROWS", 20_000),
        chart_hist_bins=_env_int("CHART_HIST_BINS", 30),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()

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

    # ── DeepSeek (Stage 1: SQL; fallback cho Stage 3) ──────────────────────
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
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

    # ── Visualization ──────────────────────────────────────────────────────
    viz_sample_rows: int  # số dòng mẫu đưa vào prompt Stage 3
    chart_dpi: int        # DPI khi render figure -> PNG

    @property
    def has_deepseek_key(self) -> bool:
        return bool(self.deepseek_api_key)


def load_settings() -> Settings:
    """Đọc .env (nếu có) rồi dựng Settings từ biến môi trường."""
    _load_env_file(PROJECT_ROOT / ".env")
    return Settings(
        db_path=_env_str("NL2VIZ_DB_PATH", str(PROJECT_ROOT / "demo_db" / "sales_demo.db")),
        deepseek_api_key=_env_str("DEEPSEEK_API_KEY", ""),
        deepseek_base_url=_env_str("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        deepseek_model=_env_str("DEEPSEEK_MODEL", "deepseek-chat"),
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
        viz_sample_rows=_env_int("VIZ_SAMPLE_ROWS", 3),
        chart_dpi=_env_int("CHART_DPI", 100),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()

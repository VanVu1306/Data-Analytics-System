"""Prompt templates (tách khỏi logic agents)."""
from backend.prompts.sql_prompts import (
    CLARIFY_PREFIX,
    DATASET_SQL_SYSTEM_PROMPT,
    FEW_SHOT_EXAMPLES,
    SQL_SYSTEM_PROMPT,
    build_dail_prompt,
    build_dataset_prompt,
)
from backend.prompts.answer_prompts import ANSWER_SYSTEM_PROMPT, build_answer_prompt
from backend.prompts.viz_prompts import VIZ_CLOUD_SYSTEM_PROMPT, build_viz_prompt

__all__ = [
    "ANSWER_SYSTEM_PROMPT",
    "build_answer_prompt",
    "CLARIFY_PREFIX",
    "DATASET_SQL_SYSTEM_PROMPT",
    "build_dataset_prompt",
    "FEW_SHOT_EXAMPLES",
    "SQL_SYSTEM_PROMPT",
    "build_dail_prompt",
    "VIZ_CLOUD_SYSTEM_PROMPT",
    "build_viz_prompt",
]

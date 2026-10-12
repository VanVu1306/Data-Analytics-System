"""Prompt templates (tách khỏi logic agents)."""
from backend.prompts.sql_prompts import FEW_SHOT_EXAMPLES, SQL_SYSTEM_PROMPT, build_dail_prompt
from backend.prompts.viz_prompts import VIZ_CLOUD_SYSTEM_PROMPT, build_viz_prompt

__all__ = [
    "FEW_SHOT_EXAMPLES",
    "SQL_SYSTEM_PROMPT",
    "build_dail_prompt",
    "VIZ_CLOUD_SYSTEM_PROMPT",
    "build_viz_prompt",
]

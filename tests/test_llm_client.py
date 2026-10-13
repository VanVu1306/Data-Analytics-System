from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from backend.llm.deepseek_client import DeepSeekClient
from backend.llm.factory import create_cloud_llm
from backend.llm.gpt_client import GPTClient


class UnsupportedMaxTokensError(Exception):
    body = {
        "error": {
            "message": "Use max_completion_tokens instead.",
            "param": "max_tokens",
            "code": "unsupported_parameter",
        }
    }


def test_retries_with_max_completion_tokens_when_model_rejects_max_tokens(settings):
    client = DeepSeekClient(
        replace(settings, deepseek_api_key="test-key", deepseek_model="gpt-5.6")
    )
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="SELECT 1"))]
    )

    with patch("openai.OpenAI") as openai, patch(
        "openai.BadRequestError", UnsupportedMaxTokensError
    ):
        create = openai.return_value.chat.completions.create
        create.side_effect = [UnsupportedMaxTokensError(), completion]

        result = client.complete("prompt", max_tokens=512)

    assert result == "SELECT 1"
    first_kwargs = create.call_args_list[0].kwargs
    second_kwargs = create.call_args_list[1].kwargs
    assert first_kwargs["max_tokens"] == 512
    assert "max_completion_tokens" not in first_kwargs
    assert second_kwargs["max_completion_tokens"] == 512
    assert "max_tokens" not in second_kwargs


def test_does_not_retry_unrelated_api_errors(settings):
    client = DeepSeekClient(replace(settings, deepseek_api_key="test-key"))
    error = RuntimeError("connection error")

    with patch("openai.OpenAI") as openai:
        create = openai.return_value.chat.completions.create
        create.side_effect = error

        try:
            client.complete("prompt", max_tokens=512)
        except RuntimeError as raised:
            assert raised is error
        else:
            raise AssertionError("Expected the API error to be raised")

    create.assert_called_once()


def test_gpt_client_uses_completion_token_parameter(settings):
    client = GPTClient(
        replace(settings, openai_api_key="test-key", openai_model="gpt-5.6")
    )
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="SELECT 1"))]
    )

    with patch("openai.OpenAI") as openai:
        create = openai.return_value.chat.completions.create
        create.return_value = completion

        result = client.complete(
            "prompt", system="system prompt", temperature=0.0, max_tokens=512
        )

    assert result == "SELECT 1"
    kwargs = create.call_args.kwargs
    assert kwargs["model"] == "gpt-5.6"
    assert kwargs["max_completion_tokens"] == 512
    assert "max_tokens" not in kwargs
    assert "temperature" not in kwargs


def test_cloud_llm_factory_selects_configured_provider(settings):
    openai_settings = replace(
        settings, llm_provider="openai", openai_api_key="test-key"
    )
    deepseek_settings = replace(settings, llm_provider="deepseek")

    assert isinstance(create_cloud_llm(openai_settings), GPTClient)
    assert isinstance(create_cloud_llm(deepseek_settings), DeepSeekClient)


def test_cloud_llm_factory_rejects_unknown_provider(settings):
    import pytest

    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        create_cloud_llm(replace(settings, llm_provider="unknown"))

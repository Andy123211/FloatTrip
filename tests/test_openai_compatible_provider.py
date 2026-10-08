from __future__ import annotations

import pytest
from pydantic import BaseModel


class _Output(BaseModel):
    ok: bool


def test_factory_resolves_openai_compatible_provider(monkeypatch):
    from app.llm.factory import resolve_llm_provider

    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    assert resolve_llm_provider() == "openai_compatible"


def test_openai_compatible_chat_uses_local_configuration(monkeypatch):
    import langchain_openai
    from app.llm.openai_compatible import build_chat_openai_compatible

    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", FakeChatOpenAI)
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "local-test-key")
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "https://example.invalid/v1/")
    monkeypatch.setenv("OPENAI_COMPATIBLE_MODEL", "provider/model")

    result = build_chat_openai_compatible(temperature=0.25)

    assert isinstance(result, FakeChatOpenAI)
    assert captured == {
        "model": "provider/model",
        "api_key": "local-test-key",
        "base_url": "https://example.invalid/v1",
        "temperature": 0.25,
        "timeout": 60.0,
        "max_retries": 1,
    }


def test_openai_compatible_structured_output_uses_function_calling(monkeypatch):
    import langchain_openai
    from app.llm.openai_compatible import build_structured_openai_compatible

    class FakeChatOpenAI:
        def __init__(self, **_kwargs):
            pass

        def with_structured_output(self, schema, **kwargs):
            return schema, kwargs

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", FakeChatOpenAI)
    monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "local-test-key")
    monkeypatch.setenv("OPENAI_COMPATIBLE_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("OPENAI_COMPATIBLE_MODEL", "provider/model")

    result = build_structured_openai_compatible(_Output)

    assert result == (_Output, {"method": "function_calling"})


def test_openai_compatible_provider_requires_local_credentials(monkeypatch):
    from app.llm.factory import build_chat_llm

    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.delenv("OPENAI_COMPATIBLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_COMPATIBLE_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_COMPATIBLE_MODEL", raising=False)

    with pytest.raises(RuntimeError, match="OPENAI_COMPATIBLE_API_KEY"):
        build_chat_llm()

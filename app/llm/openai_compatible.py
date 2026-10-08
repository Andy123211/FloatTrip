"""OpenAI Chat Completions-compatible provider adapter."""

from __future__ import annotations

import os
from typing import Any, TypeVar

from pydantic import BaseModel

from app.core.env import load_local_env

SchemaT = TypeVar("SchemaT", bound=BaseModel)


def _build_client(*, model: str | None, temperature: float) -> Any:
    load_local_env()
    api_key = os.getenv("OPENAI_COMPATIBLE_API_KEY", "").strip()
    base_url = os.getenv("OPENAI_COMPATIBLE_BASE_URL", "").strip().rstrip("/")
    model_name = (model or os.getenv("OPENAI_COMPATIBLE_MODEL", "")).strip()
    missing = [
        name
        for name, value in (
            ("OPENAI_COMPATIBLE_API_KEY", api_key),
            ("OPENAI_COMPATIBLE_BASE_URL", base_url),
            ("OPENAI_COMPATIBLE_MODEL", model_name),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "LLM_PROVIDER=openai_compatible requires " + ", ".join(missing)
            + "; set them in .env.local and keep the API key private."
        )

    try:
        from langchain_openai import ChatOpenAI
    except ModuleNotFoundError as exc:
        raise RuntimeError("缺少 langchain-openai，请先安装项目依赖。") from exc

    return ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url,
        temperature=temperature,
        timeout=float(os.getenv("OPENAI_COMPATIBLE_TIMEOUT_SECONDS", "60")),
        max_retries=1,
    )


def build_chat_openai_compatible(
    *, model: str | None = None, temperature: float = 0
) -> Any:
    return _build_client(model=model, temperature=temperature)


def build_structured_openai_compatible(
    schema: type[SchemaT], *, model: str | None = None, temperature: float = 0
) -> Any:
    client = _build_client(model=model, temperature=temperature)
    return client.with_structured_output(schema, method="function_calling")

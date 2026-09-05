from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel


class _StructuredResult(BaseModel):
    city: str


class _FakeChatModel:
    def __init__(self, response=None):
        self.response = response
        self.calls = []
        self.structured_calls = []
        self.structured_model = None

    def with_structured_output(self, schema, **kwargs):
        self.structured_calls.append((schema, kwargs))
        return self.structured_model

    def invoke(self, messages, config=None, **kwargs):
        self.calls.append((messages, config, kwargs))
        return self.response

    async def ainvoke(self, messages, config=None, **kwargs):
        self.calls.append((messages, config, kwargs))
        return self.response


def test_deepseek_thinking_configuration_and_reasoning_replay(monkeypatch):
    from app.llm import deepseek

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(deepseek, "resolve_deepseek_proxy", lambda: None)
    llm = deepseek.build_chat_deepseek(
        model="deepseek-v4-pro", thinking=True, reasoning_effort="high"
    )
    assert llm.extra_body == {"thinking": {"type": "enabled"}}
    assert llm.reasoning_effort == "high"
    assert llm.temperature is None

    messages = [
        HumanMessage("查天气"),
        AIMessage(
            content="",
            tool_calls=[{
                "name": "weather", "args": {"city": "南京"},
                "id": "call-1", "type": "tool_call",
            }],
            additional_kwargs={"reasoning_content": "需要先查询天气"},
        ),
        ToolMessage(content="晴", tool_call_id="call-1"),
    ]
    payload = llm._get_request_payload(messages)
    assistant = next(item for item in payload["messages"] if item["role"] == "assistant")
    assert assistant["reasoning_content"] == "需要先查询天气"


def test_non_agent_deepseek_calls_remain_non_thinking(monkeypatch):
    from app.llm import deepseek

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setattr(deepseek, "resolve_deepseek_proxy", lambda: None)
    llm = deepseek.build_chat_deepseek(temperature=0.25)
    assert llm.extra_body == {"thinking": {"type": "disabled"}}
    assert llm.reasoning_effort is None
    assert llm.temperature == 0.25


def test_thinking_structured_output_uses_two_stages_without_forced_reasoning_tool(monkeypatch):
    from app.llm import deepseek

    reasoning = _FakeChatModel(AIMessage(content='{"city":"南京"}'))
    formatter = _FakeChatModel({"city": "南京"})
    formatter_base = _FakeChatModel()
    formatter_base.structured_model = formatter
    build_calls = []

    def fake_build_chat(**kwargs):
        build_calls.append(kwargs)
        return reasoning if kwargs["thinking"] else formatter_base

    monkeypatch.setattr(deepseek, "build_chat_deepseek", fake_build_chat)
    llm = deepseek.build_structured_deepseek(
        _StructuredResult,
        model="deepseek-v4-pro",
        thinking=True,
        reasoning_effort="max",
    )

    result = llm.invoke([("system", "规划城市"), ("human", "南京")])

    assert result == _StructuredResult(city="南京")
    assert build_calls == [
        {
            "model": "deepseek-v4-pro",
            "thinking": True,
            "reasoning_effort": "max",
        },
        {
            "model": "deepseek-v4-pro",
            "temperature": 0,
            "thinking": False,
        },
    ]
    assert reasoning.structured_calls == []
    strict_schema, strict_options = formatter_base.structured_calls[0]
    assert strict_schema["function"]["name"] == "_StructuredResult"
    assert strict_schema["function"]["parameters"]["required"] == ["city"]
    assert strict_options == {"method": "function_calling", "strict": True}
    assert "目标 JSON Schema" in reasoning.calls[0][0][-1][1]
    assert '{"city":"南京"}' in formatter.calls[0][0][-1][1]


def test_thinking_structured_output_async_runs_reasoning_then_formatter(monkeypatch):
    from app.llm import deepseek

    reasoning = _FakeChatModel(AIMessage(content="南京语义草稿"))
    formatter = _FakeChatModel({"city": "南京"})
    formatter_base = _FakeChatModel()
    formatter_base.structured_model = formatter

    monkeypatch.setattr(
        deepseek,
        "build_chat_deepseek",
        lambda **kwargs: reasoning if kwargs["thinking"] else formatter_base,
    )
    llm = deepseek.build_structured_deepseek(
        _StructuredResult, thinking=True, reasoning_effort="high"
    )

    result = asyncio.run(llm.ainvoke([("human", "规划南京")]))

    assert result == _StructuredResult(city="南京")
    assert len(reasoning.calls) == 1
    assert len(formatter.calls) == 1
    assert "南京语义草稿" in formatter.calls[0][0][-1][1]


def test_empty_thinking_draft_does_not_reach_strict_formatter(monkeypatch):
    import pytest
    from app.llm import deepseek

    reasoning = _FakeChatModel(AIMessage(content="  "))
    formatter = _FakeChatModel(_StructuredResult(city="南京"))
    formatter_base = _FakeChatModel()
    formatter_base.structured_model = formatter
    monkeypatch.setattr(
        deepseek,
        "build_chat_deepseek",
        lambda **kwargs: reasoning if kwargs["thinking"] else formatter_base,
    )
    llm = deepseek.build_structured_deepseek(_StructuredResult, thinking=True)

    with pytest.raises(RuntimeError, match="未产出可格式化"):
        llm.invoke([("human", "规划南京")])
    assert formatter.calls == []


def test_deepseek_strict_schema_requires_every_nested_property():
    from app.chat.models import DialogueDecision
    from app.llm.deepseek import _strict_deepseek_tool_schema

    tool = _strict_deepseek_tool_schema(DialogueDecision)

    def assert_required(node):
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                assert set(node["required"]) == set(properties)
                assert node["additionalProperties"] is False
            for value in node.values():
                assert_required(value)
        elif isinstance(node, list):
            for value in node:
                assert_required(value)

    assert_required(tool["function"]["parameters"])


def test_main_and_planning_factories_force_deepseek_thinking(monkeypatch):
    import app.llm.factory as factory
    import app.planning.nodes as planning_nodes

    main_call = {}

    def fake_chat(**kwargs):
        main_call.update(kwargs)
        return object()

    monkeypatch.setattr(factory, "build_chat_llm", fake_chat)
    # react_graph imports the factory function lazily, so this captures the
    # production defaults without constructing a real network client.
    import app.chat.react_graph as react_graph
    monkeypatch.setenv("MAIN_AGENT_MODEL", "deepseek-v4-pro")

    class FakeAgent:
        pass

    # Avoid calling create_agent with the object-only fake model; verify the
    # factory arguments by making the fake stop after construction.
    def raising_chat(**kwargs):
        main_call.update(kwargs)
        raise RuntimeError("captured")

    monkeypatch.setattr(factory, "build_chat_llm", raising_chat)
    try:
        react_graph.build_main_agent_graph([])
    except RuntimeError as exc:
        assert str(exc) == "captured"
    assert main_call["provider"] == "deepseek"
    assert main_call["thinking"] is True
    assert main_call["reasoning_effort"] == "high"

    planning_call = {}

    def fake_structured(schema, **kwargs):
        planning_call.update(kwargs)
        return object()

    monkeypatch.setattr(planning_nodes, "build_structured_llm", fake_structured)
    monkeypatch.setenv("PLANNING_AGENT_MODEL", "deepseek-v4-pro")
    planning_nodes._build_planning_llm(dict, None)
    assert planning_call["provider"] == "deepseek"
    assert planning_call["thinking"] is True
    assert planning_call["reasoning_effort"] == "high"

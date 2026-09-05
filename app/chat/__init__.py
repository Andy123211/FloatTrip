"""Conversational travel planning graph and services.

The package used to import both public objects eagerly.  Runtime repositories
also import ``app.chat.artifacts`` and Python executes this module first, which
created a repositories -> chat.service -> repositories import cycle.  Keep the
public API while loading either implementation only when it is requested.
"""

from __future__ import annotations

from typing import Any

__all__ = ["ChatService", "build_chat_graph"]


def __getattr__(name: str) -> Any:
    if name == "ChatService":
        from app.chat.service import ChatService

        return ChatService
    if name == "build_chat_graph":
        from app.chat.graph import build_chat_graph

        return build_chat_graph
    raise AttributeError(name)

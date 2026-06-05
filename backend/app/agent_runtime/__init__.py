"""
Agent Runtime Package
=====================

Lightweight tool-calling agent harness for CiteThreads.

Public surface
--------------
- ``AgentRuntime``  : main entry, run a chat turn with autonomous tool use
- ``ToolRegistry``  : register/lookup tool definitions and handlers
- ``SessionMemory`` : in-process per-session message history
- ``paper_search_service``, ``openalex``, ``arxiv`` etc. are imported lazily
  to avoid circular imports with the existing service layer.
"""

from .memory import SessionMemory, session_memory
from .runtime import AgentRuntime, AgentTurnResult, ToolCallRecord, agent_runtime
from .tools import ToolRegistry, build_default_registry, tool_registry

__all__ = [
    "AgentRuntime",
    "AgentTurnResult",
    "ToolCallRecord",
    "ToolRegistry",
    "SessionMemory",
    "agent_runtime",
    "session_memory",
    "build_default_registry",
    "tool_registry",
]

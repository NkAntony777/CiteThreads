"""
Agent runtime
=============

``AgentRuntime`` is a thin orchestrator on top of an OpenAI-compatible
chat completion API. It:

1. Builds a message list (system + history + new user turn)
2. Calls the LLM with the configured tool schemas
3. If the LLM returns ``tool_calls``, executes them via ``ToolRegistry``,
   appends tool result messages, and loops
4. Stops when the LLM emits a final assistant message OR the iteration
   cap is hit
5. Returns a ``AgentTurnResult`` that captures the final text, the
   tool calls that were made, and any errors

The shape of the returned object is deliberately close to
``ChatMessage`` so it can be wired into the existing writing assistant
without a frontend change.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Literal, Optional, Union

from openai import AsyncOpenAI
from types import SimpleNamespace

from ..config import settings
from ..models.references import ChatMessage, Paper as _Paper
from .memory import MemoryMessage, SessionMemory, session_memory
from .tools import ToolRegistry, ToolError, tool_registry as default_registry

logger = logging.getLogger(__name__)


DEFAULT_MAX_ITERATIONS = 60
DEFAULT_TOOL_TIMEOUT = 30.0
DEFAULT_REQUEST_TIMEOUT = 90.0
HISTORY_WINDOW = 10  # how many past messages to keep in the prompt

# Per-prompt-kind iteration caps. The SmartSearch task is allowed
# more headroom than the writing assistant because the snowball path
# (search → author → cite/reference expansion) can take many hops
# before producing a useful answer. The general writing assistant
# stays tighter to avoid runaway drafting costs.
MAX_ITERATIONS_BY_KIND: Dict[str, int] = {
    "general": 20,
    "search": 60,
}

# Available prompt flavors. ``general`` is the default writing-assistant
# prompt; ``search`` is the SmartSearch seed-paper prompt used by the
# SearchBar endpoint. Add new kinds here and route them in
# ``AgentRuntime._build_system_prompt``.
PromptKind = Literal["general", "search"]


SYSTEM_PROMPT = """你是一位学术论文写作研究助手 (Research Writing Agent)。

你可以使用以下工具来完成任务：
- search_papers: 检索论文(标题、关键词、DOI、arXiv ID)
- get_paper_details: 获取单篇论文的完整元数据
- list_project_references: 查看项目当前已引用的论文
- find_research_gaps: 在项目引用图谱上发现研究空白

工作原则：
1. 当用户提出需要文献的问题时,**必须**先调用 search_papers 获取候选,然后视情况调用 get_paper_details 深入。
2. 推荐论文前,优先调用 list_project_references 以避免重复推荐。
3. 讨论研究空白或下一步方向时,调用 find_research_gaps。
4. 用中文回复,使用 Markdown 格式。
5. 引用论文时使用 [@CitationKey] 格式(可由 search_papers 返回的 title 与 year 推断,例如 "Smith2021")。
6. 不要编造论文;所有引用必须来自工具实际返回的结果。
7. 一次最多调用工具 20 轮(本任务类型上限),然后给用户一个简洁的总结,不要陷入无限循环。
"""


# Prompt for the SmartSearch agent used by the SearchBar. The user
# hasn't built a project yet, so project-scoped tools are off-limits
# and the goal is to surface 5-10 candidate seed papers with a short
# Chinese recommendation paragraph.
SEARCH_SYSTEM_PROMPT = """你是一位学术种子论文检索助手 (Seed Paper Finder),帮助用户从一句话自然语言描述出发,找到几篇适合作为研究起点的论文。

可用工具:
- search_papers: 跨源检索论文(标题、关键词、DOI、arXiv ID),可附带结构化 filters
- get_paper_details: 获取单篇论文的完整元数据(摘要、作者、引用数等)
- get_citing_papers: **向后雪球**——给定一篇已知论文,返回"谁引用了它"(找近期跟进工作)
- get_referenced_papers: **向前雪球**——给定一篇已知论文,返回"它引用了谁"(找方法论基础)
- search_by_author: 按作者名找人(OpenAlex 的 ``author.search`` 过滤,arXiv 兜底)

不要调用 list_project_references 或 find_research_gaps —— 当前还没有项目。

工作原则:
1. 拿到用户输入后,先把原句拆成"核心关键词 + 隐含约束(领域/年份/会议/最低引用数)"。
   - 把核心关键词改写得更具体,放进 ``query`` 参数。
   - 把隐含约束放进 ``filters``(year_range / min_citations / venues / fields / sort)。
2. **按升级策略选工具,不要反复调 search_papers**:
   a. 用户的原句含明确 DOI / arXiv ID(形如 ``10.xxxx/...`` / ``arXiv:NNNN.NNNNN`` / ``W...``)→ 直接 ``get_paper_details`` 验真,然后用 ``get_citing_papers`` / ``get_referenced_papers`` 扩。
   b. 用户原句描述了一个领域 + 关键词 → 调 ``search_papers`` 1 次,覆盖 openalex + arxiv + dblp 起步。
      命中后挑 1-2 篇做 ``get_paper_details`` 拿摘要,可选 ``get_referenced_papers`` 看方法谱系。
   c. **若 search_papers 全部返回 0**(没有命中)→ 立即切换升级路径,不要再换关键词重试 search:
      - 优先 ``search_by_author``(用户口述中可能含作者名;若没有,可以问用户"这个领域你认识哪几位作者?")。
      - 找到一篇锚定论文后 → ``get_citing_papers`` / ``get_referenced_papers`` 向两侧雪球扩。
      - 仍 0 → 直接告诉用户"在常用学术源里没找到匹配的论文,你能否提供一个大致方向(子领域、代表作者、会议名)?",并停止调工具。
3. 候选需要去重,优先保留引用数较高、年份较近、与查询最相关的 5-10 篇。
4. **不要陷入工具循环**:连续 3 轮 0 命中或重复调同一工具时必须停止;超出本任务类型上限(60 轮)后也要给总结。
5. 用中文回复,使用 Markdown 格式。
6. 引用论文使用 [@AuthorYear] 格式(由 search_papers 返回的 title 与 year 推断,例如 "Smith2021")。
7. **不要编造论文**:所有引用必须来自工具实际返回的结果。
8. 末尾给出 1-2 句简短的推荐语,告诉用户"这几篇适合作为种子,选一篇就能建图谱"。

返回结构示例:
- 简短中文导语(1-2 句)
- 候选论文列表(5-10 条),每条包含:标题 / 作者 / 年份 / 会议或期刊 / 引用数
- 一句"建议你选 X 作为种子开始建图谱"
"""


@dataclass
class ToolCallRecord:
    """One tool invocation captured for observability and the response payload.

    ``result_raw`` is the full JSON serialization of the handler return
    value (used to feed back to the LLM as the ``tool`` message).
    ``result_preview`` is a short, truncated version safe for logs and
    the HTTP response.
    """

    tool: str
    arguments: Dict[str, Any]
    result_raw: str
    result_preview: str
    latency_ms: int
    error: Optional[str] = None
    tool_call_id: Optional[str] = None


@dataclass
class AgentTurnResult:
    """The outcome of one agent turn.

    The fields are designed to be a superset of ``ChatMessage`` so the
    existing frontend (which reads paper_suggestions / action_type) keeps
    working, while new fields expose the agent's tool activity.
    """

    role: str = "assistant"
    content: str = ""
    paper_suggestions: List[Any] = field(default_factory=list)
    action_type: Optional[str] = None
    tool_calls: List[ToolCallRecord] = field(default_factory=list)
    iterations: int = 0
    truncated: bool = False
    error: Optional[str] = None

    def to_chat_message(self) -> ChatMessage:
        return ChatMessage(
            role=self.role,
            content=self.content,
            paper_suggestions=self.paper_suggestions or None,
            action_type=self.action_type,
        )


# ---------------------------------------------------------------------------
# Event types for streaming
# ---------------------------------------------------------------------------

# Event ``type`` discriminators, kept short so the JSON stays small in SSE.
EVT_TEXT_DELTA = "text_delta"
EVT_TOOL_START = "tool_start"
EVT_TOOL_END = "tool_end"
EVT_PAPER_SUGGESTIONS = "paper_suggestions"
EVT_ERROR = "error"
EVT_DONE = "done"


@dataclass
class Event:
    """A single event yielded by ``AgentRuntime.run_stream``.

    The ``type`` field is one of the ``EVT_*`` constants. ``payload``
    contains type-specific data:

    - text_delta       : ``{"delta": "..."}``
    - tool_start       : ``{"tool": "search_papers", "arguments": {...}}``
    - tool_end         : ``{"tool": "...", "result_preview": "...",
                            "latency_ms": int, "error": str|None}``
    - paper_suggestions: ``{"papers": [...]}``
    - error            : ``{"message": "...", "code": "..."}``
    - done             : ``{"iterations": int, "truncated": bool,
                            "content": str, "action_type": str|None}``
    """

    type: str
    payload: Dict[str, Any] = field(default_factory=dict)


def _safe_preview(value: Any, limit: int = 280) -> str:
    """Stringify a tool result into a short preview safe for logging
    and embedding in the response payload."""
    try:
        if isinstance(value, (dict, list)):
            text = json.dumps(value, ensure_ascii=False, default=str)
        else:
            text = str(value)
    except Exception:
        text = repr(value)
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def _parse_tool_arguments(raw: Union[str, dict, None]) -> Dict[str, Any]:
    """OpenAI sometimes returns arguments as a JSON string, sometimes
    already parsed. Tolerate both."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Bad tool arguments JSON: %r", raw[:120])
            return {}
    return {}


def _tool_call_to_dict(tc: Any) -> Dict[str, Any]:
    """Convert an OpenAI tool_call object (pydantic model or plain
    namespace) into a plain dict. Pydantic v1/v2 have ``model_dump`` /
    ``dict``; mocks may be SimpleNamespace. Handle all three."""
    if isinstance(tc, dict):
        return tc
    if hasattr(tc, "model_dump"):
        try:
            return tc.model_dump()
        except Exception:
            pass
    if hasattr(tc, "dict"):
        try:
            return tc.dict()
        except Exception:
            pass
    # Fallback: pull the fields we know about.
    out: Dict[str, Any] = {}
    if hasattr(tc, "id"):
        out["id"] = tc.id
    if hasattr(tc, "type"):
        out["type"] = tc.type
    fn = getattr(tc, "function", None)
    if fn is not None:
        if hasattr(fn, "model_dump"):
            out["function"] = fn.model_dump()
        elif hasattr(fn, "dict"):
            out["function"] = fn.dict()
        else:
            out["function"] = {
                "name": getattr(fn, "name", None),
                "arguments": getattr(fn, "arguments", None),
            }
    return out


def _tool_calls_to_dicts(tool_calls: Optional[List[Any]]) -> List[Dict[str, Any]]:
    if not tool_calls:
        return []
    return [_tool_call_to_dict(tc) for tc in tool_calls]


class AgentRuntime:
    """Tool-calling agent backed by an OpenAI-compatible chat client."""

    def __init__(
        self,
        client: Optional[AsyncOpenAI] = None,
        registry: Optional[ToolRegistry] = None,
        memory: Optional[SessionMemory] = None,
        model: Optional[str] = None,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        request_timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> None:
        self.client = client
        self.registry = registry or default_registry
        self.memory = memory or session_memory
        self.model = model or settings.ai_model
        self.max_iterations = max_iterations
        self.request_timeout = request_timeout

    async def configure(
        self,
        api_key: str,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        """Hot-swap the LLM client and model.

        The supplied ``base_url`` is run through the same SSRF guard
        the ``/api/ai/test`` endpoints use, so an attacker who can
        POST to ``/api/agent/configure`` cannot point the agent at
        ``http://127.0.0.1:...`` or another private address. Raises
        ``ValueError`` for disallowed URLs.
        """
        if base_url is None:
            base_url = settings.ai_base_url
        # Reuse the same validator the AI router uses. Import here to
        # avoid a circular import at module load (routers import the
        # agent runtime).
        from ..routers.ai import _validate_and_normalize_base_url

        try:
            safe_base_url = await _validate_and_normalize_base_url(base_url)
        except ValueError as exc:
            logger.warning("agent runtime: rejected base_url: %s", exc)
            raise

        self.client = AsyncOpenAI(
            api_key=api_key, base_url=safe_base_url, timeout=self.request_timeout
        )
        if model:
            self.model = model

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        message: str,
        project_id: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        extra_context: Optional[str] = None,
        prompt_kind: "PromptKind" = "general",
    ) -> AgentTurnResult:
        """Run one agent turn. ``history`` is the prior conversation in
        the same shape the existing API accepts (list of
        ``{role, content}`` dicts).

        This is the non-streaming entry point; internally it consumes
        the shared ``_iter_turn`` generator and aggregates the events
        into an ``AgentTurnResult``.
        """
        result = AgentTurnResult()
        last_text: str = ""
        try:
            async for ev in self._iter_turn(
                message=message,
                project_id=project_id,
                history=history,
                extra_context=extra_context,
                prompt_kind=prompt_kind,
            ):
                if ev.type == EVT_TEXT_DELTA:
                    last_text += ev.payload.get("delta", "")
                elif ev.type == EVT_TOOL_END:
                    tc = ToolCallRecord(
                        tool=ev.payload["tool"],
                        arguments=ev.payload.get("arguments", {}),
                        result_raw=ev.payload.get("result_raw", ""),
                        result_preview=ev.payload.get("result_preview", ""),
                        latency_ms=ev.payload.get("latency_ms", 0),
                        error=ev.payload.get("error"),
                        tool_call_id=ev.payload.get("tool_call_id"),
                    )
                    result.tool_calls.append(tc)
                elif ev.type == EVT_ERROR:
                    result.error = ev.payload.get("code") or ev.payload.get("message")
                elif ev.type == EVT_DONE:
                    result.iterations = ev.payload.get("iterations", 0)
                    result.truncated = ev.payload.get("truncated", False)
                    result.content = ev.payload.get("content", last_text)
                    result.action_type = ev.payload.get("action_type")
                    result.paper_suggestions = ev.payload.get("paper_suggestions", [])
                    if result.paper_suggestions:
                        result.action_type = "search"
        except _ClientMissing as exc:
            return AgentTurnResult(
                content=exc.message,
                action_type="error",
                error=exc.code,
            )
        return result

    async def run_stream(
        self,
        message: str,
        project_id: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        extra_context: Optional[str] = None,
        prompt_kind: "PromptKind" = "general",
    ) -> AsyncIterator[Event]:
        """Run one agent turn and yield ``Event`` objects as the work
        progresses. Designed to be wrapped in a Server-Sent Events
        response: each event becomes ``data: <json>\\n\\n``.

        Event sequence (best effort):
            text_delta *N       (one or more tokens of the final answer)
            done                (always the last event; carries the full
                                 result so the client can render even
                                 after a dropped connection)
        Between them, for turns that trigger tool use:
            tool_start *K
            tool_end *K
        """
        try:
            async for ev in self._iter_turn(
                message=message,
                project_id=project_id,
                history=history,
                extra_context=extra_context,
                prompt_kind=prompt_kind,
            ):
                yield ev
        except _ClientMissing as exc:
            yield Event(type=EVT_ERROR, payload={"message": exc.message, "code": exc.code})
            yield Event(
                type=EVT_DONE,
                payload={"iterations": 0, "truncated": False, "content": exc.message},
            )

    # ------------------------------------------------------------------
    # Shared internal generator
    # ------------------------------------------------------------------

    async def _iter_turn(
        self,
        message: str,
        project_id: Optional[str],
        history: Optional[List[Dict[str, Any]]],
        extra_context: Optional[str],
        prompt_kind: "PromptKind" = "general",
    ) -> AsyncIterator[Event]:
        """Inner loop shared by ``run`` and ``run_stream``. Yields
        ``Event`` objects describing the agent's progress; the final
        event is always ``EVT_DONE`` with the aggregated result."""
        if not self.client:
            raise _ClientMissing(
                code="llm_not_configured",
                message=(
                    "LLM client not configured. Please set the AI "
                    "configuration in /api/ai/configure/llm first."
                ),
            )

        session_id = project_id or "default"

        # 1) Seed memory.
        self.memory.set_system(
            session_id, self._build_system_prompt(extra_context, prompt_kind=prompt_kind)
        )
        if history:
            for msg in history[-HISTORY_WINDOW * 2 :]:
                role = msg.get("role")
                if role in ("user", "assistant", "system", "tool"):
                    self.memory.append(
                        session_id,
                        MemoryMessage(
                            role=role,
                            content=msg.get("content"),
                            name=msg.get("name"),
                            tool_call_id=msg.get("tool_call_id"),
                            tool_calls=msg.get("tool_calls"),
                        ),
                    )
        self.memory.append(session_id, MemoryMessage(role="user", content=message))

        tool_schemas = self.registry.schemas()
        final_content: str = ""
        final_action: Optional[str] = None
        tool_records: List[ToolCallRecord] = []
        final_papers: List[Dict[str, Any]] = []
        # Tracks which paper IDs the runtime has already surfaced
        # mid-stream via EVT_PAPER_SUGGESTIONS, so each subsequent
        # emit only carries the *new* ones. Populated by
        # ``_stream_new_papers``; the loop feeds the same set in.
        seen_paper_ids: set = set()
        truncated = False
        error_code: Optional[str] = None
        iterations_done = 0

        # Resolve the effective cap for this turn. The constructor's
        # ``max_iterations`` is the ceiling; ``MAX_ITERATIONS_BY_KIND``
        # lets specific task kinds (SmartSearch especially) get more
        # headroom without changing the default for everyone else.
        effective_max = self._resolve_effective_max_iterations(prompt_kind)

        try:
            for iteration in range(1, effective_max + 1):
                iterations_done = iteration
                messages = [
                    m.model_dump(exclude_none=True)
                    for m in self.memory.get(session_id)
                ]

                # Streaming LLM call. We always use stream=True so the
                # frontend can render text deltas in real time. Tool
                # call deltas are accumulated from the same stream and
                # dispatched in batch after the stream completes.
                accum: Dict[str, Any] = {}
                async for ev in self._stream_llm_turn(
                    messages=messages, tool_schemas=tool_schemas, accum=accum
                ):
                    if ev.type == EVT_ERROR:
                        # Surface the error and bail out of the turn.
                        error_code = ev.payload.get("code") or "stream_error"
                        final_content = ev.payload.get("message", "")
                        yield ev
                        accum = None  # type: ignore[assignment]
                        break
                    yield ev

                if accum is None or accum.get("error"):
                    # Error was already yielded; break the turn loop.
                    if accum is not None and accum.get("error") == "llm_timeout":
                        truncated = True
                    break

                accumulated_text = accum["text"]
                tool_calls_raw = accum["tool_calls"]

                # Record the assistant turn (might carry tool_calls).
                self.memory.append(
                    session_id,
                    MemoryMessage(
                        role="assistant",
                        content=accumulated_text,
                        tool_calls=_tool_calls_to_dicts(tool_calls_raw) or None,
                    ),
                )

                # 3a) If the model wants to call tools, run them.
                if tool_calls_raw:
                    # Emit tool_start events first so the UI can show
                    # pending tool cards before the result arrives.
                    pending: List[Any] = []
                    for tc in tool_calls_raw:
                        args = _parse_tool_arguments(tc.function.arguments)
                        pending.append((tc, args))
                        yield Event(
                            type=EVT_TOOL_START,
                            payload={
                                "tool": tc.function.name,
                                "arguments": args,
                                "tool_call_id": getattr(tc, "id", None),
                            },
                        )

                    # Execute tools in parallel.
                    tool_tasks = [
                        self._dispatch_tool(
                            name=tc.function.name,
                            arguments=args,
                            project_id=project_id,
                        )
                        for tc, args in pending
                    ]
                    outcomes = await asyncio.gather(*tool_tasks, return_exceptions=True)

                    # Pair outcomes back with their tool_call ids and
                    # emit tool_end + feed tool messages into memory.
                    for (tc, args), outcome in zip(pending, outcomes):
                        tc_id = getattr(tc, "id", None)
                        if isinstance(outcome, Exception) or outcome is None:
                            if isinstance(outcome, Exception):
                                logger.error("Tool dispatch exception: %s", outcome)
                            # Synthesize an error record so the LLM still
                            # sees a tool message in the next turn.
                            err_text = (
                                str(outcome) if isinstance(outcome, Exception) else "no result"
                            )
                            record = ToolCallRecord(
                                tool=tc.function.name,
                                arguments=args,
                                result_raw=err_text,
                                result_preview=err_text,
                                latency_ms=0,
                                error=err_text,
                                tool_call_id=tc_id,
                            )
                        else:
                            outcome.tool_call_id = tc_id
                            record = outcome
                        tool_records.append(record)
                        self.memory.append(
                            session_id,
                            MemoryMessage(
                                role="tool",
                                name=record.tool,
                                tool_call_id=tc_id,
                                content=record.result_raw,
                            ),
                        )
                        yield Event(
                            type=EVT_TOOL_END,
                            payload={
                                "tool": record.tool,
                                "arguments": record.arguments,
                                "result_preview": record.result_preview,
                                "result_raw": record.result_raw,
                                "latency_ms": record.latency_ms,
                                "error": record.error,
                                "tool_call_id": tc_id,
                            },
                        )

                    # Stream any NEW papers to the client immediately,
                    # before the agent starts its next round. The user
                    # has been burned by the old "collect everything
                    # and emit at the final answer" behavior: if the
                    # agent burns through 60 iterations without giving
                    # a final answer (which happens on hard queries),
                    # the panel ends up empty even though the
                    # ``search_papers`` calls returned 20 candidates
                    # each round. Emitting mid-stream keeps the
                    # candidate list visible as it grows.
                    new_papers = self._stream_new_papers(
                        tool_records, seen_paper_ids
                    )
                    if new_papers:
                        yield Event(
                            type=EVT_PAPER_SUGGESTIONS,
                            payload={"papers": new_papers},
                        )
                    continue

                # 3b) No tool calls -> this is the final answer.
                # Text has already been yielded token-by-token by
                # ``_stream_llm_turn``; here we just harvest metadata
                # and break out of the turn loop.
                final_content = accumulated_text
                final_action = "answer"
                # The full snapshot goes into the ``done`` payload
                # (the client uses it as the authoritative state on
                # reconnect). We've already streamed the per-iter
                # deltas above, so we don't re-emit a duplicate
                # EVT_PAPER_SUGGESTIONS here.
                final_papers = self._collect_paper_suggestions(tool_records)
                if final_papers:
                    final_action = "search"
                break
            else:
                truncated = True
                final_content = "已达到本轮工具调用上限,如需继续请发送新消息。"
                # Even on the cap, surface whatever candidates we've
                # already accumulated. Without this, a turn that
                # burns through 60 iterations never returns to the
                # client and the panel stays empty.
                final_papers = self._collect_paper_suggestions(tool_records)
                if final_papers:
                    final_action = "search"
                yield Event(
                    type=EVT_ERROR,
                    payload={"code": "iteration_cap", "message": final_content},
                )
        except Exception as exc:
            logger.exception("Agent runtime error")
            error_code = str(exc)
            if not final_content:
                final_content = f"Agent 内部错误: {exc}"
            yield Event(
                type=EVT_ERROR,
                payload={"code": error_code, "message": final_content},
            )

        # Final done event: always emit so the client has a complete
        # picture even if it dropped the connection mid-stream.
        yield Event(
            type=EVT_DONE,
            payload={
                "iterations": iterations_done,
                "truncated": truncated,
                "content": final_content,
                "action_type": final_action,
                "paper_suggestions": final_papers,
                "tool_calls": [
                    {
                        "tool": r.tool,
                        "arguments": r.arguments,
                        "result_preview": r.result_preview,
                        "latency_ms": r.latency_ms,
                        "error": r.error,
                        "tool_call_id": r.tool_call_id,
                    }
                    for r in tool_records
                ],
                "error": error_code,
            },
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _stream_llm_turn(
        self,
        messages: List[Dict[str, Any]],
        tool_schemas: List[Dict[str, Any]],
        accum: Dict[str, Any],
    ) -> AsyncIterator[Event]:
        """Run a single streaming LLM turn.

        Yields ``EVT_TEXT_DELTA`` events as content tokens arrive, and
        populates ``accum`` with the final turn state:

        - ``accum["text"]``        : str, the full assistant text
        - ``accum["tool_calls"]``  : list of tool_call objects (each
          with ``id``, ``type='function'``, ``function.name``,
          ``function.arguments``)
        - ``accum["finish_reason"]``: str or None
        - ``accum["error"]``       : str error code on failure

        The provider's stream protocol allows exactly one of text or
        tool_calls in any given response; this helper handles both.
        """
        # Make the streaming call. The OpenAI Python SDK accepts
        # ``stream=True`` and returns an async iterator of chunks. We
        # wrap the whole thing in a timeout so a slow provider doesn't
        # hang a user-facing turn.
        try:
            stream = await asyncio.wait_for(
                self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tool_schemas if tool_schemas else None,
                    tool_choice="auto" if tool_schemas else None,
                    temperature=0.4,
                    max_tokens=1800,
                    stream=True,
                ),
                timeout=self.request_timeout,
            )
        except asyncio.TimeoutError:
            accum["error"] = "llm_timeout"
            yield Event(
                type=EVT_ERROR,
                payload={"code": "llm_timeout", "message": "模型响应超时,请稍后重试。"},
            )
            return
        except Exception as exc:  # network / protocol / 5xx
            logger.exception("Streaming LLM call failed at open")
            accum["error"] = "llm_open_failed"
            yield Event(
                type=EVT_ERROR,
                payload={"code": "llm_open_failed", "message": str(exc)},
            )
            return

        accum.setdefault("text", "")
        accum.setdefault("tool_calls_dict", {})
        accum.setdefault("finish_reason", None)
        accum.setdefault("error", None)

        # Tool call deltas arrive as a list keyed by ``index``. We
        # accumulate per-index state into a dict.
        try:
            async for chunk in stream:
                if not getattr(chunk, "choices", None):
                    continue
                choice = chunk.choices[0]
                delta = getattr(choice, "delta", None)
                if choice.finish_reason is not None:
                    accum["finish_reason"] = choice.finish_reason
                if delta is None:
                    continue

                # 1) Text tokens -> emit text_delta events as they
                # arrive so the UI can render progressively.
                text_piece = getattr(delta, "content", None)
                if text_piece:
                    accum["text"] += text_piece
                    yield Event(type=EVT_TEXT_DELTA, payload={"delta": text_piece})

                # 2) Tool call deltas -> accumulate.
                tc_deltas = getattr(delta, "tool_calls", None)
                if tc_deltas:
                    for tc in tc_deltas:
                        idx = getattr(tc, "index", 0)
                        slot = accum["tool_calls_dict"].setdefault(
                            idx, {"id": None, "name": "", "arguments": ""}
                        )
                        if getattr(tc, "id", None):
                            slot["id"] = tc.id
                        fn = getattr(tc, "function", None)
                        if fn is not None:
                            if getattr(fn, "name", None):
                                slot["name"] = fn.name
                            if getattr(fn, "arguments", None):
                                slot["arguments"] += fn.arguments
        except Exception as exc:
            logger.exception("Streaming LLM call failed mid-stream")
            accum["error"] = "llm_stream_failed"
            yield Event(
                type=EVT_ERROR,
                payload={"code": "llm_stream_failed", "message": str(exc)},
            )
            return

        # Build tool_calls list in the shape the rest of the runtime
        # expects (a list of objects with .id / .function.name /
        # .function.arguments). Empty list when the model gave a final
        # text answer instead.
        tool_calls: List[Any] = []
        for idx in sorted(accum["tool_calls_dict"].keys()):
            info = accum["tool_calls_dict"][idx]
            if not info["name"]:
                # Skip malformed partial tool calls.
                continue
            tool_calls.append(
                SimpleNamespace(
                    id=info["id"],
                    type="function",
                    function=SimpleNamespace(
                        name=info["name"],
                        arguments=info["arguments"],
                    ),
                )
            )
        accum["tool_calls"] = tool_calls
    def _resolve_effective_max_iterations(self, prompt_kind: "PromptKind") -> int:
        """Compute the iteration cap for a turn.

        The constructor's ``max_iterations`` is the absolute ceiling;
        ``MAX_ITERATIONS_BY_KIND`` lets specific task kinds
        (SmartSearch especially) get more headroom without changing
        the default for everyone else. Exposed as a method so the
        test suite can verify the resolution without spinning up an
        LLM.
        """
        kind_cap = MAX_ITERATIONS_BY_KIND.get(prompt_kind, self.max_iterations)
        return min(self.max_iterations, kind_cap)

    def _build_system_prompt(
        self, extra_context: Optional[str], prompt_kind: "PromptKind" = "general"
    ) -> str:
        """Pick the right system prompt for the call site and append any
        extra context the caller passed in. ``prompt_kind="search"``
        routes to ``SEARCH_SYSTEM_PROMPT`` (the SmartSearch agent);
        everything else uses the general writing-assistant prompt.
        """
        base = SEARCH_SYSTEM_PROMPT if prompt_kind == "search" else SYSTEM_PROMPT
        if extra_context:
            return base + "\n\n## 额外上下文\n" + extra_context
        return base

    async def _dispatch_tool(
        self,
        name: str,
        arguments: Dict[str, Any],
        project_id: Optional[str],
    ) -> Optional[ToolCallRecord]:
        """Execute a single tool and return a record. Returns None on
        unrecoverable dispatch errors."""
        start = time.perf_counter()
        try:
            # Inject project_id automatically when the tool needs it
            # and the model forgot to pass it. Reduces prompt brittleness.
            if project_id and "project_id" in (self.registry.get(name).parameters.get("properties", {}) if self.registry.get(name) else {}):
                arguments = {**arguments, "project_id": arguments.get("project_id") or project_id}

            value = await self.registry.invoke(name, arguments)
            latency = int((time.perf_counter() - start) * 1000)
            raw = _safe_preview(value, limit=200_000)  # full payload for LLM
            return ToolCallRecord(
                tool=name,
                arguments=arguments,
                result_raw=raw,
                result_preview=_safe_preview(value),
                latency_ms=latency,
            )
        except ToolError as exc:
            latency = int((time.perf_counter() - start) * 1000)
            logger.warning("Tool %s failed: %s", name, exc)
            return ToolCallRecord(
                tool=name,
                arguments=arguments,
                result_raw=str(exc),
                result_preview=str(exc),
                latency_ms=latency,
                error=str(exc),
            )
        except Exception as exc:
            latency = int((time.perf_counter() - start) * 1000)
            logger.exception("Tool %s raised unexpectedly", name)
            return ToolCallRecord(
                tool=name,
                arguments=arguments,
                result_raw=f"unexpected error: {exc}",
                result_preview=f"unexpected error: {exc}",
                latency_ms=latency,
                error=str(exc),
            )

    # Tools whose result payload contains a "papers" list we should
    # surface to the client. Kept here as the single source of truth
    # for the streaming collector below.
    _PAPER_TOOLS = frozenset(
        {
            "search_papers",
            "get_citing_papers",
            "get_referenced_papers",
            "search_by_author",
        }
    )

    # Cap the total number of papers surfaced per turn. Going much
    # higher makes the suggestion panel unwieldy; this matches the
    # original 5 + a bit more headroom for multi-source snowball
    # results.
    _MAX_SUGGESTIONS_PER_TURN = 10

    def _extract_papers_from_record(
        self, record: ToolCallRecord
    ) -> List[Dict[str, Any]]:
        """Pull ``{"papers": [...]}`` out of any snowball tool's
        result payload, validating each paper. Returns an empty list
        on JSON / validation errors so the caller can stay simple."""
        from ..models import Paper

        if record.tool not in self._PAPER_TOOLS or record.error:
            return []
        try:
            payload = json.loads(record.result_raw)
        except (json.JSONDecodeError, TypeError):
            return []
        out: List[Dict[str, Any]] = []
        for paper in payload.get("papers", []) or []:
            try:
                Paper.model_validate(paper)
            except Exception:
                continue
            out.append(_paper_dict_for_response(paper))
        return out

    def _paper_id(self, paper: Dict[str, Any]) -> Optional[str]:
        """Best-effort stable id for a paper dict, used to dedupe
        across ``search_papers`` and snowball calls.

        A paper surfaced by two different sources often has
        different ``id`` values (e.g. ``OpenAlex:W1`` vs
        ``arXiv:2106.09685`` for the same work), so DOI is the
        preferred key when present. We accept any of the stable
        identifiers and fall back to the title for the worst case.
        Returns ``None`` only when the dict is truly anonymous, in
        which case the caller can still surface the paper but won't
        be able to dedupe it.
        """
        # DOI is the most cross-source stable identifier; normalize
        # away the URL prefix some crawlers include.
        doi = paper.get("doi")
        if doi:
            normalized = str(doi).replace("https://doi.org/", "").lower().strip()
            if normalized:
                return f"doi:{normalized}"

        arxiv = paper.get("arxiv_id")
        if arxiv:
            return f"arxiv:{str(arxiv).lower().strip()}"

        pid = paper.get("id")
        if pid:
            return f"id:{pid}"

        title = paper.get("title")
        if title:
            return f"title:{title}"

        return None

    def _stream_new_papers(
        self,
        tool_records: List[ToolCallRecord],
        seen_paper_ids: set,
    ) -> List[Dict[str, Any]]:
        """Return the list of papers that have NOT been surfaced yet,
        in the order they were discovered. Mutates ``seen_paper_ids``
        in place to track what's been emitted so the next call only
        produces the deltas."""
        new: List[Dict[str, Any]] = []
        for record in tool_records:
            for paper in self._extract_papers_from_record(record):
                pid = self._paper_id(paper)
                if not pid or pid in seen_paper_ids:
                    continue
                seen_paper_ids.add(pid)
                new.append(paper)
                if len(seen_paper_ids) >= self._MAX_SUGGESTIONS_PER_TURN:
                    return new
        return new

    def _collect_paper_suggestions(
        self, tool_calls: List[ToolCallRecord]
    ) -> List[Dict[str, Any]]:
        """Snapshot helper used by the ``done`` payload. Returns up to
        ``_MAX_SUGGESTIONS_PER_TURN`` papers, deduped by id/doi/title,
        in discovery order. The streaming deltas above have already
        pushed the same papers to the client; this is the
        authoritative state for the ``done`` event so reconnects
        don't lose them."""
        seen: set = set()
        out: List[Dict[str, Any]] = []
        for record in tool_calls:
            for paper in self._extract_papers_from_record(record):
                pid = self._paper_id(paper)
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                out.append(paper)
                if len(out) >= self._MAX_SUGGESTIONS_PER_TURN:
                    return out
        return out


def _paper_dict_for_response(paper: Any) -> Dict[str, Any]:
    """Normalize a paper dict for the response payload (drop None values
    and stringify any odd types)."""
    if isinstance(paper, dict):
        return {k: v for k, v in paper.items() if v is not None}
    if hasattr(paper, "model_dump"):
        return paper.model_dump(exclude_none=True)
    return dict(paper)


class _ClientMissing(Exception):
    """Internal: raised when no LLM client is configured."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# Module-level singleton: importable as ``from app.agent_runtime import agent_runtime``.
agent_runtime: AgentRuntime = AgentRuntime()

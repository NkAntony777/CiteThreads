"""
Tool Registry
=============

Wraps existing services as OpenAI-compatible function-calling tools.

Each tool is a dict with:
  - name: short identifier (snake_case)
  - description: when the LLM should call it
  - parameters: JSON Schema for arguments
  - handler: async callable(**kwargs) -> JSON-serializable result

Add new tools by registering a (schema, handler) pair with
``ToolRegistry.register``. The default registry exposes the four tools
most useful for a research-writing agent.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# Default per-tool timeout; long enough for external API calls, short
# enough that a single bad tool doesn't hang a whole agent turn.
DEFAULT_TOOL_TIMEOUT = float(30)


class ToolError(Exception):
    """Raised when a tool handler fails. Returned to the LLM as a tool
    message so the model can recover gracefully."""


@dataclass
class ToolDefinition:
    """A registered tool: schema for the LLM + async handler."""

    name: str
    description: str
    parameters: Dict[str, Any]
    handler: Callable[..., Awaitable[Any]]
    timeout: float = DEFAULT_TOOL_TIMEOUT


class ToolRegistry:
    """In-memory store of tools available to the agent."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            logger.warning("Overwriting existing tool registration: %s", tool.name)
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def schemas(self) -> List[Dict[str, Any]]:
        """Return OpenAI-compatible tool schemas."""
        out: List[Dict[str, Any]] = []
        for t in self._tools.values():
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
            )
        return out

    def names(self) -> List[str]:
        return list(self._tools.keys())

    async def invoke(self, name: str, arguments: Dict[str, Any]) -> Any:
        """Invoke a tool by name with a timeout. Returns the handler's
        result or raises ``ToolError`` on failure."""
        tool = self.get(name)
        if tool is None:
            raise ToolError(f"Unknown tool: {name}")
        try:
            return await asyncio.wait_for(tool.handler(**arguments), timeout=tool.timeout)
        except asyncio.TimeoutError as exc:
            raise ToolError(f"Tool '{name}' timed out after {tool.timeout}s") from exc
        except TypeError as exc:
            # Bad arguments from the LLM
            raise ToolError(f"Tool '{name}' got bad arguments: {exc}") from exc
        except Exception as exc:
            logger.exception("Tool %s raised", name)
            raise ToolError(f"Tool '{name}' failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Tool handlers (lazy imports to avoid circulars)
# ---------------------------------------------------------------------------

_ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$", re.IGNORECASE)
_DOI_RE = re.compile(r"^10\.\d{4,}/", re.IGNORECASE)


def _detect_paper_id_kind(value: str) -> str:
    v = value.strip()
    if v.startswith("OpenAlex:") or v.startswith("arXiv:") or v.startswith("DOI:"):
        return "tagged"
    if _ARXIV_ID_RE.match(v):
        return "arxiv"
    if _DOI_RE.match(v):
        return "doi"
    return "unknown"


async def _search_papers_handler(
    query: str,
    sources: Optional[List[str]] = None,
    limit: int = 5,
    filters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Search academic papers across configured sources.

    ``filters`` is an optional structured-intent blob. The service
    passes what each source can honor and logs the rest; the response
    payload includes a ``filters_applied`` map so the caller knows
    which constraints actually narrowed the results.
    """
    from ..services.paper_search_service import (
        SearchFilters,
        paper_search_service,
    )

    if not query or not query.strip():
        return {
            "papers": [],
            "total": 0,
            "sources_searched": [],
            "errors": {"_": "empty query"},
            "filters_applied": paper_search_service._compute_filters_applied(None),
        }

    limit = max(1, min(int(limit or 5), 10))
    search_filters = _build_search_filters(filters)
    result = await paper_search_service.search(
        query=query.strip(),
        sources=sources,
        filters=search_filters,
        limit=limit,
    )
    return {
        "total": result.total,
        "sources_searched": result.sources_searched,
        "errors": result.errors,
        "filters_applied": result.filters_applied,
        "papers": [p.model_dump() for p in result.papers[:limit]],
    }


def _build_search_filters(filters: Optional[Dict[str, Any]]):
    """Translate the tool-layer ``filters`` dict into a
    ``SearchFilters`` dataclass, tolerating bad or partial input.

    Unknown keys are dropped; bad-shaped values are coerced where
    possible. The point is to keep the agent's tool call forgiving —
    if a number sneaks in as a string, we try to recover instead of
    failing the whole search.
    """
    from ..services.paper_search_service import SearchFilters

    if not filters or not isinstance(filters, dict):
        return None

    year_range = filters.get("year_range")
    if year_range is not None:
        if isinstance(year_range, (list, tuple)) and len(year_range) == 2:
            try:
                year_range = (int(year_range[0]), int(year_range[1]))
            except (TypeError, ValueError):
                year_range = None
        else:
            year_range = None

    min_citations = filters.get("min_citations")
    if min_citations is not None:
        try:
            min_citations = int(min_citations)
        except (TypeError, ValueError):
            min_citations = None

    fields = filters.get("fields")
    if fields is not None and not isinstance(fields, list):
        fields = None

    venues = filters.get("venues")
    if venues is not None and not isinstance(venues, list):
        venues = None

    sort = filters.get("sort")
    if sort not in (None, "relevance", "citations", "date"):
        sort = None

    return SearchFilters(
        year_range=year_range,
        min_citations=min_citations,
        fields=fields,
        venues=venues,
        # Treat ``venues`` as DBLP ``conferences`` for backward compat.
        conferences=venues,
        sort=sort,
    )


async def _get_paper_details_handler(paper_id: str) -> Dict[str, Any]:
    """Fetch full metadata for a single paper by ID (DOI, arXiv, OpenAlex)."""
    from ..crawlers import openalex, arxiv, semantic_scholar

    if not paper_id:
        return {"error": "paper_id is required"}
    kind = _detect_paper_id_kind(paper_id)
    paper = None
    if paper_id.startswith("OpenAlex:"):
        paper = await openalex.get_paper_by_id(paper_id)
    elif paper_id.startswith("arXiv:") or (kind == "arxiv"):
        clean = paper_id.split(":", 1)[-1]
        paper = await arxiv.get_paper_by_id(clean)
        if not paper:
            paper = await openalex.get_paper_by_id(f"arXiv:{clean}")
    elif paper_id.startswith("10.") or paper_id.startswith("DOI:"):
        doi = paper_id.split(":", 1)[-1]
        paper = await openalex.get_paper_by_id(f"DOI:{doi}")
        if not paper:
            paper = await semantic_scholar.get_paper_by_id(doi)
    else:
        paper = await semantic_scholar.get_paper_by_id(paper_id)
    if not paper:
        return {"error": f"Paper not found: {paper_id}"}
    return paper.model_dump()


async def _list_project_references_handler(project_id: str) -> Dict[str, Any]:
    """Return the references currently attached to a writing project."""
    from ..services.storage import get_project

    if not project_id:
        return {"error": "project_id is required", "references": []}
    try:
        project = get_project(project_id)
    except Exception as exc:
        return {"error": f"Failed to load project: {exc}", "references": []}
    if project is None:
        return {"error": "project not found", "references": []}
    refs = project.graph if hasattr(project, "graph") else None
    nodes = []
    if refs is not None and getattr(refs, "nodes", None):
        nodes = [n.model_dump() for n in refs.nodes[:25]]
    return {
        "project_id": project_id,
        "count": len(nodes),
        "papers": nodes,
    }


async def _find_research_gaps_handler(project_id: str) -> Dict[str, Any]:
    """Run research-gap detection on a project's citation graph."""
    from ..services.gap_detection import GapDetector
    from ..services.storage import get_project

    if not project_id:
        return {"error": "project_id is required", "gaps": []}
    try:
        project = get_project(project_id)
    except Exception as exc:
        return {"error": f"Failed to load project: {exc}", "gaps": []}
    if project is None or not getattr(project, "graph", None):
        return {"error": "project or graph not found", "gaps": []}
    detector = GapDetector()
    gaps = detector.detect(project.graph)
    return {
        "project_id": project_id,
        "total": len(gaps),
        "gaps": gaps[:10],
    }


async def _get_citing_papers_handler(
    paper_id: str,
    limit: int = 10,
    sources: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Return papers that cite ``paper_id`` (backward snowball).

    Use when the user already has a known paper in mind and wants to
    find recent follow-ups, or after a keyword search returns nothing
    and we want to expand from a single anchor paper. Tries OpenAlex
    first (it accepts DOI, arXiv, and OpenAlex IDs natively); falls
    back to Semantic Scholar for arXiv IDs that OpenAlex can't resolve
    on the first hop.
    """
    from ..crawlers import openalex, semantic_scholar

    if not paper_id:
        return {"error": "paper_id is required", "papers": [], "sources_searched": []}

    limit = max(1, min(int(limit or 10), 30))
    requested = [s.lower() for s in (sources or ["openalex", "semantic_scholar"])]
    papers: List[Any] = []
    sources_searched: List[str] = []
    errors: Dict[str, str] = {}

    if "openalex" in requested and not papers:
        try:
            candidates = await openalex.get_citations(paper_id, limit=limit)
            if candidates:
                papers = candidates
            sources_searched.append("openalex")
        except Exception as exc:  # noqa: BLE001
            errors["openalex"] = str(exc)

    # Fall back to S2 when OpenAlex returned nothing (most often the
    # case for arXiv-only IDs, since OpenAlex's _resolve_to_openalex_id
    # doesn't always follow arXiv forward).
    if not papers and "semantic_scholar" in requested:
        try:
            candidates = await semantic_scholar.get_citations(paper_id, limit=limit)
            if candidates:
                papers = candidates
            sources_searched.append("semantic_scholar")
        except Exception as exc:  # noqa: BLE001
            errors["semantic_scholar"] = str(exc)

    return {
        "anchor_paper_id": paper_id,
        "direction": "citing",
        "total": len(papers),
        "sources_searched": sources_searched,
        "errors": errors,
        "papers": [p.model_dump() for p in papers[:limit]],
    }


async def _get_referenced_papers_handler(
    paper_id: str,
    limit: int = 10,
    sources: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Return papers referenced by ``paper_id`` (forward snowball).

    Use when the user wants to see the foundation that a known paper
    builds on (methodology papers, surveys, etc.) or to widen a thin
    keyword search by expanding from a single anchor.
    """
    from ..crawlers import openalex, semantic_scholar

    if not paper_id:
        return {"error": "paper_id is required", "papers": [], "sources_searched": []}

    limit = max(1, min(int(limit or 10), 30))
    requested = [s.lower() for s in (sources or ["openalex", "semantic_scholar"])]
    papers: List[Any] = []
    sources_searched: List[str] = []
    errors: Dict[str, str] = {}

    if "openalex" in requested and not papers:
        try:
            candidates = await openalex.get_references(paper_id, limit=limit)
            if candidates:
                papers = candidates
            sources_searched.append("openalex")
        except Exception as exc:  # noqa: BLE001
            errors["openalex"] = str(exc)

    if not papers and "semantic_scholar" in requested:
        try:
            candidates = await semantic_scholar.get_references(paper_id, limit=limit)
            if candidates:
                papers = candidates
            sources_searched.append("semantic_scholar")
        except Exception as exc:  # noqa: BLE001
            errors["semantic_scholar"] = str(exc)

    return {
        "anchor_paper_id": paper_id,
        "direction": "referenced",
        "total": len(papers),
        "sources_searched": sources_searched,
        "errors": errors,
        "papers": [p.model_dump() for p in papers[:limit]],
    }


async def _search_by_author_handler(
    author_name: str,
    limit: int = 10,
    sources: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Find papers by a given author name.

    Use when keyword search returns nothing and the user can name a
    likely author in the field (e.g. "papers by Hinton" or "first author
    Smith 2020"). OpenAlex's ``author.search`` filter on the works
    endpoint is the primary path; arXiv is used as a fallback because
    it's the most permissive about author matching.
    """
    from ..crawlers import openalex, arxiv

    if not author_name or not author_name.strip():
        return {"error": "author_name is required", "papers": [], "sources_searched": []}

    author_name = author_name.strip()
    limit = max(1, min(int(limit or 10), 20))
    requested = [s.lower() for s in (sources or ["openalex", "arxiv"])]
    papers: List[Any] = []
    sources_searched: List[str] = []
    errors: Dict[str, str] = {}

    if "openalex" in requested:
        try:
            # OpenAlex lets us filter works by author name in a single
            # call: filter=author.search:"Lastname Firstname" or
            # filter=author.search:Lastname. We keep the human-typed
            # name as-is; OpenAlex is reasonably tolerant.
            data = await openalex._request(  # noqa: SLF001 — internal helper, public-by-design
                "/works",
                {"filter": f"author.search:{author_name}", "per-page": limit},
            )
            if data and data.get("results"):
                papers = [openalex._parse_paper(w) for w in data["results"]]  # noqa: SLF001
            sources_searched.append("openalex")
        except Exception as exc:  # noqa: BLE001
            errors["openalex"] = str(exc)

    # arXiv's author query is loose (matches anywhere in the metadata),
    # so it's a useful fallback when OpenAlex has no record for the
    # author. arXiv.search_papers is also the only path that can
    # surface pure-preprint authors.
    if not papers and "arxiv" in requested:
        try:
            candidates = await arxiv.search_papers(f"au:{author_name}", limit=limit)
            if candidates:
                papers = candidates
            sources_searched.append("arxiv")
        except Exception as exc:  # noqa: BLE001
            errors["arxiv"] = str(exc)

    return {
        "author_name": author_name,
        "total": len(papers),
        "sources_searched": sources_searched,
        "errors": errors,
        "papers": [p.model_dump() for p in papers[:limit]],
    }


# ---------------------------------------------------------------------------
# Default registry
# ---------------------------------------------------------------------------

SEARCH_PAPERS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Search query: rewritten/expanded keywords, DOI, or arXiv ID. "
                "Always include the keywords you want to match, even when you "
                "also pass ``filters``."
            ),
        },
        "sources": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": ["openalex", "arxiv", "dblp", "pubmed", "semantic_scholar"],
            },
            "description": "Optional list of sources to query. Omit to use defaults.",
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 10,
            "description": "Maximum number of papers to return (1-10).",
        },
        "filters": {
            "type": "object",
            "description": (
                "Optional structured intent (year range, min citations, "
                "fields, venues, sort). Pass them here when the user's "
                "request implies a constraint; if the source cannot honor "
                "a given field, the service falls back to passing only the "
                "rewritten query, so always put the keywords in ``query``."
            ),
            "properties": {
                "year_range": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "minItems": 2,
                    "maxItems": 2,
                    "description": "[start_year, end_year] inclusive.",
                },
                "min_citations": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Minimum citation count.",
                },
                "fields": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Research fields/topics. Currently advisory only; the "
                        "service uses them as extra query tokens but does not "
                        "filter at the source level."
                    ),
                },
                "venues": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Conference or journal names. Today only DBLP honors "
                        "this; other sources receive the rewritten query only."
                    ),
                },
                "sort": {
                    "type": "string",
                    "enum": ["relevance", "citations", "date"],
                    "description": "Preferred sort order (advisory).",
                },
            },
        },
    },
    "required": ["query"],
}

GET_PAPER_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "paper_id": {
            "type": "string",
            "description": (
                "Paper identifier. Accepts DOI ('10.xxxx/...'), arXiv ID "
                "('2106.09685' or 'arXiv:2106.09685'), or OpenAlex ID "
                "('OpenAlex:W...')."
            ),
        },
    },
    "required": ["paper_id"],
}

LIST_REFS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "project_id": {
            "type": "string",
            "description": "Project ID whose references to list.",
        },
    },
    "required": ["project_id"],
}

FIND_GAPS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "project_id": {
            "type": "string",
            "description": "Project ID whose citation graph to analyze.",
        },
    },
    "required": ["project_id"],
}


# Common parameter block for the snowball-style tools below.
_SOURCES_ENUM = ["openalex", "semantic_scholar", "arxiv"]

_PAPER_ID_DESCRIPTION = (
    "Paper identifier. Accepts DOI ('10.xxxx/...'), arXiv ID "
    "('2106.09685' or 'arXiv:2106.09685'), or OpenAlex ID "
    "('OpenAlex:W...'). The tool tries each format against the "
    "configured sources."
)

GET_CITING_PAPERS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "paper_id": {
            "type": "string",
            "description": _PAPER_ID_DESCRIPTION,
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 30,
            "description": "Maximum number of citing papers to return (1-30).",
        },
        "sources": {
            "type": "array",
            "items": {"type": "string", "enum": _SOURCES_ENUM},
            "description": (
                "Optional sources to query. The first source that returns "
                "any result wins; later sources are only tried on empty "
                "results. Default: ['openalex', 'semantic_scholar']."
            ),
        },
    },
    "required": ["paper_id"],
}

GET_REFERENCED_PAPERS_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "paper_id": {
            "type": "string",
            "description": _PAPER_ID_DESCRIPTION,
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 30,
            "description": "Maximum number of referenced papers to return (1-30).",
        },
        "sources": {
            "type": "array",
            "items": {"type": "string", "enum": _SOURCES_ENUM},
            "description": (
                "Optional sources to query. The first source that returns "
                "any result wins; later sources are only tried on empty "
                "results. Default: ['openalex', 'semantic_scholar']."
            ),
        },
    },
    "required": ["paper_id"],
}

SEARCH_BY_AUTHOR_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "author_name": {
            "type": "string",
            "description": (
                "Author name to search for. OpenAlex and arXiv both "
                "tolerate a partial name (last name, or last + first); "
                "for disambiguation, prefer the most distinctive token."
            ),
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 20,
            "description": "Maximum number of papers to return (1-20).",
        },
        "sources": {
            "type": "array",
            "items": {"type": "string", "enum": ["openalex", "arxiv"]},
            "description": (
                "Optional sources to query. OpenAlex's ``author.search`` "
                "filter is the primary path; arXiv is the fallback. "
                "Default: ['openalex', 'arxiv']."
            ),
        },
    },
    "required": ["author_name"],
}


def build_default_registry() -> ToolRegistry:
    """Build a registry pre-populated with the four research tools."""
    reg = ToolRegistry()
    reg.register(
        ToolDefinition(
            name="search_papers",
            description=(
                "Search academic papers by title keywords, DOI, or arXiv ID. "
                "Returns a list of papers with title, authors, year, abstract, "
                "and citation count. Use this whenever the user asks for "
                "literature, references, or papers on a topic. You may also "
                "pass ``filters`` (year_range, min_citations, fields, venues, "
                "sort) to express structured intent; if the underlying source "
                "doesn't honor a given filter, the service falls back to "
                "passing only the rewritten query (so always include the "
                "rewritten keywords in ``query``). The response payload "
                "includes a ``filters_applied`` map so you can tell the user "
                "which constraints actually narrowed the results."
            ),
            parameters=SEARCH_PAPERS_SCHEMA,
            handler=_search_papers_handler,
        )
    )
    reg.register(
        ToolDefinition(
            name="get_paper_details",
            description=(
                "Fetch full metadata (abstract, authors, year, venue, "
                "citation count, DOI/arXiv ID) for a single paper given its "
                "ID. Use after search_papers to retrieve the abstract of a "
                "specific candidate."
            ),
            parameters=GET_PAPER_SCHEMA,
            handler=_get_paper_details_handler,
        )
    )
    reg.register(
        ToolDefinition(
            name="list_project_references",
            description=(
                "List the papers already attached to a writing project so you "
                "can avoid recommending duplicates and so you know which "
                "[@CitationKey] tags are valid."
            ),
            parameters=LIST_REFS_SCHEMA,
            handler=_list_project_references_handler,
        )
    )
    reg.register(
        ToolDefinition(
            name="find_research_gaps",
            description=(
                "Analyze a project's citation graph and return research gaps "
                "(unrefuted claims, sparse regions, broken chains, stale "
                "frontiers). Use when the user asks about research gaps, "
                "open questions, or next-step ideas."
            ),
            parameters=FIND_GAPS_SCHEMA,
            handler=_find_research_gaps_handler,
        )
    )
    reg.register(
        ToolDefinition(
            name="get_citing_papers",
            description=(
                "Backward snowball: return papers that cite a known paper. "
                "Use when (a) the user already names a paper and wants "
                "recent follow-ups, or (b) a keyword search returned no "
                "results and you have one anchor paper in hand. Accepts "
                "DOI, arXiv ID, or OpenAlex ID for the anchor."
            ),
            parameters=GET_CITING_PAPERS_SCHEMA,
            handler=_get_citing_papers_handler,
        )
    )
    reg.register(
        ToolDefinition(
            name="get_referenced_papers",
            description=(
                "Forward snowball: return papers that a known paper "
                "references (its foundation). Use to expose the "
                "methodology or survey lineage behind a paper the user "
                "mentions, or to widen a thin keyword result set by "
                "expanding from an anchor."
            ),
            parameters=GET_REFERENCED_PAPERS_SCHEMA,
            handler=_get_referenced_papers_handler,
        )
    )
    reg.register(
        ToolDefinition(
            name="search_by_author",
            description=(
                "Find papers by an author name. Use when keyword search "
                "returns nothing and the user can name a likely author in "
                "the field (e.g. 'papers by Hinton'). Tries OpenAlex's "
                "``author.search`` filter first; falls back to arXiv."
            ),
            parameters=SEARCH_BY_AUTHOR_SCHEMA,
            handler=_search_by_author_handler,
        )
    )
    return reg


# Module-level singleton: importable, but tests can build their own.
tool_registry: ToolRegistry = build_default_registry()

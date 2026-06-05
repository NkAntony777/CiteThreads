"""
CiteThreads - Pydantic Models
"""
from pydantic import BaseModel, Field
from typing import Dict, List, Optional, Literal, Any
from datetime import datetime
from enum import Enum


class CitationIntent(str, Enum):
    """Citation intent classification"""
    SUPPORT = "SUPPORT"
    OPPOSE = "OPPOSE"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class CitationFunction(str, Enum):
    """Function of the citation"""
    BACKGROUND = "BACKGROUND"
    METHODOLOGY = "METHODOLOGY"
    COMPARISON = "COMPARISON"
    CRITIQUE = "CRITIQUE"
    BASIS = "BASIS"
    UNKNOWN = "UNKNOWN"


class CitationSentiment(str, Enum):
    """Sentiment of the citation"""
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    NEGATIVE = "NEGATIVE"
    UNKNOWN = "UNKNOWN"


# ============ Paper Models ============

class Author(BaseModel):
    """Author information"""
    name: str
    affiliations: List[str] = []


class Paper(BaseModel):
    """Paper/Publication model"""
    id: str = Field(..., description="Unique identifier (S2ID/DOI/arXiv ID)")
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    title: str
    authors: List[str] = []
    year: Optional[int] = None
    venue: Optional[str] = None
    abstract: Optional[str] = None
    citation_count: int = 0
    reference_count: int = 0
    fields: List[str] = []
    url: Optional[str] = None


class CitationEdge(BaseModel):
    """Citation relationship between papers"""
    source: str = Field(..., description="Citing paper ID")
    target: str = Field(..., description="Cited paper ID")
    intent: CitationIntent = CitationIntent.UNKNOWN
    confidence: float = 0.0
    reasoning: Optional[str] = None
    
    # Deep Analysis Fields
    citation_contexts: Optional[List[str]] = []
    citation_function: CitationFunction = CitationFunction.UNKNOWN
    citation_sentiment: CitationSentiment = CitationSentiment.UNKNOWN
    importance_score: int = Field(default=0, ge=0, le=5)
    key_concept: Optional[str] = None


class GraphData(BaseModel):
    """Complete citation graph data"""
    nodes: List[Paper] = []
    edges: List[CitationEdge] = []
    

class GraphStats(BaseModel):
    """Graph statistics"""
    total_nodes: int = 0
    total_edges: int = 0
    year_range: Optional[tuple] = None


# ============ Request/Response Models ============

class PaperSearchRequest(BaseModel):
    """Paper search request"""
    query: str = Field(..., min_length=1, description="Search query (DOI/arXiv ID/title)")
    query_type: Literal["auto", "doi", "arxiv", "title"] = "auto"
    limit: int = Field(default=10, ge=1, le=50)


class PaperSearchResponse(BaseModel):
    """Paper search response"""
    papers: List[Paper]
    total: int


class ProjectCreateRequest(BaseModel):
    """Create project and start preparing hidden writing context.

    The simplified flow (2026-06) only needs a seed paper; depth /
    direction / max_papers are now advanced knobs that the UI no
    longer surfaces. Callers can still pass them for power use.
    """
    seed_paper_id: str = Field(..., description="Seed paper ID to start crawling")
    name: Optional[str] = None
    # Power-use only. UI does not surface these but the router still
    # honors them so the existing ProjectList rename / export flows
    # that round-trip the project keep working.
    depth: int = Field(default=1, ge=1, le=3, description="Crawl depth (1-3)")
    direction: Literal["forward", "backward", "both"] = "both"
    max_papers: int = Field(
        default=30, ge=5, le=200,
        description="Maximum papers to fetch (5-200, default 30)",
    )


class ProjectConfig(BaseModel):
    """Project configuration"""
    seed_paper_id: str
    depth: int = 2
    direction: str = "both"


class ProjectMetadata(BaseModel):
    """Project metadata"""
    id: str
    name: str
    created_at: datetime
    updated_at: datetime
    config: ProjectConfig
    status: Literal["created", "crawling", "analyzing", "completed", "failed"] = "created"
    status_msg: Optional[str] = None
    stats: Optional[GraphStats] = None
    # Owner of the project. Optional for back-compat with projects
    # created before the per-user auth layer landed; in users-file
    # mode the project router enforces that the requesting user must
    # match this value (or None for legacy projects).
    user_id: Optional[str] = None


class ProjectResponse(BaseModel):
    """Full project response with graph data"""
    metadata: ProjectMetadata
    graph: GraphData
    # 2026-06: each project also serves as a chat conversation. The
    # chat history is append-only from the agent runtime's point of
    # view; the client renders it back into a ChatGPT-style thread.
    # Backwards-compat: missing on disk ⇒ empty list.
    chat_history: List["ChatMessage"] = []


class SectionDraft(BaseModel):
    """A single section of a long-form draft produced by CTDP.

    Surfaced to the chat thread as the runtime emits it. Persists
    inside ChatMessage.section_drafts.
    """
    section: str = Field(..., description="One of CTDP's SECTION_NAMES")
    content: str = Field(..., description="Markdown body of the section")
    citations: List[str] = Field(
        default_factory=list,
        description="Citation keys (AuthorYear style) used in the section",
    )


class ChatMessage(BaseModel):
    """One turn in a project's chat history.

    Persisted as part of ``Project.chat_history``. The runtime
    writes user and assistant turns; tool_calls / paper_suggestions
    / section_drafts ride along on the assistant turn so a refresh
    restores the entire thread verbatim.
    """
    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: Optional[datetime] = None
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)
    paper_suggestions: List[Dict[str, Any]] = Field(default_factory=list)
    section_drafts: List[SectionDraft] = Field(default_factory=list)


class ConversationSummary(BaseModel):
    """Lightweight record for the conversation sider list. Avoids
    shipping the full chat history just to render a row."""
    id: str
    name: str
    created_at: datetime
    updated_at: datetime
    status: str
    last_message_preview: Optional[str] = None
    paper_count: int = 0
    section_draft_count: int = 0


class ConversationListResponse(BaseModel):
    items: List[ConversationSummary]


class AnnotationUpdate(BaseModel):
    """Update citation annotation"""
    intent: CitationIntent
    note: Optional[str] = None


class CrawlProgress(BaseModel):
    """Crawl progress update"""
    status: str
    progress: int = 0
    total: int = 0
    message: str = ""
    current_paper: Optional[str] = None


# ============ AI Classification Models ============

class IntentClassificationResult(BaseModel):
    """AI intent classification result"""
    intent: CitationIntent
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str = ""

    # Deep Analysis
    citation_function: CitationFunction = CitationFunction.UNKNOWN
    citation_sentiment: CitationSentiment = CitationSentiment.UNKNOWN
    importance_score: int = 0
    key_concept: Optional[str] = None


class NetworkMetrics(BaseModel):
    """Network analysis results for a project"""
    pagerank: Dict[str, float] = Field(default_factory=dict, description="PageRank score per paper ID")
    betweenness: Dict[str, float] = Field(default_factory=dict, description="Betweenness centrality per paper ID")
    communities: Dict[str, int] = Field(default_factory=dict, description="Community assignment per paper ID")
    community_count: int = 0


class ResearchGapItem(BaseModel):
    """A detected research gap"""
    gap_type: str = Field(..., description="Type: unrefuted_claim, sparse_region, broken_chain, stale_frontier")
    description: str
    paper_ids: List[str] = []
    severity: float = Field(default=0.0, ge=0.0, le=1.0)


class ResearchGapResponse(BaseModel):
    """Research gap detection response"""
    gaps: List[ResearchGapItem] = []
    total: int = 0

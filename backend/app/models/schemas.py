"""
CiteThreads - Pydantic Models
"""
from pydantic import BaseModel, Field
from typing import List, Optional, Literal
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
    """Create project and build graph request"""
    seed_paper_id: str = Field(..., description="Seed paper ID to start crawling")
    depth: int = Field(default=1, ge=1, le=3, description="Crawl depth (1-3)")
    direction: Literal["forward", "backward", "both"] = "both"
    name: Optional[str] = None
    max_papers: int = Field(default=50, ge=10, le=200, description="Maximum papers to fetch")


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


class ProjectResponse(BaseModel):
    """Full project response with graph data"""
    metadata: ProjectMetadata
    graph: GraphData


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

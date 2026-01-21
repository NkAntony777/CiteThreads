"""
Papers API Router - Search and fetch paper metadata
"""
from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List
from pydantic import BaseModel
import re

from ..models import Paper, PaperSearchRequest, PaperSearchResponse
from ..crawlers import semantic_scholar, arxiv, openalex
from ..services.paper_search_service import paper_search_service

router = APIRouter(prefix="/papers", tags=["papers"])


class UnifiedSearchRequest(BaseModel):
    """Request model for unified multi-source search"""
    query: str
    limit: int = 10
    sources: Optional[List[str]] = None


def detect_query_type(query: str) -> str:
    """Auto-detect query type from query string"""
    query = query.strip()
    
    # DOI pattern
    if re.match(r'^10\.\d{4,}/', query):
        return "doi"
    
    # arXiv ID patterns
    if re.match(r'^(arXiv:)?\d{4}\.\d{4,5}(v\d+)?$', query, re.IGNORECASE):
        return "arxiv"
    if "arxiv.org" in query.lower():
        return "arxiv"
    
    # Default to title search
    return "title"

def _tokenize_query(query: str) -> list[str]:
    tokens = re.split(r'\W+', query.lower())
    return [t for t in tokens if len(t) >= 4]


def _filter_title_matches(query: str, papers: list[Paper]) -> list[Paper]:
    tokens = _tokenize_query(query)
    if not tokens:
        return papers
    filtered = []
    for paper in papers:
        title = (paper.title or "").lower()
        if any(token in title for token in tokens):
            filtered.append(paper)
    return filtered


@router.post("/search", response_model=PaperSearchResponse)
async def search_papers(request: PaperSearchRequest):
    """
    Search for papers by DOI, arXiv ID, or title.
    
    - **query**: Search query (DOI, arXiv ID, or title keywords)
    - **query_type**: "auto" (detect), "doi", "arxiv", or "title"
    - **limit**: Maximum number of results (1-50)
    """
    import logging
    logger = logging.getLogger(__name__)
    
    query = request.query.strip()
    query_type = request.query_type
    
    logger.info(f"Search request: query='{query}', type={query_type}")
    
    # Auto-detect query type if needed
    if query_type == "auto":
        query_type = detect_query_type(query)
        logger.info(f"Auto-detected query type: {query_type}")
    
    papers = []
    
    try:
        if query_type == "doi":
            # Prefer OpenAlex for DOI lookup to avoid S2 rate limits
            logger.info(f"Fetching paper by DOI via OpenAlex: {query}")
            paper = await openalex.get_paper_by_id(f"DOI:{query}")
            if paper:
                papers = [paper]
                logger.info(f"Found paper: {paper.title[:50]}...")
            else:
                # Fallback to Semantic Scholar if needed
                logger.info(f"OpenAlex miss, trying S2 DOI: {query}")
                paper = await semantic_scholar.get_paper_by_id(query)
                if paper:
                    papers = [paper]
                    logger.info(f"Found paper: {paper.title[:50]}...")
        
        elif query_type == "arxiv":
            # Try to get by ID first (arXiv free API)
            logger.info(f"Fetching paper by arXiv ID: {query}")
            paper = await arxiv.get_paper_by_id(query)
            if paper:
                papers = [paper]
                logger.info(f"Found paper: {paper.title[:50]}...")
            else:
                # Fall back to arXiv search
                logger.info(f"Searching arXiv for: {query}")
                papers = await arxiv.search_papers(query, limit=request.limit)
                logger.info(f"Found {len(papers)} papers from arXiv search")
        
        else:  # title search
            # Prefer OpenAlex for higher free limits
            logger.info(f"Searching OpenAlex for: {query}")
            papers = await openalex.search_papers(query, limit=request.limit)
            logger.info(f"Found {len(papers)} papers from OpenAlex")

            # Filter for title relevance to reduce unrelated matches
            filtered = _filter_title_matches(query, papers)
            if filtered:
                papers = filtered
            else:
                logger.info("OpenAlex results not relevant, trying arXiv...")
                papers = await arxiv.search_papers(query, limit=request.limit)
                logger.info(f"Found {len(papers)} papers from arXiv")
    
    except Exception as e:
        logger.error(f"Search error: {e}")
        raise
    
    return PaperSearchResponse(
        papers=papers,
        total=len(papers)
    )


@router.post("/search-unified")
async def search_papers_unified(request: UnifiedSearchRequest):
    """
    Unified multi-source paper search.
    Searches across OpenAlex, arXiv, DBLP, and PubMed.
    """
    import logging
    logger = logging.getLogger(__name__)
    
    logger.info(f"Unified search: query='{request.query}', sources={request.sources}")
    
    try:
        result = await paper_search_service.search(
            query=request.query,
            sources=request.sources,
            limit=request.limit
        )
        
        # Convert papers to dict for JSON response
        return {
            "success": True,
            "papers": [p.dict() for p in result.papers],
            "total": result.total,
            "sources_searched": result.sources_searched,
            "errors": result.errors
        }
    except Exception as e:
        logger.error(f"Unified search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{paper_id:path}", response_model=Paper)
async def get_paper(paper_id: str):
    """
    Get paper details by ID.
    
    Supports multiple ID formats:
    - DOI: 10.1038/nature12373
    - Semantic Scholar ID: S2:649def34f8be52c8b66281af98ae884c09aef38b
    - arXiv: arXiv:2106.09685 or 2106.09685
    """
    # Determine source and fetch (avoid S2 unless needed)
    if paper_id.startswith("OpenAlex:"):
        paper = await openalex.get_paper_by_id(paper_id)
    elif paper_id.startswith("arXiv:") or re.match(r'^\d{4}\.\d{4,5}', paper_id):
        paper = await arxiv.get_paper_by_id(paper_id)
        if not paper:
            paper = await openalex.get_paper_by_id(f"arXiv:{paper_id}")
    elif paper_id.startswith("10."):
        paper = await openalex.get_paper_by_id(f"DOI:{paper_id}")
        if not paper:
            paper = await semantic_scholar.get_paper_by_id(paper_id)
    else:
        # Fallback to S2 for unknown IDs
        paper = await semantic_scholar.get_paper_by_id(paper_id)
    
    if not paper:
        raise HTTPException(status_code=404, detail="Paper not found")
    
    return paper

"""
Unified Paper Search Service
Aggregates search results from multiple data sources
"""
import asyncio
import logging
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
from enum import Enum

from ..models import Paper
from ..crawlers import (
    openalex, 
    semantic_scholar, 
    arxiv, 
    dblp_crawler, 
    pubmed_crawler
)

logger = logging.getLogger(__name__)


class SearchSource(str, Enum):
    """Available paper search sources"""
    OPENALEX = "openalex"
    SEMANTIC_SCHOLAR = "semantic_scholar"
    ARXIV = "arxiv"
    DBLP = "dblp"
    PUBMED = "pubmed"


@dataclass
class SearchFilters:
    """Filters for paper search"""
    year_range: Optional[tuple] = None  # (start_year, end_year)
    conferences: Optional[List[str]] = None  # For DBLP
    keywords_all: Optional[List[str]] = None  # For DBLP (AND logic)
    fields: Optional[List[str]] = None  # Research fields
    min_citations: Optional[int] = None


@dataclass
class SearchResult:
    """Aggregated search result"""
    papers: List[Paper] = field(default_factory=list)
    total: int = 0
    sources_searched: List[str] = field(default_factory=list)
    errors: Dict[str, str] = field(default_factory=dict)


class UnifiedPaperSearchService:
    """
    Unified paper search service that aggregates results from multiple sources
    """
    
    DEFAULT_SOURCES = [
        SearchSource.OPENALEX, 
        SearchSource.SEMANTIC_SCHOLAR, 
        SearchSource.ARXIV
    ]
    
    def __init__(self):
        self._dedup_cache: Dict[str, Paper] = {}
    
    async def search(
        self,
        query: str,
        sources: Optional[List[str]] = None,
        filters: Optional[SearchFilters] = None,
        limit: int = 20
    ) -> SearchResult:
        """
        Search papers across multiple sources
        
        Args:
            query: Search query string
            sources: List of source names to search (default: openalex, semantic_scholar, arxiv)
            filters: Optional search filters
            limit: Maximum results per source
            
        Returns:
            SearchResult with aggregated papers
        """
        if not query:
            return SearchResult()
        
        # Parse sources
        if sources:
            search_sources = [SearchSource(s) for s in sources if s in [e.value for e in SearchSource]]
        else:
            search_sources = self.DEFAULT_SOURCES
        
        result = SearchResult(sources_searched=[s.value for s in search_sources])
        
        # Create search tasks for each source
        tasks = []
        for source in search_sources:
            task = self._search_source(source, query, filters, limit)
            tasks.append((source, task))
        
        # Execute searches concurrently
        for source, task in tasks:
            try:
                papers = await task
                result.papers.extend(papers)
                logger.info(f"Found {len(papers)} papers from {source.value}")
            except Exception as e:
                result.errors[source.value] = str(e)
                logger.error(f"Search error from {source.value}: {e}")
        
        # Deduplicate papers
        result.papers = self._deduplicate_papers(result.papers)
        result.total = len(result.papers)
        
        return result
    
    async def _search_source(
        self, 
        source: SearchSource, 
        query: str, 
        filters: Optional[SearchFilters],
        limit: int
    ) -> List[Paper]:
        """Search a specific source"""
        
        if source == SearchSource.OPENALEX:
            return await openalex.search_papers(query, limit=limit)
        
        elif source == SearchSource.ARXIV:
            return await arxiv.search_papers(query, limit=limit)
        
        elif source == SearchSource.DBLP:
            # DBLP requires special handling with keywords
            keywords = query.split()
            conferences = filters.conferences if filters else None
            year_range = filters.year_range if filters else None
            keywords_all = filters.keywords_all if filters else None
            
            return await dblp_crawler.search_papers(
                keywords=keywords,
                keywords_all=keywords_all,
                conferences=conferences,
                year_range=year_range,
                limit=limit
            )
        
        elif source == SearchSource.PUBMED:
            return await pubmed_crawler.search_papers(query, limit=limit)
        
        elif source == SearchSource.SEMANTIC_SCHOLAR:
            # Semantic Scholar currently only supports DOI lookup
            # Fall back to empty for general search
            logger.warning("Semantic Scholar general search not implemented, skipping")
            return []
        
        return []
    
    def _deduplicate_papers(self, papers: List[Paper]) -> List[Paper]:
        """Remove duplicate papers based on DOI or title similarity"""
        seen_dois = set()
        seen_titles = set()
        unique_papers = []
        
        for paper in papers:
            # Check DOI first
            if paper.doi:
                doi_lower = paper.doi.lower()
                if doi_lower in seen_dois:
                    continue
                seen_dois.add(doi_lower)
            
            # Check title similarity (simple normalization)
            title_norm = self._normalize_title(paper.title)
            if title_norm in seen_titles:
                continue
            seen_titles.add(title_norm)
            
            unique_papers.append(paper)
        
        return unique_papers
    
    def _normalize_title(self, title: str) -> str:
        """Normalize title for deduplication"""
        import re
        # Remove punctuation, lowercase, remove extra spaces
        normalized = re.sub(r'[^\w\s]', '', title.lower())
        normalized = ' '.join(normalized.split())
        return normalized
    
    async def search_for_writing(
        self,
        topic: str,
        context: Optional[str] = None,
        limit: int = 10
    ) -> List[Paper]:
        """
        Search papers specifically for AI writing assistance
        Optimized for finding relevant papers to cite
        
        Args:
            topic: Research topic or question
            context: Optional existing document context
            limit: Maximum number of results
            
        Returns:
            List of relevant papers
        """
        # Build enhanced query
        query = topic
        if context:
            # Extract key terms from context (simple approach)
            # In production, could use TF-IDF or LLM extraction
            words = context.split()[:50]  # First 50 words
            key_terms = [w for w in words if len(w) > 5][:5]
            if key_terms:
                query = f"{topic} {' '.join(key_terms)}"
        
        # Search primarily academic sources
        result = await self.search(
            query=query,
            sources=["openalex", "arxiv", "dblp"],
            limit=limit
        )
        
        return result.papers


# Singleton instance
paper_search_service = UnifiedPaperSearchService()

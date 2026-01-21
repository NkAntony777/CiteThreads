"""
Semantic Scholar API Crawler
Documentation: https://api.semanticscholar.org/
"""
import httpx
import logging
import asyncio
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)

S2_BASE_URL = "https://api.semanticscholar.org/graph/v1"

class SemanticScholarCrawler:
    """
    Semantic Scholar API client.
    Used primarily for fetching citation contexts (snippets).
    """
    
    def __init__(self):
        self.base_url = S2_BASE_URL
        self.headers = {
            "User-Agent": "CiteThreads/1.0"
        }
        # S2 Public API limit: 100 requests / 5 minutes (?) or 1 QPS.
        # We use a semaphore to limit concurrency.
        self._sem = asyncio.Semaphore(1) 

    async def _request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Make a rate-limited request to S2 API"""
        async with self._sem:
            url = f"{self.base_url}{endpoint}"
            try:
                # Add a small delay to respect rate limits (1 QPS for unauthenticated)
                await asyncio.sleep(1.0) 
                
                async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                    response = await client.get(url, headers=self.headers, params=params)
                    
                    if response.status_code == 200:
                        return response.json()
                    elif response.status_code == 404:
                        return None
                    elif response.status_code == 429:
                        logger.warning("Semantic Scholar rate limit reached. Backing off.")
                        await asyncio.sleep(5.0)
                        return None
                    else:
                        logger.warning(f"S2 API error {response.status_code}: {response.text}")
                        return None
            except Exception as e:
                logger.error(f"S2 request failed: {e}")
                return None

    async def get_citation_contexts(self, citing_doi: str, cited_doi: str) -> List[str]:
        """
        Fetch citation contexts for a specific pair of papers.
        Uses DOI to lookup papers.
        """
        if not citing_doi or not cited_doi:
            return []

        # 1. Resolve Citing Paper to S2 ID
        # Note: "citations.contexts" is DEPRECATED/REMOVED from S2 API.
        # We can no longer fetch contexts directly via this endpoint easily without bulk API or other methods.
        # Removing "citations.contexts" to fix 400 Error.
        citing_paper = await self.get_paper_by_doi(citing_doi, fields=["citations.paperId", "citations.externalIds"])
        
        if not citing_paper or "citations" not in citing_paper:
            return []
            
        contexts = []
        
        # 2. Iterate through citations to find the Cited Paper
        # Since we can't get contexts content anymore, this method effectively verifies the citation exists
        # and returns empty list for contexts, or we could return a placeholder if needed.
        # For now, we return empty list to avoid breaking the calling code, but logs are clean.
        
        # for citation in citing_paper.get("citations", []):
        #     cited_paper_id = citation.get("paperId")
        #     external_ids = citation.get("externalIds") or {}
        #     citation_doi = external_ids.get("DOI")
        #     
        #     # Check match by DOI (Case insensitive)
        #     if (citation_doi and citation_doi.lower() == cited_doi.lower()):
        #         # S2 no longer returns contexts in this endpoint
        #         break
        
        return []

    async def get_paper_by_doi(self, doi: str, fields: List[str] = None) -> Optional[Dict]:
        """Get paper details by DOI"""
        if not fields:
            fields = ["paperId", "title"]
            
        params = {
            "fields": ",".join(fields)
        }
        # ID can be DOI:xxxx
        return await self._request(f"/paper/DOI:{doi}", params)
    
    async def get_paper_by_id(self, paper_id: str) -> Optional[Any]:
        """Get paper by various ID formats and return as Paper model"""
        from ..models import Paper
        
        # Determine the ID format
        if paper_id.startswith("DOI:"):
            endpoint = f"/paper/{paper_id}"
        elif paper_id.startswith("arXiv:"):
            endpoint = f"/paper/ARXIV:{paper_id[6:]}"
        elif paper_id.startswith("10."):  # DOI without prefix
            endpoint = f"/paper/DOI:{paper_id}"
        else:
            # Assume it's an S2 ID or try as-is
            endpoint = f"/paper/{paper_id}"
        
        fields = ["paperId", "title", "authors", "year", "venue", "abstract", 
                  "citationCount", "referenceCount", "externalIds", "fieldsOfStudy"]
        
        data = await self._request(endpoint, {"fields": ",".join(fields)})
        if not data:
            return None
        
        return self._parse_paper(data)
    
    def _parse_paper(self, data: Dict) -> Any:
        """Parse S2 paper data into Paper model"""
        from ..models import Paper
        
        s2_id = data.get("paperId", "")
        external_ids = data.get("externalIds") or {}
        doi = external_ids.get("DOI")
        arxiv_id = external_ids.get("ArXiv")
        
        authors = [a.get("name", "") for a in data.get("authors", [])]
        
        return Paper(
            id=f"S2:{s2_id}" if s2_id else "",
            doi=doi,
            arxiv_id=arxiv_id,
            title=data.get("title", "Unknown"),
            authors=authors,
            year=data.get("year"),
            venue=data.get("venue", ""),
            abstract=data.get("abstract"),
            citation_count=data.get("citationCount", 0),
            reference_count=data.get("referenceCount", 0),
            fields=data.get("fieldsOfStudy") or [],
            url=f"https://www.semanticscholar.org/paper/{s2_id}" if s2_id else None
        )
    
    async def get_references(self, paper_id: str, limit: int = 100) -> List[Any]:
        """Get papers that this paper references"""
        from ..models import Paper
        
        # Resolve paper ID format
        if paper_id.startswith("DOI:"):
            endpoint = f"/paper/{paper_id}/references"
        elif paper_id.startswith("arXiv:"):
            endpoint = f"/paper/ARXIV:{paper_id[6:]}/references"
        elif paper_id.startswith("10."):
            endpoint = f"/paper/DOI:{paper_id}/references"
        elif paper_id.startswith("S2:"):
            endpoint = f"/paper/{paper_id[3:]}/references"
        elif paper_id.startswith("OpenAlex:"):
            # Try to extract DOI from OpenAlex format - skip for now
            logger.warning(f"S2 cannot directly handle OpenAlex ID: {paper_id}")
            return []
        else:
            endpoint = f"/paper/{paper_id}/references"
        
        fields = ["paperId", "title", "authors", "year", "venue", "citationCount", 
                  "externalIds", "fieldsOfStudy"]
        
        data = await self._request(endpoint, {"fields": ",".join(fields), "limit": limit})
        if not data or "data" not in data:
            return []
        
        papers = []
        for item in data.get("data", []):
            ref_paper = item.get("citedPaper")
            if ref_paper and ref_paper.get("paperId"):
                papers.append(self._parse_paper(ref_paper))
        
        return papers
    
    async def get_citations(self, paper_id: str, limit: int = 100) -> List[Any]:
        """Get papers that cite this paper"""
        from ..models import Paper
        
        # Resolve paper ID format
        if paper_id.startswith("DOI:"):
            endpoint = f"/paper/{paper_id}/citations"
        elif paper_id.startswith("arXiv:"):
            endpoint = f"/paper/ARXIV:{paper_id[6:]}/citations"
        elif paper_id.startswith("10."):
            endpoint = f"/paper/DOI:{paper_id}/citations"
        elif paper_id.startswith("S2:"):
            endpoint = f"/paper/{paper_id[3:]}/citations"
        elif paper_id.startswith("OpenAlex:"):
            logger.warning(f"S2 cannot directly handle OpenAlex ID: {paper_id}")
            return []
        else:
            endpoint = f"/paper/{paper_id}/citations"
        
        fields = ["paperId", "title", "authors", "year", "venue", "citationCount", 
                  "externalIds", "fieldsOfStudy"]
        
        data = await self._request(endpoint, {"fields": ",".join(fields), "limit": limit})
        if not data or "data" not in data:
            return []
        
        papers = []
        for item in data.get("data", []):
            citing_paper = item.get("citingPaper")
            if citing_paper and citing_paper.get("paperId"):
                papers.append(self._parse_paper(citing_paper))
        
        return papers

# Singleton
semantic_scholar = SemanticScholarCrawler()

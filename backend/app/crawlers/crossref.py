"""
Crossref API Crawler
Documentation: https://api.crossref.org/
"""
import httpx
import asyncio
import logging
from typing import List, Optional, Dict, Any
from ..models import Paper
from ..config import settings

logger = logging.getLogger(__name__)

# API endpoints
CROSSREF_BASE_URL = "https://api.crossref.org"

class CrossrefCrawler:
    """Crossref API client for fetching paper metadata by DOI"""
    
    def __init__(self):
        self.base_url = CROSSREF_BASE_URL
        self.headers = {
            "User-Agent": "CiteThreads/1.0 (mailto:citethreads@example.com)"
        }
        self._lock = asyncio.Lock()
    
    async def _request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Make a request to Crossref API"""
        url = f"{self.base_url}{endpoint}"
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(url, headers=self.headers, params=params)
                
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 404:
                    return None
                else:
                    logger.warning(f"Crossref API error {response.status_code}: {response.text}")
                    return None
        except Exception as e:
            logger.error(f"Crossref request failed: {e}")
            return None

    def _parse_paper(self, data: Dict[str, Any]) -> Paper:
        """Parse Crossref work object into Paper model"""
        item = data.get("message", {}) if "message" in data else data
        
        doi = item.get("DOI")
        title_list = item.get("title", [])
        title = title_list[0] if title_list else "Unknown Title"
        
        authors = []
        for author in item.get("author", []):
            given = author.get("given", "")
            family = author.get("family", "")
            name = f"{given} {family}".strip()
            if name:
                authors.append(name)
        
        # Date
        issued = item.get("issued", {}).get("date-parts", [[]])[0]
        year = issued[0] if issued else None
        
        # Venue
        container_title = item.get("container-title", [])
        venue = container_title[0] if container_title else None
        
        # References/Citations
        citation_count = item.get("is-referenced-by-count", 0)
        reference_count = item.get("references-count", 0)
        
        # Abstract (Crossref sometimes provides JATS XML abstract in 'abstract' field, 
        # usually need parsing. For simplicity we skip or clean it lightly)
        abstract = item.get("abstract") # This is often XML string like <jats:p>...</jats:p>
        if abstract:
            import re
            abstract = re.sub(r'<[^>]+>', '', abstract).strip()

        return Paper(
            id=f"DOI:{doi}",
            doi=doi,
            arxiv_id=None, # Crossref generally doesn't link to arXiv
            title=title,
            authors=authors,
            year=year,
            venue=venue,
            abstract=abstract,
            citation_count=citation_count,
            reference_count=reference_count,
            fields=[], # Crossref subjects are often not field-like
            url=item.get("URL")
        )

    async def get_paper_by_doi(self, doi: str) -> Optional[Paper]:
        """Get paper by DOI"""
        clean_doi = doi.replace("DOI:", "").replace("doi:", "")
        data = await self._request(f"/works/{clean_doi}")
        if not data:
            return None
        return self._parse_paper(data)

    async def search_papers(self, query: str, limit: int = 10) -> List[Paper]:
        """Search papers by title/keywords"""
        params = {
            "query": query,
            "rows": limit,
        }
        data = await self._request("/works", params)
        if not data or "message" not in data or "items" not in data["message"]:
            return []
        
        return [self._parse_paper(item) for item in data["message"]["items"]]

# Singleton
crossref = CrossrefCrawler()

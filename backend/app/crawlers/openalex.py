"""
OpenAlex API Crawler
Documentation: https://docs.openalex.org/
"""
import httpx
import asyncio
import logging
from typing import List, Optional, Dict, Any
from ..models import Paper
from ..config import settings

logger = logging.getLogger(__name__)

# API endpoints
OPENALEX_BASE_URL = "https://api.openalex.org"

class OpenAlexCrawler:
    """OpenAlex API client for fetching paper data and citations"""
    
    def __init__(self):
        self.base_url = OPENALEX_BASE_URL
        self.headers = {
            "User-Agent": "CiteThreads/1.0 (mailto:citethreads@example.com)"
        }
        # Provide email in header to get higher rate limit (pool request)
        # You can add email to settings later
        
        self.rate_limit = 10  # Conservative limit
        self._lock = asyncio.Lock()
    
    async def _request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Make a request to OpenAlex API"""
        url = f"{self.base_url}{endpoint}"
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(url, headers={**self.headers, "Accept-Encoding": "gzip, deflate"}, params=params)
                
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 404:
                    return None
                else:
                    logger.warning(f"OpenAlex API error {response.status_code}: {response.text}")
                    return None
        except Exception as e:
            logger.error(f"OpenAlex request failed: {e}")
            return None

    def _parse_paper(self, data: Dict[str, Any]) -> Paper:
        """Parse OpenAlex work object into Paper model"""
        # IDs
        oa_id = data.get("id", "").replace("https://openalex.org/", "")
        ids = data.get("ids", {})
        doi = ids.get("doi", "").replace("https://doi.org/", "") if ids.get("doi") else None
        arxiv_id = None
        if ids.get("arxiv"):
            arxiv_id = ids.get("arxiv", "").replace("https://arxiv.org/abs/", "")
        
        # Authors
        authorships = data.get("authorships", [])
        authors = [a.get("author", {}).get("display_name", "") for a in authorships]
        
        # Abstract (OpenAlex uses inverted index for abstract, need to reconstruction or use None)
        # For now, we leave abstract None as reconstructing it is expensive/complex here
        # Or we can support it later.
        abstract = None 
        # Note: OpenAlex provides abstract_inverted_index. 
        # Reconstructing it:
        inverted = data.get("abstract_inverted_index")
        if inverted:
            abstract_dict = {}
            for word, positions in inverted.items():
                for pos in positions:
                    abstract_dict[pos] = word
            abstract_words = [abstract_dict[i] for i in sorted(abstract_dict.keys())]
            abstract = " ".join(abstract_words)

        # Venue
        primary_location = data.get("primary_location") or {}
        source = primary_location.get("source") or {}
        venue = source.get("display_name", "Unknown Venue")
        
        # Fields
        concepts = data.get("concepts", [])
        fields = [c.get("display_name") for c in concepts if c.get("level") == 0] # Top level fields

        return Paper(
            id=f"OpenAlex:{oa_id}",
            doi=doi,
            arxiv_id=arxiv_id,
            title=data.get("display_name", "Unknown Title"),
            authors=authors,
            year=data.get("publication_year"),
            venue=venue,
            abstract=abstract,
            citation_count=data.get("cited_by_count", 0),
            reference_count=0, # OpenAlex work object doesn't have ref count directly easily accessible sometimes? 
            # Actually referenced_works is a list of IDs.
            fields=fields,
            url=ids.get("doi") or ids.get("url")
        )

    async def search_papers(self, query: str, limit: int = 10) -> List[Paper]:
        """Search papers by title/keywords"""
        params = {
            "filter": f"title.search:{query}",
            "per-page": limit,
        }
        data = await self._request("/works", params)
        if not data or "results" not in data:
            return []
        
        return [self._parse_paper(work) for work in data["results"]]

    async def get_paper_by_id(self, paper_id: str) -> Optional[Paper]:
        """Get paper by OpenAlex ID, DOI, or arXiv ID"""
        # Handle ID types
        if paper_id.startswith("OpenAlex:"):
            api_id = paper_id[9:]
        elif paper_id.startswith("DOI:"):
            api_id = f"doi:{paper_id[4:]}"
        elif paper_id.startswith("arXiv:"):
            # Search by arXiv ID
            # OpenAlex supports filter=arxiv.id:
            arxiv_clean = paper_id[6:]
            params = {"filter": f"arxiv.id:{arxiv_clean}"}
            data = await self._request("/works", params)
            if data and data.get("results"):
                return self._parse_paper(data["results"][0])
            return None
        else:
            api_id = paper_id
            
        data = await self._request(f"/works/{api_id}")
        if not data:
            return None
            
        return self._parse_paper(data)

    async def get_references(self, paper_id: str, limit: int = 100) -> List[Paper]:
        """Get references for a paper (papers this paper cites)"""
        # Resolve paper_id to OpenAlex ID format
        oa_id = await self._resolve_to_openalex_id(paper_id)
        if not oa_id:
            logger.warning(f"Could not resolve paper_id to OpenAlex ID: {paper_id}")
            return []
        
        params = {
            "filter": f"cited_by:{oa_id}",
            "per-page": limit
        }
        data = await self._request("/works", params)
        if not data or "results" not in data:
            return []
        
        return [self._parse_paper(p) for p in data["results"]]

    async def get_citations(self, paper_id: str, limit: int = 100) -> List[Paper]:
        """Get papers that cite this paper"""
        # Resolve paper_id to OpenAlex ID format
        oa_id = await self._resolve_to_openalex_id(paper_id)
        if not oa_id:
            logger.warning(f"Could not resolve paper_id to OpenAlex ID: {paper_id}")
            return []

        params = {
            "filter": f"cites:{oa_id}",
            "per-page": limit
        }
        data = await self._request("/works", params)
        if not data or "results" not in data:
            return []
        
        return [self._parse_paper(p) for p in data["results"]]
    
    async def _resolve_to_openalex_id(self, paper_id: str) -> Optional[str]:
        """Resolve various ID formats to OpenAlex work ID (W...)"""
        # Already OpenAlex ID
        if paper_id.startswith("OpenAlex:"):
            oa_id = paper_id[9:]
            if oa_id.startswith("W"):
                return oa_id
            return None
        
        # Already a raw OpenAlex ID (W...)
        if paper_id.startswith("W"):
            return paper_id
        
        # DOI format
        if paper_id.startswith("DOI:"):
            doi = paper_id[4:]
        elif paper_id.startswith("10."):
            doi = paper_id
        else:
            # Unknown format, can't resolve
            logger.debug(f"Unknown paper_id format, cannot resolve: {paper_id}")
            return None
        
        # Lookup by DOI to get OpenAlex ID
        data = await self._request(f"/works/doi:{doi}")
        if data and data.get("id"):
            oa_id = data.get("id", "").replace("https://openalex.org/", "")
            logger.info(f"Resolved DOI {doi} to OpenAlex ID {oa_id}")
            return oa_id
        return None

# Singleton
openalex = OpenAlexCrawler()

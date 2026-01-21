"""
arXiv API Crawler
Documentation: https://info.arxiv.org/help/api/index.html
"""
import httpx
import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from typing import List, Optional
from ..models import Paper
from ..config import settings

logger = logging.getLogger(__name__)

ARXIV_API_URL = "https://export.arxiv.org/api/query"
ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


class ArxivCrawler:
    """arXiv API client for fetching paper metadata"""
    
    def __init__(self):
        # arXiv rate limit: 1 request per 3 seconds
        self.rate_limit_delay = 1.0 / settings.arxiv_rate_limit
        self._last_request = 0.0
        self._lock = asyncio.Lock()
    
    async def _rate_limit_wait(self):
        """Ensure we don't exceed rate limit"""
        async with self._lock:
            import time
            now = time.time()
            elapsed = now - self._last_request
            if elapsed < self.rate_limit_delay:
                await asyncio.sleep(self.rate_limit_delay - elapsed)
            self._last_request = time.time()
    
    def _extract_arxiv_id(self, id_str: str) -> str:
        """Extract clean arXiv ID from various formats"""
        # Remove URL prefix if present
        if "arxiv.org" in id_str:
            match = re.search(r'abs/(\d{4}\.\d{4,5}(?:v\d+)?)', id_str)
            if match:
                return match.group(1)
        
        # Remove "arXiv:" prefix
        if id_str.lower().startswith("arxiv:"):
            id_str = id_str[6:]
        
        # Extract just the ID (e.g., "2106.09685" or "2106.09685v2")
        match = re.match(r'(\d{4}\.\d{4,5}(?:v\d+)?)', id_str)
        if match:
            return match.group(1)
        
        return id_str
    
    def _parse_entry(self, entry: ET.Element) -> Paper:
        """Parse arXiv Atom entry into Paper model"""
        # Get ID
        id_elem = entry.find("atom:id", ARXIV_NS)
        full_id = id_elem.text if id_elem is not None else ""
        arxiv_id = self._extract_arxiv_id(full_id)
        
        # Get title (remove extra whitespace)
        title_elem = entry.find("atom:title", ARXIV_NS)
        title = " ".join((title_elem.text or "").split()) if title_elem is not None else "Unknown"
        
        # Get authors
        authors = []
        for author in entry.findall("atom:author", ARXIV_NS):
            name = author.find("atom:name", ARXIV_NS)
            if name is not None and name.text:
                authors.append(name.text)
        
        # Get abstract
        summary_elem = entry.find("atom:summary", ARXIV_NS)
        abstract = " ".join((summary_elem.text or "").split()) if summary_elem is not None else None
        
        # Get publication date (year)
        published_elem = entry.find("atom:published", ARXIV_NS)
        year = None
        if published_elem is not None and published_elem.text:
            year = int(published_elem.text[:4])
        
        # Get categories/fields
        fields = []
        for category in entry.findall("arxiv:primary_category", ARXIV_NS):
            term = category.get("term")
            if term:
                fields.append(term)
        for category in entry.findall("atom:category", ARXIV_NS):
            term = category.get("term")
            if term and term not in fields:
                fields.append(term)
        
        # Get DOI if available
        doi = None
        doi_elem = entry.find("arxiv:doi", ARXIV_NS)
        if doi_elem is not None:
            doi = doi_elem.text
        
        return Paper(
            id=f"arXiv:{arxiv_id}",
            arxiv_id=arxiv_id,
            doi=doi,
            title=title,
            authors=authors,
            year=year,
            venue="arXiv",
            abstract=abstract,
            citation_count=0,  # arXiv doesn't provide this
            fields=fields,
            url=f"https://arxiv.org/abs/{arxiv_id}"
        )
    
    async def search_papers(self, query: str, limit: int = 10) -> List[Paper]:
        """Search arXiv papers by query"""
        await self._rate_limit_wait()
        
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": limit,
            "sortBy": "relevance",
            "sortOrder": "descending"
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(ARXIV_API_URL, params=params)
                
                if response.status_code != 200:
                    logger.error(f"arXiv API error: {response.status_code}")
                    return []
                
                root = ET.fromstring(response.text)
                papers = []
                
                for entry in root.findall("atom:entry", ARXIV_NS):
                    try:
                        paper = self._parse_entry(entry)
                        papers.append(paper)
                    except Exception as e:
                        logger.warning(f"Failed to parse entry: {e}")
                
                return papers
                
        except Exception as e:
            logger.error(f"arXiv search failed: {e}")
            return []
    
    async def get_paper_by_id(self, arxiv_id: str) -> Optional[Paper]:
        """Get paper by arXiv ID"""
        await self._rate_limit_wait()
        
        clean_id = self._extract_arxiv_id(arxiv_id)
        
        params = {
            "id_list": clean_id,
            "max_results": 1
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(ARXIV_API_URL, params=params)
                
                if response.status_code != 200:
                    logger.error(f"arXiv API error: {response.status_code}")
                    return None
                
                root = ET.fromstring(response.text)
                entries = root.findall("atom:entry", ARXIV_NS)
                
                if not entries:
                    return None
                
                # Check if it's an error response (no title)
                title = entries[0].find("atom:title", ARXIV_NS)
                if title is None or not title.text or title.text.strip() == "Error":
                    return None
                
                return self._parse_entry(entries[0])
                
        except Exception as e:
            logger.error(f"arXiv get paper failed: {e}")
            return None


# Singleton instance
arxiv = ArxivCrawler()

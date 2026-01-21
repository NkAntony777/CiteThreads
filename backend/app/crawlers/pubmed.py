"""
PubMed API Crawler
For medical/biological literature search
Documentation: https://www.ncbi.nlm.nih.gov/books/NBK25500/
"""
import httpx
import asyncio
import logging
import xml.etree.ElementTree as ET
from typing import List, Optional, Dict, Any
from ..models import Paper

logger = logging.getLogger(__name__)

# PubMed E-utilities base URLs
ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"


class PubMedCrawler:
    """
    PubMed API client for medical/biological literature search
    """
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize PubMed crawler
        
        Args:
            api_key: Optional NCBI API key for higher rate limits
                    Without key: 3 requests/second
                    With key: 10 requests/second
        """
        self.api_key = api_key
        self.headers = {
            "User-Agent": "CiteThreads/1.0 (Academic Research Tool)"
        }
        self._sem = asyncio.Semaphore(3)  # Limit concurrent requests
    
    async def _request(self, url: str, params: Dict[str, Any]) -> Optional[str]:
        """Make a rate-limited request to PubMed"""
        async with self._sem:
            try:
                # Add API key if available
                if self.api_key:
                    params["api_key"] = self.api_key
                
                # Add delay to respect rate limits
                await asyncio.sleep(0.35)
                
                async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                    response = await client.get(url, params=params, headers=self.headers)
                    
                    if response.status_code == 200:
                        return response.text
                    else:
                        logger.warning(f"PubMed request failed {response.status_code}: {url}")
                        return None
            except Exception as e:
                logger.error(f"PubMed request error: {e}")
                return None
    
    async def search_papers(
        self,
        query: str,
        limit: int = 20,
        sort: str = "relevance"
    ) -> List[Paper]:
        """
        Search papers from PubMed
        
        Args:
            query: Search query string (supports PubMed query syntax)
            limit: Maximum number of results
            sort: Sort order - 'relevance', 'pub_date', 'first_author'
            
        Returns:
            List of Paper objects
        """
        if not query:
            return []
        
        # Map sort options
        sort_map = {
            "relevance": "relevance",
            "pub_date": "pub_date",
            "first_author": "first_author"
        }
        
        # Step 1: Search for PMIDs
        search_params = {
            "db": "pubmed",
            "term": query,
            "retmax": limit,
            "retmode": "json",
            "sort": sort_map.get(sort, "relevance"),
        }
        
        search_result = await self._request(ESEARCH_URL, search_params)
        if not search_result:
            return []
        
        try:
            import json
            data = json.loads(search_result)
            pmids = data.get("esearchresult", {}).get("idlist", [])
            
            if not pmids:
                logger.info(f"No results found for PubMed query: {query}")
                return []
            
            logger.info(f"Found {len(pmids)} PMIDs from PubMed search")
            
        except Exception as e:
            logger.error(f"Failed to parse PubMed search result: {e}")
            return []
        
        # Step 2: Fetch paper details
        return await self._fetch_papers_by_pmids(pmids)
    
    async def _fetch_papers_by_pmids(self, pmids: List[str]) -> List[Paper]:
        """Fetch paper details for a list of PMIDs"""
        if not pmids:
            return []
        
        fetch_params = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "xml",
            "rettype": "abstract",
        }
        
        xml_result = await self._request(EFETCH_URL, fetch_params)
        if not xml_result:
            return []
        
        try:
            return self._parse_pubmed_xml(xml_result)
        except Exception as e:
            logger.error(f"Failed to parse PubMed XML: {e}")
            return []
    
    def _parse_pubmed_xml(self, xml_content: str) -> List[Paper]:
        """Parse PubMed XML response into Paper objects"""
        papers = []
        
        try:
            root = ET.fromstring(xml_content)
            
            for article in root.findall(".//PubmedArticle"):
                try:
                    # Extract PMID
                    pmid_elem = article.find(".//PMID")
                    pmid = pmid_elem.text if pmid_elem is not None else None
                    
                    if not pmid:
                        continue
                    
                    # Extract title
                    title_elem = article.find(".//ArticleTitle")
                    title = title_elem.text if title_elem is not None else "Unknown Title"
                    
                    # Extract authors
                    authors = []
                    for author in article.findall(".//Author"):
                        lastname = author.find("LastName")
                        forename = author.find("ForeName")
                        if lastname is not None and forename is not None:
                            authors.append(f"{forename.text} {lastname.text}")
                        elif lastname is not None:
                            authors.append(lastname.text)
                    
                    # Extract year
                    year = None
                    pub_date = article.find(".//PubDate")
                    if pub_date is not None:
                        year_elem = pub_date.find("Year")
                        if year_elem is not None:
                            try:
                                year = int(year_elem.text)
                            except ValueError:
                                pass
                    
                    # Extract journal
                    journal_elem = article.find(".//Journal/Title")
                    venue = journal_elem.text if journal_elem is not None else None
                    
                    # Extract abstract
                    abstract_parts = []
                    for abstract_text in article.findall(".//AbstractText"):
                        if abstract_text.text:
                            label = abstract_text.get("Label", "")
                            if label:
                                abstract_parts.append(f"{label}: {abstract_text.text}")
                            else:
                                abstract_parts.append(abstract_text.text)
                    abstract = " ".join(abstract_parts) if abstract_parts else None
                    
                    # Extract DOI
                    doi = None
                    for article_id in article.findall(".//ArticleId"):
                        if article_id.get("IdType") == "doi":
                            doi = article_id.text
                            break
                    
                    # Extract MeSH terms as fields
                    fields = []
                    for mesh in article.findall(".//MeshHeading/DescriptorName"):
                        if mesh.text:
                            fields.append(mesh.text)
                    
                    paper = Paper(
                        id=f"pmid:{pmid}",
                        doi=doi,
                        title=title,
                        authors=authors,
                        year=year,
                        venue=venue,
                        abstract=abstract,
                        citation_count=0,
                        reference_count=0,
                        fields=fields[:10],  # Limit fields
                        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    )
                    papers.append(paper)
                    
                except Exception as e:
                    logger.warning(f"Failed to parse article: {e}")
                    continue
                    
        except ET.ParseError as e:
            logger.error(f"XML parse error: {e}")
        
        return papers
    
    async def get_paper_by_pmid(self, pmid: str) -> Optional[Paper]:
        """
        Get a single paper by PMID
        
        Args:
            pmid: PubMed ID (e.g., "12345678")
            
        Returns:
            Paper object or None if not found
        """
        # Clean PMID
        pmid = pmid.strip().replace("pmid:", "").replace("PMID:", "")
        
        papers = await self._fetch_papers_by_pmids([pmid])
        return papers[0] if papers else None
    
    def configure(self, api_key: str):
        """Configure the crawler with an API key"""
        self.api_key = api_key
        logger.info("PubMed API key configured")


# Singleton instance
pubmed_crawler = PubMedCrawler()

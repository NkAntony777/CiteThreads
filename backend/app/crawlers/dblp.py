"""
DBLP Conference Paper Crawler
Based on PaperHunter (https://github.com/ahlien/PaperHunter)
Refactored to async implementation for CiteThreads
"""
import httpx
import asyncio
import logging
import re
from typing import List, Optional, Dict, Tuple, Any
from bs4 import BeautifulSoup
from ..models import Paper

logger = logging.getLogger(__name__)

# Configuration constants
MAX_SUBPAGE = 5  # Maximum number of subpages to check
DELAY_RANGE = (0.5, 1.5)  # Request interval range in seconds

# Mapping from common abbreviations to internal keys
COMMON_TO_KNOWN = {
    "socc": "cloud",
    "hipeac": "hipc",
    "pact": "ieeepact",
    "ipdps": "ipps",
    "msst": "mss",
    "fse": "sigsoft",
    "atc": "usenix",
    "security": "uss",
    "csf": "csfw",
    "ase": "kbse",
    "scheme": "icfp",
    "lctes": "lctrts",
    "icsme": "icsm",
    "sigsoft": "fse_esec",
    "iswc": "semweb",
    "hscc": "hybrid",
    "neurips": "nips",
    "aamas": "atal",
    "fg": "fgr",
    "ijcb": "icb",
    "iss": "tabletop",
    "iccv": "iccvw",
    "ubicomp": "huc"
}

# Known conferences list - 300+ CCF conferences
# Format: abbreviation -> (full_name, dblp_key)
KNOWN_CONFS = {
    # Computer Architecture/Parallel & Distributed Computing/Storage Systems - Class A
    "ppopp": ("ACM SIGPLAN Symposium on Principles & Practice of Parallel Programming", "ppopp"),
    "fast": ("USENIX Conference on File and Storage Technologies", "fast"),
    "dac": ("Design Automation Conference", "dac"),
    "hpca": ("IEEE International Symposium on High Performance Computer Architecture", "hpca"),
    "micro": ("IEEE/ACM International Symposium on Microarchitecture", "micro"),
    "sc": ("International Conference for High Performance Computing, Networking, Storage, and Analysis", "sc"),
    "asplos": ("International Conference on Architectural Support for Programming Languages and Operating Systems", "asplos"),
    "isca": ("International Symposium on Computer Architecture", "isca"),
    "usenix": ("USENIX Annual Technical Conference", "usenix"),
    "eurosys": ("European Conference on Computer Systems", "eurosys"),

    # Computer Networks - Class A
    "sigcomm": ("ACM International Conference on Applications, Technologies, Architectures, and Protocols for Computer Communication", "sigcomm"),
    "mobicom": ("ACM International Conference on Mobile Computing and Networking", "mobicom"),
    "infocom": ("IEEE International Conference on Computer Communications", "infocom"),
    "nsdi": ("Symposium on Network System Design and Implementation", "nsdi"),

    # Network & Information Security - Class A
    "ccs": ("ACM Conference on Computer and Communications Security", "ccs"),
    "eurocrypt": ("International Conference on the Theory and Applications of Cryptographic Techniques", "eurocrypt"),
    "sp": ("IEEE Symposium on Security and Privacy", "sp"),
    "crypto": ("International Cryptology Conference", "crypto"),
    "uss": ("USENIX Security Symposium", "uss"),
    "ndss": ("Network and Distributed System Security Symposium", "ndss"),

    # Software Engineering/System Software/Programming Languages - Class A
    "pldi": ("ACM SIGPLAN Conference on Programming Language Design and Implementation", "pldi"),
    "popl": ("ACM SIGPLAN-SIGACT Symposium on Principles of Programming Languages", "popl"),
    "fse_esec": ("ACM Joint European Software Engineering Conference and Symposium on the Foundations of Software Engineering", "sigsoft"),
    "sosp": ("ACM Symposium on Operating Systems Principles", "sosp"),
    "oopsla": ("Conference on Object-Oriented Programming Systems, Languages, and Applications", "oopsla"),
    "kbse": ("International Conference on Automated Software Engineering", "ase"),
    "icse": ("International Conference on Software Engineering", "icse"),
    "issta": ("International Symposium on Software Testing and Analysis", "issta"),
    "osdi": ("USENIX Symposium on Operating Systems Design and Implementation", "osdi"),
    "fm": ("International Symposium on Formal Methods", "fm"),

    # Databases/Data Mining/Content Retrieval - Class A
    "sigmod": ("ACM SIGMOD Conference", "sigmod"),
    "kdd": ("ACM SIGKDD Conference on Knowledge Discovery and Data Mining", "kdd"),
    "icde": ("IEEE International Conference on Data Engineering", "icde"),
    "sigir": ("International ACM SIGIR Conference on Research and Development in Information Retrieval", "sigir"),
    "vldb": ("International Conference on Very Large Data Bases", "vldb"),

    # Computer Science Theory - Class A
    "stoc": ("ACM Symposium on the Theory of Computing", "stoc"),
    "soda": ("ACM-SIAM Symposium on Discrete Algorithms", "soda"),
    "cav": ("International Conference on Computer Aided Verification", "cav"),
    "focs": ("IEEE Annual Symposium on Foundations of Computer Science", "focs"),
    "lics": ("ACM/IEEE Symposium on Logic in Computer Science", "lics"),

    # Computer Graphics & Multimedia - Class A
    "mm": ("ACM International Conference on Multimedia", "mm"),
    "siggraph": ("ACM Special Interest Group on Computer Graphics", "siggraph"),
    "vr": ("IEEE Virtual Reality", "vr"),
    "visualization": ("IEEE Visualization Conference", "visualization"),

    # Artificial Intelligence - Class A
    "aaai": ("AAAI Conference on Artificial Intelligence", "aaai"),
    "nips": ("Conference on Neural Information Processing Systems", "neurips"),
    "acl": ("Annual Meeting of the Association for Computational Linguistics", "acl"),
    "cvpr": ("IEEE/CVF Computer Vision and Pattern Recognition Conference", "cvpr"),
    "iccvw": ("International Conference on Computer Vision", "iccvw"),
    "icml": ("International Conference on Machine Learning", "icml"),
    "ijcai": ("International Joint Conference on Artificial Intelligence", "ijcai"),

    # Artificial Intelligence - Class B
    "colt": ("Annual Conference on Computational Learning Theory", "colt"),
    "emnlp": ("Conference on Empirical Methods in Natural Language Processing", "emnlp"),
    "ecai": ("European Conference on Artificial Intelligence", "ecai"),
    "eccv": ("European Conference on Computer Vision", "eccv"),
    "icra": ("IEEE International Conference on Robotics and Automation", "icra"),
    "icaps": ("International Conference on Automated Planning and Scheduling", "icaps"),
    "iccbr": ("International Conference on Case-Based Reasoning", "iccbr"),
    "coling": ("International Conference on Computational Linguistics", "coling"),
    "kr": ("International Conference on Principles of Knowledge Representation and Reasoning", "kr"),
    "uai": ("Conference on Uncertainty in Artificial Intelligence", "uai"),
    "atal": ("International Joint Conference on Autonomous Agents and Multi-agent Systems", "aamas"),
    "ppsn": ("Parallel Problem Solving from Nature", "ppsn"),
    "naacl": ("North American Chapter of the Association for Computational Linguistics", "naacl"),

    # Artificial Intelligence - Class C
    "aistats": ("International Conference on Artificial Intelligence and Statistics", "aistats"),
    "accv": ("Asian Conference on Computer Vision", "accv"),
    "acml": ("Asian Conference on Machine Learning", "acml"),
    "bmvc": ("British Machine Vision Conference", "bmvc"),
    "nlpcc": ("CCF International Conference on Natural Language Processing and Chinese Computing", "nlpcc"),
    "conll": ("Conference on Computational Natural Language Learning", "conll"),
    "gecco": ("Genetic and Evolutionary Computation Conference", "gecco"),
    "ictai": ("IEEE International Conference on Tools with Artificial Intelligence", "ictai"),
    "iros": ("IEEE/RSJ International Conference on Intelligent Robots and Systems", "iros"),
    "alt": ("International Conference on Algorithmic Learning Theory", "alt"),
    "icann": ("International Conference on Artificial Neural Networks", "icann"),
    "fgr": ("IEEE International Conference on Automatic Face and Gesture Recognition", "fg"),
    "icdar": ("International Conference on Document Analysis and Recognition", "icdar"),
    "ilp": ("International Conference on Inductive Logic Programming", "ilp"),
    "ksem": ("International Conference on Knowledge Science, Engineering and Management", "ksem"),
    "iconip": ("International Conference on Neural Information Processing", "iconip"),
    "icpr": ("International Conference on Pattern Recognition", "icpr"),
    "icb": ("International Joint Conference on Biometrics", "ijcb"),
    "ijcnn": ("International Joint Conference on Neural Networks", "ijcnn"),
    "pricai": ("Pacific Rim International Conference on Artificial Intelligence", "pricai"),

    # Databases/Data Mining - Class B
    "cikm": ("ACM International Conference on Information and Knowledge Management", "cikm"),
    "wsdm": ("ACM International Conference on Web Search and Data Mining", "wsdm"),
    "pods": ("ACM SIGMOD-SIGACT-SIGAI Symposium on Principles of Database Systems", "pods"),
    "dasfaa": ("International Conference on Database Systems for Advanced Applications", "dasfaa"),
    "pkdd": ("European Conference on Machine Learning and Principles and Practice of Knowledge Discovery in Databases", "pkdd"),
    "semweb": ("IEEE International Semantic Web Conference", "iswc"),
    "icdm": ("IEEE International Conference on Data Mining", "icdm"),
    "icdt": ("International Conference on Database Theory", "icdt"),
    "edbt": ("International Conference on Extending Database Technology", "edbt"),
    "cidr": ("Conference on Innovative Data Systems Research", "cidr"),
    "sdm": ("SIAM International Conference on Data Mining", "sdm"),
    "recsys": ("ACM Conference on Recommender Systems", "recsys"),

    # Human-Computer Interaction & Ubiquitous Computing - Class A
    "huc": ("ACM International Joint Conference on Pervasive and Ubiquitous Computing", "ubicomp"),
    "uist": ("ACM Symposium on User Interface Software and Technology", "uist"),

    # Interdisciplinary/Comprehensive/Emerging - Class A
    "www": ("International World Wide Web Conference", "www"),
    "rtss": ("IEEE Real-Time Systems Symposium", "rtss"),
    "wine": ("Conference on Web and Internet Economics", "wine"),

    # Interdisciplinary - Class B
    "cogsci": ("Annual Meeting of the Cognitive Science Society", "cogsci"),
    "bibm": ("IEEE International Conference on Bioinformatics and Biomedicine", "bibm"),
    "emsoft": ("International Conference on Embedded Software", "emsoft"),
    "ismb": ("International Conference on Intelligent Systems for Molecular Biology", "biolink"),
    "recomb": ("Annual International Conference on Research in Computational Molecular Biology", "recomb"),
    "miccai": ("International Conference on Medical Image Computing and Computer-Assisted Intervention", "miccai"),
}

# Special suffix mappings for specific conferences with non-standard URL formats
SPECIAL_SUFFIX = {
    "vldb": "w",
    "sca": "p",
    "ubicomp": "ap",
    "eurovis": "short",
    "sgp": "p",
    "egsr": "st",
    "pg": "s",
}


class DBLPCrawler:
    """
    DBLP Conference Paper Crawler
    Supports 300+ CCF conferences with keyword filtering
    """
    
    def __init__(self):
        self.base_url = "https://dblp.org/db/conf"
        self.headers = {
            "User-Agent": "CiteThreads/1.0 (Academic Research Tool)"
        }
        self._sem = asyncio.Semaphore(2)  # Limit concurrent requests
    
    async def _request(self, url: str) -> Optional[str]:
        """Make a rate-limited request to DBLP"""
        async with self._sem:
            try:
                # Add delay to respect rate limits
                await asyncio.sleep(0.5)
                
                async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                    response = await client.get(url, headers=self.headers)
                    
                    if response.status_code == 200:
                        return response.text
                    elif response.status_code == 404:
                        return None
                    else:
                        logger.warning(f"DBLP request failed {response.status_code}: {url}")
                        return None
            except Exception as e:
                logger.error(f"DBLP request error: {e}")
                return None
    
    async def _check_conf_pages(self, short: str, key: str, year: int) -> List[str]:
        """Check and return valid conference pages for a given year from DBLP"""
        base_url = f"{self.base_url}/{short}/"
        
        # 1. Try main conference page (e.g., icml2024.html)
        main_url = f"{base_url}{key}{year}.html"
        content = await self._request(main_url)
        if content:
            return [main_url]
        
        # 2. Try sub-pages (e.g., icml2024-1.html)
        found = []
        for i in range(1, MAX_SUBPAGE + 1):
            sub_url = f"{base_url}{key}{year}-{i}.html"
            content = await self._request(sub_url)
            if content:
                found.append(sub_url)
            else:
                break
        
        if found:
            return found
        
        # 3. Try special suffix pages for conferences with unique URL patterns
        if key in SPECIAL_SUFFIX:
            suffix = SPECIAL_SUFFIX[key]
            special_url = f"{base_url}{key}{year}{suffix}.html"
            content = await self._request(special_url)
            if content:
                return [special_url]
        
        return []
    
    async def _fetch_papers_from_page(self, url: str) -> List[Dict[str, Any]]:
        """Extract paper information from a DBLP conference page"""
        content = await self._request(url)
        if not content:
            return []
        
        papers = []
        soup = BeautifulSoup(content, "html.parser")
        
        for cite in soup.find_all("cite", class_="data"):
            title_tag = cite.find("span", class_="title")
            if not title_tag or not title_tag.text:
                continue
            
            title = title_tag.text.strip()
            
            # Extract authors
            authors = []
            for author_tag in cite.find_all("span", itemprop="author"):
                name_tag = author_tag.find("span", itemprop="name")
                if name_tag:
                    authors.append(name_tag.text.strip())
            
            # Extract DOI if available
            doi = None
            doi_tag = cite.find("a", href=re.compile(r"doi\.org"))
            if doi_tag:
                doi_match = re.search(r"doi\.org/(.+)$", doi_tag.get("href", ""))
                if doi_match:
                    doi = doi_match.group(1)
            
            papers.append({
                "title": title,
                "authors": authors,
                "doi": doi,
            })
        
        return papers
    
    async def search_papers(
        self,
        keywords: List[str],
        keywords_all: List[str] = None,
        conferences: List[str] = None,
        year_range: Tuple[int, int] = None,
        limit: int = 50
    ) -> List[Paper]:
        """
        Search papers from DBLP conferences
        
        Args:
            keywords: Keywords (at least one must match - OR logic)
            keywords_all: Keywords that must all match (AND logic)
            conferences: List of conference abbreviations (e.g., ['neurips', 'icml'])
                        Use None for all conferences
            year_range: Tuple of (start_year, end_year), e.g., (2020, 2024)
            limit: Maximum number of papers to return
            
        Returns:
            List of Paper objects matching the criteria
        """
        if not keywords and not keywords_all:
            logger.warning("No keywords provided for DBLP search")
            return []
        
        # Resolve conference names
        conf_keys = []
        if conferences:
            for conf in conferences:
                conf_lower = conf.lower()
                # Check mapping first
                internal = COMMON_TO_KNOWN.get(conf_lower, conf_lower)
                if internal in KNOWN_CONFS:
                    conf_keys.append((internal, KNOWN_CONFS[internal]))
                else:
                    logger.warning(f"Unknown conference: {conf}")
        else:
            # Use all known conferences (too slow, limit to common AI ones)
            common_ai_confs = ["nips", "icml", "cvpr", "acl", "emnlp", "aaai", "ijcai"]
            for key in common_ai_confs:
                if key in KNOWN_CONFS:
                    conf_keys.append((key, KNOWN_CONFS[key]))
        
        # Determine year range
        if year_range:
            years = list(range(year_range[0], year_range[1] + 1))
        else:
            years = [2024, 2023, 2022]  # Default to recent years
        
        # Fetch papers from all conference/year combinations
        all_papers = []
        for conf_short, (full_name, dblp_key) in conf_keys:
            for year in years:
                logger.info(f"Searching {full_name} {year}...")
                
                pages = await self._check_conf_pages(conf_short, dblp_key, year)
                for page_url in pages:
                    papers = await self._fetch_papers_from_page(page_url)
                    
                    # Filter by keywords
                    for paper_data in papers:
                        title = paper_data["title"]
                        
                        # Check AND keywords (all must match)
                        if keywords_all:
                            if not all(re.search(kw, title, re.IGNORECASE) for kw in keywords_all):
                                continue
                        
                        # Check OR keywords (at least one must match)
                        if keywords:
                            if not any(re.search(kw, title, re.IGNORECASE) for kw in keywords):
                                continue
                        
                        # Create Paper object
                        paper = Paper(
                            id=paper_data.get("doi") or f"dblp:{conf_short}{year}:{hash(title)}",
                            title=title,
                            authors=paper_data.get("authors", []),
                            year=year,
                            venue=full_name,
                            doi=paper_data.get("doi"),
                            citation_count=0,
                            reference_count=0,
                            fields=[],
                        )
                        all_papers.append(paper)
                        
                        if len(all_papers) >= limit:
                            logger.info(f"Reached limit of {limit} papers")
                            return all_papers
        
        logger.info(f"Found {len(all_papers)} papers from DBLP")
        return all_papers
    
    def get_supported_conferences(self) -> Dict[str, str]:
        """Return dict of supported conference abbreviations and their full names"""
        return {key: info[0] for key, info in KNOWN_CONFS.items()}
    
    def resolve_conference_name(self, name: str) -> Optional[Tuple[str, str]]:
        """
        Resolve a conference name to its DBLP key
        Returns (abbreviation, (full_name, dblp_key)) or None if not found
        """
        name_lower = name.lower()
        internal = COMMON_TO_KNOWN.get(name_lower, name_lower)
        if internal in KNOWN_CONFS:
            return (internal, KNOWN_CONFS[internal])
        return None


# Singleton instance
dblp_crawler = DBLPCrawler()

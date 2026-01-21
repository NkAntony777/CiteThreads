"""
Graph Builder Service - Multi-source citation network with smart sorting
Supports: Semantic Scholar (primary) + arXiv (fallback)
"""
import asyncio
import logging
from typing import Optional, Callable, List
from datetime import datetime

from ..models import (
    Paper, CitationEdge, CitationIntent, GraphData, GraphStats,
    ProjectMetadata, ProjectConfig, CrawlProgress
)
from ..crawlers import semantic_scholar, arxiv, openalex, crossref
from .ai_classifier import intent_classifier

logger = logging.getLogger(__name__)


# Configuration limits
DEFAULT_MAX_PAPERS = 30  # Reduced default for better UX
MAX_REFS_PER_PAPER = 10  # Reduced per-paper limit
MAX_CITES_PER_PAPER = 10
API_DELAY_SECONDS = 0.8  # Increased delay to avoid rate limits


class RateLimitStatus:
    """Track rate limit status for each data source"""
    def __init__(self):
        self.s2_limited = False
        self.openalex_limited = False
        self.arxiv_limited = False
        self.s2_limit_time: Optional[datetime] = None
        self.openalex_limit_time: Optional[datetime] = None
    
    def mark_s2_limited(self):
        self.s2_limited = True
        self.s2_limit_time = datetime.now()
        logger.warning("Semantic Scholar rate limited")
    
    def mark_openalex_limited(self):
        self.openalex_limited = True
        self.openalex_limit_time = datetime.now()
        logger.warning("OpenAlex rate limited")

    def is_s2_available(self) -> bool:
        if not self.s2_limited:
            return True
        if self.s2_limit_time and (datetime.now() - self.s2_limit_time).seconds > 300:
            self.s2_limited = False
            logger.info("Semantic Scholar rate limit recovered")
            return True
        return False

    def is_openalex_available(self) -> bool:
        if not self.openalex_limited:
            return True
        if self.openalex_limit_time and (datetime.now() - self.openalex_limit_time).seconds > 60:
            self.openalex_limited = False
            logger.info("OpenAlex rate limit recovered")
            return True
        return False


class GraphBuilder:
    """Build citation graph with multi-source support and smart sorting"""
    
    def __init__(self):
        self.s2_crawler = semantic_scholar
        self.openalex_crawler = openalex
        self.crossref_crawler = crossref
        self.arxiv_crawler = arxiv
        self.rate_status = RateLimitStatus()
    
    async def build_graph(
        self,
        seed_paper_id: str,
        depth: int = 1,
        direction: str = "both",
        classify_intent: bool = True,
        max_papers: int = DEFAULT_MAX_PAPERS,
        data_source: str = "auto",  # "auto", "semantic_scholar", "arxiv"
        progress_callback: Optional[Callable[[CrawlProgress], None]] = None
    ) -> GraphData:
        """
        Build citation graph starting from seed paper
        
        Args:
            seed_paper_id: Starting paper ID (DOI, arXiv ID, or S2 ID)
            depth: How many levels to crawl (1-2)
            direction: "forward" (references), "backward" (citations), or "both"
            classify_intent: Whether to use AI to classify citation intent
            max_papers: Maximum number of papers to include
            data_source: "auto" (smart fallback), "semantic_scholar", or "arxiv"
            progress_callback: Optional callback for progress updates
        
        Returns:
            GraphData with nodes and edges, sorted by citation count
        """
        visited_ids: set[str] = set()
        papers_dict: dict[str, Paper] = {}
        edges: list[CitationEdge] = []
        
        queue: list[tuple[str, int]] = [(seed_paper_id, 0)]
        total_processed = 0
        rate_limited = False
        current_source = "semantic_scholar" if data_source in ["auto", "semantic_scholar"] else "arxiv"
        
        def send_progress(status: str, message: str, current: Optional[str] = None):
            if progress_callback:
                progress_callback(CrawlProgress(
                    status=status,
                    progress=total_processed,
                    total=min(len(visited_ids) + len(queue), max_papers),
                    message=message,
                    current_paper=current
                ))
        
        send_progress("crawling", f"开始构建引用图谱 (数据源: {current_source})...")
        logger.info(f"Starting graph build: seed={seed_paper_id}, depth={depth}, max={max_papers}, source={data_source}")
        
        while queue and len(papers_dict) < max_papers:
            paper_id, current_depth = queue.pop(0)
            
            if paper_id in visited_ids:
                continue
            
            visited_ids.add(paper_id)
            total_processed += 1
            
            send_progress("crawling", f"获取论文 {total_processed}/{max_papers}...", paper_id)
            
            # Try to fetch paper with fallback
            paper = await self._fetch_paper_with_fallback(paper_id, data_source)
            
            if not paper:
                if not self.rate_status.is_s2_available():
                    rate_limited = True
                    send_progress("rate_limited", "API限流中，正在尝试备用源...")
                continue
            
            papers_dict[paper.id] = paper
            
            if paper_id != paper.id:
                visited_ids.add(paper.id)
            
            if current_depth >= depth or len(papers_dict) >= max_papers:
                continue
            
            await asyncio.sleep(API_DELAY_SECONDS)
            
            # Fetch references (FORWARD)
            if direction in ["forward", "both"]:
                send_progress("crawling", f"获取参考文献: {paper.title[:35]}...")
                
                refs = await self._fetch_references_with_fallback(paper, data_source)
                
                # Sort by citation count and take top ones
                refs.sort(key=lambda p: p.citation_count, reverse=True)
                refs = refs[:MAX_REFS_PER_PAPER]
                
                for ref in refs:
                    edges.append(CitationEdge(
                        source=paper.id,
                        target=ref.id,
                        intent=CitationIntent.UNKNOWN,
                        confidence=0.0
                    ))
                    if ref.id not in papers_dict:
                        papers_dict[ref.id] = ref
                    if ref.id not in visited_ids and len(papers_dict) < max_papers:
                        queue.append((ref.id, current_depth + 1))
                
                await asyncio.sleep(API_DELAY_SECONDS)
            
            # Fetch citations (BACKWARD)
            if direction in ["backward", "both"]:
                send_progress("crawling", f"获取施引文献: {paper.title[:35]}...")
                
                cites = await self._fetch_citations_with_fallback(paper, data_source)
                
                # Sort by citation count and take top ones
                cites.sort(key=lambda p: p.citation_count, reverse=True)
                cites = cites[:MAX_CITES_PER_PAPER]
                
                for cit in cites:
                    edges.append(CitationEdge(
                        source=cit.id,
                        target=paper.id,
                        intent=CitationIntent.UNKNOWN,
                        confidence=0.0
                    ))
                    if cit.id not in papers_dict:
                        papers_dict[cit.id] = cit
                    if cit.id not in visited_ids and len(papers_dict) < max_papers:
                        queue.append((cit.id, current_depth + 1))
                
                await asyncio.sleep(API_DELAY_SECONDS)
        
        # Deduplicate edges
        unique_edges: dict[str, CitationEdge] = {}
        for edge in edges:
            if edge.source in papers_dict and edge.target in papers_dict:
                edge_key = f"{edge.source}|{edge.target}"
                if edge_key not in unique_edges:
                    unique_edges[edge_key] = edge
        
        edges = list(unique_edges.values())
        
        # Sort papers by citation count (highest first)
        sorted_papers = sorted(papers_dict.values(), key=lambda p: p.citation_count, reverse=True)
        
        logger.info(f"Graph built: {len(sorted_papers)} papers, {len(edges)} edges")
        
        # AI classification (limited)
        if classify_intent and edges:
            send_progress("analyzing", "AI分析引用意图...")
            edges = await self._classify_intents(papers_dict, edges, progress_callback)
        
        status_msg = f"完成！{len(sorted_papers)} 篇论文，{len(edges)} 条引用"
        if rate_limited:
            status_msg += " (部分数据因限流未获取)"
        send_progress("completed", status_msg)
        
        return GraphData(
            nodes=sorted_papers,  # Already sorted by citation count
            edges=edges
        )
    
    async def _fetch_paper_with_fallback(self, paper_id: str, source: str) -> Optional[Paper]:
        """Fetch paper with fallback (S2 -> OpenAlex -> arXiv)"""
        paper = None
        
        # 1. Try Semantic Scholar
        if source in ["auto", "semantic_scholar"] and self.rate_status.is_s2_available():
            try:
                paper = await self.s2_crawler.get_paper_by_id(paper_id)
                if paper: return paper
                self.rate_status.mark_s2_limited() # Assume failure is limited for now or just failed
            except Exception:
                pass
        
        # 2. Try OpenAlex
        if source in ["auto", "openalex"] and self.rate_status.is_openalex_available():
            try:
                # OpenAlex handles various IDs
                paper = await self.openalex_crawler.get_paper_by_id(paper_id)
                if paper: return paper
                # If failed, mark limited only if 429 (handled in crawler usually returning None)
            except Exception:
                pass
        
        # 3. Fallback to arXiv (metadata only)
        if source in ["auto", "arxiv"]:
            # If ID is DOI, try Crossref first
            if paper_id.startswith("DOI:") or paper_id.startswith("10."):
                 try:
                    paper = await self.crossref_crawler.get_paper_by_doi(paper_id)
                    if paper: return paper
                 except Exception: pass
            
            # Then arXiv
            try:
                paper = await self.arxiv_crawler.get_paper_by_id(paper_id)
            except Exception: pass
        
        return paper
    
    async def _fetch_references_with_fallback(self, paper: Paper, source: str) -> List[Paper]:
        """Fetch references with fallback (S2 -> OpenAlex)
        
        Uses appropriate ID format for each source:
        - S2: prefers DOI, then arXiv ID, then S2 ID
        - OpenAlex: uses OpenAlex ID from paper.id
        """
        refs = []
        
        # Try S2 first with DOI (most reliable for S2)
        if source in ["auto", "semantic_scholar"] and self.rate_status.is_s2_available():
            s2_id = None
            if paper.doi:
                s2_id = paper.doi  # S2 can handle raw DOI
            elif paper.arxiv_id:
                s2_id = f"arXiv:{paper.arxiv_id}"
            elif paper.id.startswith("S2:"):
                s2_id = paper.id[3:]  # Extract S2 ID
            
            if s2_id:
                logger.info(f"Fetching references from S2 using ID: {s2_id}")
                refs = await self.s2_crawler.get_references(s2_id, limit=MAX_REFS_PER_PAPER * 2)
                if refs:
                    logger.info(f"S2 returned {len(refs)} references")
                    return refs
        
        # Fallback to OpenAlex
        if source in ["auto", "openalex"] and self.rate_status.is_openalex_available():
            oa_id = None
            if paper.id.startswith("OpenAlex:"):
                oa_id = paper.id  # Use OpenAlex ID directly
            elif paper.doi:
                # Try to get by DOI first
                oa_id = f"DOI:{paper.doi}"
            
            if oa_id:
                logger.info(f"Fetching references from OpenAlex using ID: {oa_id}")
                refs = await self.openalex_crawler.get_references(oa_id, limit=MAX_REFS_PER_PAPER * 2)
                if refs:
                    logger.info(f"OpenAlex returned {len(refs)} references")
                    return refs
        
        logger.warning(f"Could not fetch references for paper: {paper.title[:50]}")
        return []
    
    async def _fetch_citations_with_fallback(self, paper: Paper, source: str) -> List[Paper]:
        """Fetch citations with fallback (S2 -> OpenAlex)
        
        Uses appropriate ID format for each source:
        - S2: prefers DOI, then arXiv ID, then S2 ID
        - OpenAlex: uses OpenAlex ID from paper.id
        """
        cites = []
        
        # Try S2 first with DOI
        if source in ["auto", "semantic_scholar"] and self.rate_status.is_s2_available():
            s2_id = None
            if paper.doi:
                s2_id = paper.doi  # S2 can handle raw DOI
            elif paper.arxiv_id:
                s2_id = f"arXiv:{paper.arxiv_id}"
            elif paper.id.startswith("S2:"):
                s2_id = paper.id[3:]
            
            if s2_id:
                logger.info(f"Fetching citations from S2 using ID: {s2_id}")
                cites = await self.s2_crawler.get_citations(s2_id, limit=MAX_CITES_PER_PAPER * 2)
                if cites:
                    logger.info(f"S2 returned {len(cites)} citations")
                    return cites
        
        # Fallback to OpenAlex
        if source in ["auto", "openalex"] and self.rate_status.is_openalex_available():
            oa_id = None
            if paper.id.startswith("OpenAlex:"):
                oa_id = paper.id
            elif paper.doi:
                oa_id = f"DOI:{paper.doi}"
            
            if oa_id:
                logger.info(f"Fetching citations from OpenAlex using ID: {oa_id}")
                cites = await self.openalex_crawler.get_citations(oa_id, limit=MAX_CITES_PER_PAPER * 2)
                if cites:
                    logger.info(f"OpenAlex returned {len(cites)} citations")
                    return cites
        
        logger.warning(f"Could not fetch citations for paper: {paper.title[:50]}")
        return []
    
    async def _classify_intents(
        self,
        papers: dict[str, Paper],
        edges: list[CitationEdge],
        progress_callback: Optional[Callable] = None
    ) -> list[CitationEdge]:
        """Classify citation intents using AI with Context Enhancement"""
        MAX_AI_CLASSIFICATIONS = 20  # Reduced to avoid API costs
        
        # Sort edges by source paper's citation count (classify important ones first)
        sorted_edges = sorted(
            edges,
            key=lambda e: papers.get(e.source, Paper(id="", title="", citation_count=0)).citation_count,
            reverse=True
        )
        
        edges_to_classify = sorted_edges[:MAX_AI_CLASSIFICATIONS]
        remaining_edges = sorted_edges[MAX_AI_CLASSIFICATIONS:]
        
        # Prepare batch with contexts
        batch_inputs = []
        
        # Fetch contexts concurrently using Semantic Scholar
        from ..crawlers import semantic_scholar
        
        logger.info(f"Fetching citation contexts for {len(edges_to_classify)} pairs...")
        
        async def fetch_context_for_edge(edge):
            citing = papers.get(edge.source)
            cited = papers.get(edge.target)
            contexts = []
            
            if citing and cited and citing.doi and cited.doi:
                try:
                    # Use S2 to find context
                    contexts = await semantic_scholar.get_citation_contexts(citing.doi, cited.doi)
                except Exception as e:
                    logger.warning(f"Failed to fetch context for {citing.doi}->{cited.doi}: {e}")
            
            return citing, cited, contexts, edge

        # Fetch in parallel with limit
        sem_s2 = asyncio.Semaphore(5) 
        async def limited_fetch(edge):
            async with sem_s2:
                return await fetch_context_for_edge(edge)

        tasks = [limited_fetch(edge) for edge in edges_to_classify]
        
        # Show progress for context fetching
        if progress_callback:
            progress_callback(CrawlProgress(
                status="analyzing",
                progress=0,
                total=len(tasks),
                message="正在获取原文引用上下文..."
            ))
            
        fetched_results = await asyncio.gather(*tasks)
        
        # Prepare for AI
        valid_batch = []
        for citing, cited, contexts, edge in fetched_results:
            if citing and cited:
                valid_batch.append((citing, cited, contexts))
                if contexts:
                    edge.citation_contexts = contexts # Save to edge
                    logger.info(f"Found {len(contexts)} contexts for {citing.title[:20]}->{cited.title[:20]}")

        # Run Classification
        intent_results = await intent_classifier.classify_batch(
            valid_batch,
            progress_callback=lambda current, total: progress_callback(CrawlProgress(
                status="analyzing",
                progress=current,
                total=total,
                message=f"AI分析中 ({current}/{total})"
            )) if progress_callback else None
        )
        
        # Update edges
        classified_edges = []
        for i, result in enumerate(intent_results):
            edge = edges_to_classify[i] # Original edge
            edge.intent = result.intent
            edge.confidence = result.confidence
            edge.reasoning = result.reasoning
            
            # Map Deep Analysis fields
            edge.citation_function = result.citation_function
            edge.citation_sentiment = result.citation_sentiment
            edge.importance_score = result.importance_score
            edge.key_concept = result.key_concept
            
            classified_edges.append(edge)
            
        classified_edges.extend(remaining_edges)
        return classified_edges


# Singleton instance
graph_builder = GraphBuilder()

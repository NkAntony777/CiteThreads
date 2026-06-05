"""
Research Gap Detection Service - Identify under-explored areas in citation networks.
Detects: unrefuted claims, sparse regions, broken citation chains.
"""
from collections import defaultdict
from typing import Dict, List, Set

from ..models import Paper, CitationEdge, CitationIntent, GraphData


class ResearchGap:
    """A detected research gap."""

    def __init__(self, gap_type: str, description: str, paper_ids: List[str], severity: float = 0.0):
        self.gap_type = gap_type
        self.description = description
        self.paper_ids = paper_ids
        self.severity = severity  # 0.0 ~ 1.0

    def to_dict(self) -> dict:
        return {
            "gap_type": self.gap_type,
            "description": self.description,
            "paper_ids": self.paper_ids,
            "severity": round(self.severity, 3),
        }


class GapDetector:
    """Detect research gaps in citation graphs."""

    def detect(self, graph: GraphData) -> List[dict]:
        """
        Run all gap detection analyses.
        Returns list of gap dicts sorted by severity (descending).
        """
        gaps: List[ResearchGap] = []

        papers = {p.id: p for p in graph.nodes}
        edges = graph.edges

        gaps.extend(self._find_unrefuted_papers(papers, edges))
        gaps.extend(self._find_sparse_regions(papers, edges))
        gaps.extend(self._find_broken_chains(papers, edges))
        gaps.extend(self._find_stale_frontiers(papers, edges))

        # Sort by severity descending
        gaps.sort(key=lambda g: g.severity, reverse=True)
        return [g.to_dict() for g in gaps]

    def _find_unrefuted_papers(
        self, papers: Dict[str, Paper], edges: List[CitationEdge]
    ) -> List[ResearchGap]:
        """
        Papers with SUPPORT citations but no OPPOSE citations.
        These represent claims that have not been challenged.
        """
        support_targets: Set[str] = set()
        oppose_targets: Set[str] = set()

        for edge in edges:
            if edge.target in papers:
                if edge.intent == CitationIntent.SUPPORT:
                    support_targets.add(edge.target)
                elif edge.intent == CitationIntent.OPPOSE:
                    oppose_targets.add(edge.target)

        unrefuted = support_targets - oppose_targets
        gaps = []

        for pid in unrefuted:
            paper = papers[pid]
            # Higher citation count + no refutation = higher severity
            severity = min(1.0, paper.citation_count / 50.0) * 0.8
            gaps.append(ResearchGap(
                gap_type="unrefuted_claim",
                description=f"Paper has supporting citations but no opposing views: {paper.title[:60]}",
                paper_ids=[pid],
                severity=severity,
            ))

        return gaps

    def _find_sparse_regions(
        self, papers: Dict[str, Paper], edges: List[CitationEdge]
    ) -> List[ResearchGap]:
        """
        Papers with very few connections (degree < 2) that may represent
        under-explored research areas.
        """
        degree: Dict[str, int] = defaultdict(int)
        for edge in edges:
            if edge.source in papers:
                degree[edge.source] += 1
            if edge.target in papers:
                degree[edge.target] += 1

        gaps = []
        for pid, paper in papers.items():
            d = degree.get(pid, 0)
            if d <= 1 and paper.citation_count > 5:
                # High citation count but few connections in the graph = sparse area
                severity = min(1.0, paper.citation_count / 100.0) * 0.6
                gaps.append(ResearchGap(
                    gap_type="sparse_region",
                    description=f"Well-cited paper with few graph connections: {paper.title[:60]}",
                    paper_ids=[pid],
                    severity=severity,
                ))

        return gaps

    def _find_broken_chains(
        self, papers: Dict[str, Paper], edges: List[CitationEdge]
    ) -> List[ResearchGap]:
        """
        Papers that cite works NOT present in the graph (missing references).
        These represent broken citation chains that could be explored.
        """
        graph_ids = set(papers.keys())
        edge_sources = {e.source for e in edges}
        edge_targets = {e.target for e in edges}

        # Papers that are cited but not in graph (external references)
        missing_refs = edge_targets - graph_ids

        gaps = []
        if missing_refs:
            # Group by the papers that reference them
            referrers: Dict[str, List[str]] = defaultdict(list)
            for edge in edges:
                if edge.target in missing_refs and edge.source in graph_ids:
                    referrers[edge.target].append(edge.source)

            # Find papers that have the most missing references
            paper_missing_count: Dict[str, int] = defaultdict(int)
            for target, sources in referrers.items():
                for src in sources:
                    paper_missing_count[src] += 1

            for pid, count in sorted(paper_missing_count.items(), key=lambda x: -x[1]):
                if count >= 2:
                    paper = papers[pid]
                    severity = min(1.0, count / 10.0) * 0.7
                    gaps.append(ResearchGap(
                        gap_type="broken_chain",
                        description=f"Paper references {count} works not in graph: {paper.title[:60]}",
                        paper_ids=[pid],
                        severity=severity,
                    ))

        return gaps

    def _find_stale_frontiers(
        self, papers: Dict[str, Paper], edges: List[CitationEdge]
    ) -> List[ResearchGap]:
        """
        Older high-impact papers that haven't been cited recently.
        These may represent research frontiers that have gone stale.
        """
        import datetime
        current_year = datetime.datetime.now().year

        # Find the most recent citation year for each paper
        newest_citation: Dict[str, int] = {}
        for edge in edges:
            if edge.target in papers and edge.source in papers:
                citing_year = papers[edge.source].year or 0
                target = edge.target
                if target not in newest_citation or citing_year > newest_citation[target]:
                    newest_citation[target] = citing_year

        gaps = []
        for pid, paper in papers.items():
            if not paper.year or paper.citation_count < 10:
                continue

            last_cited = newest_citation.get(pid, paper.year)
            years_since = current_year - last_cited

            # Old, highly-cited paper that hasn't been cited in 5+ years
            if years_since >= 5 and paper.citation_count >= 20:
                severity = min(1.0, years_since / 10.0) * 0.5
                gaps.append(ResearchGap(
                    gap_type="stale_frontier",
                    description=f"High-impact paper uncited for {years_since} years: {paper.title[:60]}",
                    paper_ids=[pid],
                    severity=severity,
                ))

        return gaps


# Singleton
gap_detector = GapDetector()

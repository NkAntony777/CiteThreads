"""
Tests for research gap detection service.
"""
import pytest

from app.models import Paper, CitationEdge, GraphData, CitationIntent
from app.services.gap_detection import GapDetector


def _make_paper(pid: str, title: str = "Test", citations: int = 10, year: int = 2024) -> Paper:
    return Paper(
        id=pid, title=title, authors=["A"], abstract="",
        year=year, citation_count=citations, source="test"
    )


def _make_edge(src: str, tgt: str, intent: CitationIntent = CitationIntent.UNKNOWN) -> CitationEdge:
    return CitationEdge(source=src, target=tgt, intent=intent)


class TestUnrefutedPapers:
    def test_supported_but_not_refuted(self):
        detector = GapDetector()
        graph = GraphData(
            nodes=[_make_paper("A", "Claim Paper", 50), _make_paper("B"), _make_paper("C")],
            edges=[
                _make_edge("B", "A", CitationIntent.SUPPORT),
                _make_edge("C", "A", CitationIntent.SUPPORT),
            ]
        )
        gaps = detector.detect(graph)
        unrefuted = [g for g in gaps if g["gap_type"] == "unrefuted_claim"]
        assert len(unrefuted) >= 1
        assert any("A" in g["paper_ids"] for g in unrefuted)

    def test_refuted_paper_not_flagged(self):
        detector = GapDetector()
        graph = GraphData(
            nodes=[_make_paper("A"), _make_paper("B"), _make_paper("C")],
            edges=[
                _make_edge("B", "A", CitationIntent.SUPPORT),
                _make_edge("C", "A", CitationIntent.OPPOSE),
            ]
        )
        gaps = detector.detect(graph)
        unrefuted = [g for g in gaps if g["gap_type"] == "unrefuted_claim"]
        assert not any("A" in g["paper_ids"] for g in unrefuted)


class TestSparseRegions:
    def test_well_cited_few_connections(self):
        detector = GapDetector()
        # A has 50 citations but only 1 edge in graph
        graph = GraphData(
            nodes=[_make_paper("A", "Popular Paper", 50), _make_paper("B")],
            edges=[_make_edge("B", "A")]
        )
        gaps = detector.detect(graph)
        sparse = [g for g in gaps if g["gap_type"] == "sparse_region"]
        assert any("A" in g["paper_ids"] for g in sparse)

    def test_well_connected_not_flagged(self):
        detector = GapDetector()
        graph = GraphData(
            nodes=[_make_paper("A", "Connected", 50), _make_paper("B"), _make_paper("C"), _make_paper("D")],
            edges=[_make_edge("B", "A"), _make_edge("C", "A"), _make_edge("D", "A")]
        )
        gaps = detector.detect(graph)
        sparse = [g for g in gaps if g["gap_type"] == "sparse_region"]
        assert not any("A" in g["paper_ids"] for g in sparse)


class TestBrokenChains:
    def test_missing_references(self):
        detector = GapDetector()
        # A references X and Y which are not in graph
        graph = GraphData(
            nodes=[_make_paper("A"), _make_paper("B")],
            edges=[
                _make_edge("A", "X"), _make_edge("A", "Y"), _make_edge("A", "Z"),
                _make_edge("B", "A"),
            ]
        )
        gaps = detector.detect(graph)
        broken = [g for g in gaps if g["gap_type"] == "broken_chain"]
        assert len(broken) >= 1
        assert any("A" in g["paper_ids"] for g in broken)


class TestStaleFrontiers:
    def test_old_uncited_paper(self):
        detector = GapDetector()
        # Paper from 2010 with 30 citations, last cited by a 2018 paper
        graph = GraphData(
            nodes=[
                _make_paper("OLD", "Old Frontier", 30, 2010),
                _make_paper("MID", "Mid Paper", 10, 2018),
            ],
            edges=[_make_edge("MID", "OLD")]  # Last citation in 2018
        )
        gaps = detector.detect(graph)
        stale = [g for g in gaps if g["gap_type"] == "stale_frontier"]
        assert len(stale) >= 1

    def test_recently_cited_not_stale(self):
        detector = GapDetector()
        graph = GraphData(
            nodes=[
                _make_paper("ACTIVE", "Active Paper", 30, 2020),
                _make_paper("NEW", "New Paper", 5, 2024),
            ],
            edges=[_make_edge("NEW", "ACTIVE")]
        )
        gaps = detector.detect(graph)
        stale = [g for g in gaps if g["gap_type"] == "stale_frontier"]
        assert not any("ACTIVE" in g["paper_ids"] for g in stale)


class TestEmptyGraph:
    def test_no_gaps_on_empty(self):
        detector = GapDetector()
        gaps = detector.detect(GraphData(nodes=[], edges=[]))
        assert gaps == []


class TestSeveritySorting:
    def test_sorted_by_severity(self):
        detector = GapDetector()
        graph = GraphData(
            nodes=[_make_paper("A", "Paper A", 100), _make_paper("B", "Paper B", 10)],
            edges=[_make_edge("B", "A", CitationIntent.SUPPORT)]
        )
        gaps = detector.detect(graph)
        if len(gaps) >= 2:
            for i in range(len(gaps) - 1):
                assert gaps[i]["severity"] >= gaps[i + 1]["severity"]

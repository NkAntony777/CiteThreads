"""
Tests for network analysis service (PageRank, Betweenness, Community Detection).
"""
import pytest

from app.models import Paper, CitationEdge, GraphData, CitationIntent
from app.services.network_analysis import NetworkAnalyzer


def _make_paper(pid: str, citations: int = 10) -> Paper:
    return Paper(
        id=pid, title=f"Paper {pid}", authors=["A"], abstract="",
        year=2024, citation_count=citations, source="test"
    )


def _make_edge(src: str, tgt: str) -> CitationEdge:
    return CitationEdge(source=src, target=tgt, intent=CitationIntent.UNKNOWN)


class TestPageRank:
    def test_empty_graph(self):
        analyzer = NetworkAnalyzer()
        result = analyzer.analyze(GraphData(nodes=[], edges=[]))
        assert result["pagerank"] == {}

    def test_single_node(self):
        analyzer = NetworkAnalyzer()
        graph = GraphData(nodes=[_make_paper("A")], edges=[])
        result = analyzer.analyze(graph)
        assert abs(result["pagerank"]["A"] - 1.0) < 1e-6

    def test_two_node_chain(self):
        """A -> B: B should have higher PageRank (receives from A)"""
        analyzer = NetworkAnalyzer()
        graph = GraphData(
            nodes=[_make_paper("A"), _make_paper("B")],
            edges=[_make_edge("A", "B")]
        )
        result = analyzer.analyze(graph)
        assert result["pagerank"]["B"] > result["pagerank"]["A"]

    def test_hub_node(self):
        """Node C receives from A and B, should have highest PageRank"""
        analyzer = NetworkAnalyzer()
        graph = GraphData(
            nodes=[_make_paper("A"), _make_paper("B"), _make_paper("C")],
            edges=[_make_edge("A", "C"), _make_edge("B", "C")]
        )
        result = analyzer.analyze(graph)
        assert result["pagerank"]["C"] > result["pagerank"]["A"]
        assert result["pagerank"]["C"] > result["pagerank"]["B"]

    def test_scores_sum_to_one(self):
        """PageRank scores should sum to ~1.0"""
        analyzer = NetworkAnalyzer()
        graph = GraphData(
            nodes=[_make_paper(f"P{i}") for i in range(5)],
            edges=[_make_edge("P0", "P1"), _make_edge("P1", "P2"),
                   _make_edge("P2", "P3"), _make_edge("P3", "P4")]
        )
        result = analyzer.analyze(graph)
        total = sum(result["pagerank"].values())
        assert abs(total - 1.0) < 1e-4

    def test_disconnected_graph(self):
        """Disconnected nodes should still get scores"""
        analyzer = NetworkAnalyzer()
        graph = GraphData(
            nodes=[_make_paper("A"), _make_paper("B"), _make_paper("C")],
            edges=[_make_edge("A", "B")]  # C is disconnected
        )
        result = analyzer.analyze(graph)
        assert "C" in result["pagerank"]
        assert result["pagerank"]["C"] > 0


class TestBetweennessCentrality:
    def test_bridge_node(self):
        """Node B bridges A and C in chain A->B->C"""
        analyzer = NetworkAnalyzer()
        graph = GraphData(
            nodes=[_make_paper("A"), _make_paper("B"), _make_paper("C")],
            edges=[_make_edge("A", "B"), _make_edge("B", "C")]
        )
        result = analyzer.analyze(graph)
        assert result["betweenness"]["B"] > result["betweenness"]["A"]
        assert result["betweenness"]["B"] > result["betweenness"]["C"]

    def test_star_center(self):
        """Center of star should have highest betweenness"""
        analyzer = NetworkAnalyzer()
        graph = GraphData(
            nodes=[_make_paper("CENTER")] + [_make_paper(f"L{i}") for i in range(4)],
            edges=[_make_edge(f"L{i}", "CENTER") for i in range(4)]
        )
        result = analyzer.analyze(graph)
        center_score = result["betweenness"]["CENTER"]
        for i in range(4):
            assert center_score >= result["betweenness"][f"L{i}"]

    def test_empty_graph(self):
        analyzer = NetworkAnalyzer()
        result = analyzer.analyze(GraphData(nodes=[], edges=[]))
        assert result["betweenness"] == {}


class TestCommunityDetection:
    def test_single_community(self):
        """Fully connected graph should form one community"""
        analyzer = NetworkAnalyzer()
        graph = GraphData(
            nodes=[_make_paper(f"P{i}") for i in range(4)],
            edges=[
                _make_edge("P0", "P1"), _make_edge("P1", "P0"),
                _make_edge("P1", "P2"), _make_edge("P2", "P1"),
                _make_edge("P2", "P3"), _make_edge("P3", "P2"),
                _make_edge("P3", "P0"), _make_edge("P0", "P3"),
            ]
        )
        result = analyzer.analyze(graph)
        communities = result["communities"]
        # All nodes should be in same community
        unique = set(communities.values())
        assert len(unique) == 1

    def test_two_communities(self):
        """Two disconnected clusters should form two communities"""
        analyzer = NetworkAnalyzer()
        graph = GraphData(
            nodes=[_make_paper(f"A{i}") for i in range(3)] +
                   [_make_paper(f"B{i}") for i in range(3)],
            edges=[
                # Cluster A
                _make_edge("A0", "A1"), _make_edge("A1", "A0"),
                _make_edge("A1", "A2"), _make_edge("A2", "A1"),
                # Cluster B
                _make_edge("B0", "B1"), _make_edge("B1", "B0"),
                _make_edge("B1", "B2"), _make_edge("B2", "B1"),
            ]
        )
        result = analyzer.analyze(graph)
        communities = result["communities"]

        # A nodes should share a community
        a_comms = {communities[f"A{i}"] for i in range(3)}
        assert len(a_comms) == 1

        # B nodes should share a different community
        b_comms = {communities[f"B{i}"] for i in range(3)}
        assert len(b_comms) == 1

        # The two communities should be different
        assert a_comms != b_comms

    def test_all_nodes_assigned(self):
        """Every node should be assigned to a community"""
        analyzer = NetworkAnalyzer()
        graph = GraphData(
            nodes=[_make_paper(f"P{i}") for i in range(5)],
            edges=[_make_edge("P0", "P1"), _make_edge("P2", "P3")]
        )
        result = analyzer.analyze(graph)
        for p in graph.nodes:
            assert p.id in result["communities"]


class TestFullAnalysis:
    def test_returns_all_metrics(self):
        """analyze() should return all three metric types"""
        analyzer = NetworkAnalyzer()
        graph = GraphData(
            nodes=[_make_paper("A"), _make_paper("B"), _make_paper("C")],
            edges=[_make_edge("A", "B"), _make_edge("B", "C")]
        )
        result = analyzer.analyze(graph)
        assert "pagerank" in result
        assert "betweenness" in result
        assert "communities" in result

    def test_realistic_citation_graph(self):
        """Test with a more realistic citation network"""
        analyzer = NetworkAnalyzer()
        # Classic paper -> 3 citing papers -> each cited by 2 more
        nodes = [_make_paper("ROOT", 100)]
        edges = []
        for i in range(3):
            nodes.append(_make_paper(f"CITER{i}", 30))
            edges.append(_make_edge(f"CITER{i}", "ROOT"))
            for j in range(2):
                pid = f"CITER{i}_REF{j}"
                nodes.append(_make_paper(pid, 5))
                edges.append(_make_edge(pid, f"CITER{i}"))

        graph = GraphData(nodes=nodes, edges=edges)
        result = analyzer.analyze(graph)

        # ROOT should have highest PageRank
        root_pr = result["pagerank"]["ROOT"]
        for node in nodes:
            if node.id != "ROOT":
                assert root_pr >= result["pagerank"][node.id]

        # All nodes assigned
        assert len(result["communities"]) == len(nodes)

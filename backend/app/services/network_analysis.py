"""
Network Analysis Service - Citation graph metrics.
Implements PageRank, Betweenness Centrality, and Community Detection.
No external graph library required.
"""
import math
from collections import defaultdict, deque
from typing import Dict, List, Set, Tuple

from ..models import Paper, CitationEdge, GraphData


class NetworkAnalyzer:
    """Compute network metrics for citation graphs."""

    def analyze(self, graph: GraphData) -> Dict[str, Dict[str, float]]:
        """
        Run all analyses on a graph. Returns dict of metric_name -> {paper_id: score}.
        Also returns community assignments.
        """
        if not graph.nodes:
            return {"pagerank": {}, "betweenness": {}, "communities": {}}

        # Build adjacency structures
        adj_out, adj_in = self._build_adjacency(graph)

        pagerank = self._pagerank(adj_out, adj_in, set(p.id for p in graph.nodes))
        betweenness = self._betweenness_centrality(adj_out, set(p.id for p in graph.nodes))
        communities = self._label_propagation(adj_out, set(p.id for p in graph.nodes))

        return {
            "pagerank": pagerank,
            "betweenness": betweenness,
            "communities": communities,
        }

    def _build_adjacency(
        self, graph: GraphData
    ) -> Tuple[Dict[str, Set[str]], Dict[str, Set[str]]]:
        """Build outgoing and incoming adjacency sets."""
        adj_out: Dict[str, Set[str]] = defaultdict(set)
        adj_in: Dict[str, Set[str]] = defaultdict(set)

        for edge in graph.edges:
            adj_out[edge.source].add(edge.target)
            adj_in[edge.target].add(edge.source)

        return adj_out, adj_in

    def _pagerank(
        self,
        adj_out: Dict[str, Set[str]],
        adj_in: Dict[str, Set[str]],
        nodes: Set[str],
        damping: float = 0.85,
        iterations: int = 50,
        tolerance: float = 1e-6,
    ) -> Dict[str, float]:
        """
        Compute PageRank scores.
        Iterative power method with damping factor.
        """
        n = len(nodes)
        if n == 0:
            return {}

        initial_value = 1.0 / n
        rank = {node: initial_value for node in nodes}

        for _ in range(iterations):
            new_rank = {}
            # Collect dangling nodes (no outgoing edges)
            dangling_sum = sum(
                rank[node] for node in nodes if not adj_out.get(node)
            )

            for node in nodes:
                # Contribution from incoming links
                incoming_sum = sum(
                    rank[src] / len(adj_out[src])
                    for src in adj_in.get(node, set())
                    if src in adj_out and adj_out[src]
                )
                # Dangling contribution distributed evenly
                dangling_contrib = dangling_sum / n

                new_rank[node] = (1 - damping) / n + damping * (
                    incoming_sum + dangling_contrib
                )

            # Check convergence
            diff = sum(abs(new_rank[node] - rank[node]) for node in nodes)
            rank = new_rank

            if diff < tolerance:
                break

        return rank

    def _betweenness_centrality(
        self,
        adj_out: Dict[str, Set[str]],
        nodes: Set[str],
    ) -> Dict[str, float]:
        """
        Compute betweenness centrality using Brandes' algorithm (unweighted).
        Measures how often a node lies on shortest paths between other nodes.
        """
        betweenness = {node: 0.0 for node in nodes}

        for source in nodes:
            # BFS from source
            stack = []
            predecessors: Dict[str, List[str]] = defaultdict(list)
            sigma = {node: 0.0 for node in nodes}
            sigma[source] = 1.0
            distance = {node: -1 for node in nodes}
            distance[source] = 0

            queue = deque([source])

            while queue:
                v = queue.popleft()
                stack.append(v)
                for w in adj_out.get(v, set()):
                    if w not in nodes:
                        continue
                    # First time visiting w?
                    if distance[w] < 0:
                        distance[w] = distance[v] + 1
                        queue.append(w)
                    # Shortest path to w via v?
                    if distance[w] == distance[v] + 1:
                        sigma[w] += sigma[v]
                        predecessors[w].append(v)

            # Back-propagation
            delta = {node: 0.0 for node in nodes}
            while stack:
                w = stack.pop()
                for v in predecessors[w]:
                    if sigma[w] > 0:
                        delta[v] += (sigma[v] / sigma[w]) * (1 + delta[w])
                if w != source:
                    betweenness[w] += delta[w]

        # Normalize for undirected interpretation (divide by 2)
        if len(nodes) > 2:
            norm = (len(nodes) - 1) * (len(nodes) - 2)
            for node in betweenness:
                betweenness[node] /= norm

        return betweenness

    def _label_propagation(
        self,
        adj_out: Dict[str, Set[str]],
        nodes: Set[str],
        max_iterations: int = 30,
    ) -> Dict[str, int]:
        """
        Community detection via Label Propagation Algorithm.
        Each node adopts the most common label among its neighbors.
        Returns dict mapping node_id -> community_id.
        """
        # Build undirected adjacency (merge in/out for citation graphs)
        adj: Dict[str, Set[str]] = defaultdict(set)
        for src, targets in adj_out.items():
            if src in nodes:
                for tgt in targets:
                    if tgt in nodes:
                        adj[src].add(tgt)
                        adj[tgt].add(src)

        # Initialize each node with its own label
        labels = {node: i for i, node in enumerate(nodes)}
        node_list = list(nodes)

        for iteration in range(max_iterations):
            changed = False

            # Shuffle order each iteration for stability
            import random
            random.seed(42 + iteration)
            random.shuffle(node_list)

            for node in node_list:
                neighbors = adj.get(node, set())
                if not neighbors:
                    continue

                # Count label frequencies among neighbors
                label_counts: Dict[int, int] = defaultdict(int)
                for neighbor in neighbors:
                    label_counts[labels[neighbor]] += 1

                # Pick most frequent label (break ties by smallest label)
                max_count = max(label_counts.values())
                best_label = min(
                    label for label, count in label_counts.items()
                    if count == max_count
                )

                if best_label != labels[node]:
                    labels[node] = best_label
                    changed = True

            if not changed:
                break

        # Remap to contiguous community IDs
        unique_labels = sorted(set(labels.values()))
        label_map = {label: i for i, label in enumerate(unique_labels)}
        return {node: label_map[label] for node, label in labels.items()}


# Singleton
network_analyzer = NetworkAnalyzer()

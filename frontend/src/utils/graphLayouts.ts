/**
 * Graph Layout Algorithms
 * Provides multiple layout modes for the citation graph.
 */
import type { Paper, CitationEdge, GraphNode, GraphLink } from '../types';

export type LayoutMode = 'timeline' | 'radial' | 'force';

interface LayoutConfig {
    width: number;
    height: number;
}

const DEFAULT_CONFIG: LayoutConfig = { width: 2000, height: 1200 };

const calculateNodeSize = (citationCount: number): number =>
    Math.max(12, Math.min(35, 12 + Math.log10(citationCount + 1) * 10));

/**
 * Timeline layout (default): X = year, Y = sorted by citations within year.
 */
export function timelineLayout(
    papers: Paper[],
    _edges: { source: string; target: string }[],
    config: LayoutConfig = DEFAULT_CONFIG
): Map<string, { x: number; y: number }> {
    const positions = new Map<string, { x: number; y: number }>();
    if (papers.length === 0) return positions;

    const yearGroups = new Map<number, Paper[]>();
    let minYear = Infinity;
    let maxYear = -Infinity;

    for (const paper of papers) {
        const year = paper.year || 2000;
        minYear = Math.min(minYear, year);
        maxYear = Math.max(maxYear, year);
        if (!yearGroups.has(year)) yearGroups.set(year, []);
        yearGroups.get(year)!.push(paper);
    }

    yearGroups.forEach(group => group.sort((a, b) => b.citation_count - a.citation_count));

    const paddingX = 150;
    const paddingY = 100;
    const minSpacingY = 140;
    const yearRange = maxYear - minYear || 1;

    const xScale = (year: number) =>
        paddingX + ((year - minYear) / yearRange) * (config.width - paddingX * 2);

    const sortedYears = Array.from(yearGroups.keys()).sort((a, b) => a - b);

    for (const year of sortedYears) {
        const group = yearGroups.get(year)!;
        const availableHeight = config.height - paddingY * 2;
        const spacing = Math.max(minSpacingY, availableHeight / (group.length + 1));
        const startY = paddingY + (availableHeight - (group.length - 1) * spacing) / 2;

        group.forEach((paper, i) => {
            positions.set(paper.id, { x: xScale(year), y: startY + i * spacing });
        });
    }

    return positions;
}

/**
 * Radial layout: most-cited paper at center, others in concentric rings.
 */
export function radialLayout(
    papers: Paper[],
    _edges: { source: string; target: string }[],
    config: LayoutConfig = DEFAULT_CONFIG
): Map<string, { x: number; y: number }> {
    const positions = new Map<string, { x: number; y: number }>();
    if (papers.length === 0) return positions;

    const centerX = config.width / 2;
    const centerY = config.height / 2;

    // Sort by citation count descending
    const sorted = [...papers].sort((a, b) => b.citation_count - a.citation_count);

    // Top paper at center
    positions.set(sorted[0].id, { x: centerX, y: centerY });

    // Distribute rest in rings
    const remaining = sorted.slice(1);
    const maxRadius = Math.min(config.width, config.height) * 0.4;
    const ringCount = Math.max(1, Math.ceil(Math.sqrt(remaining.length / 6)));

    for (let i = 0; i < remaining.length; i++) {
        const ring = Math.floor(i / (remaining.length / ringCount));
        const ringFraction = (ring + 1) / ringCount;
        const radius = 120 + ringFraction * maxRadius;

        const itemsInRing = Math.min(
            remaining.length - ring * Math.ceil(remaining.length / ringCount),
            Math.ceil(remaining.length / ringCount)
        );
        const indexInRing = i - ring * Math.ceil(remaining.length / ringCount);
        const angle = (indexInRing / itemsInRing) * Math.PI * 2 - Math.PI / 2;

        positions.set(remaining[i].id, {
            x: centerX + Math.cos(angle) * radius,
            y: centerY + Math.sin(angle) * radius,
        });
    }

    return positions;
}

/**
 * Force-directed layout: simple force simulation (no d3-force dependency).
 * Uses iterative spring-electric model.
 */
export function forceDirectedLayout(
    papers: Paper[],
    edges: { source: string; target: string }[],
    config: LayoutConfig = DEFAULT_CONFIG
): Map<string, { x: number; y: number }> {
    const positions = new Map<string, { x: number; y: number }>();
    if (papers.length === 0) return positions;

    const n = papers.length;
    const padding = 100;

    // Initialize with grid positions
    const cols = Math.ceil(Math.sqrt(n));
    for (let i = 0; i < n; i++) {
        const row = Math.floor(i / cols);
        const col = i % cols;
        positions.set(papers[i].id, {
            x: padding + (col / cols) * (config.width - padding * 2) + (config.width - padding * 2) / cols / 2,
            y: padding + (row / Math.ceil(n / cols)) * (config.height - padding * 2) + (config.height - padding * 2) / Math.ceil(n / cols) / 2,
        });
    }

    // Build adjacency
    const adj = new Map<string, Set<string>>();
    for (const p of papers) adj.set(p.id, new Set());
    for (const e of edges) {
        adj.get(e.source)?.add(e.target);
        adj.get(e.target)?.add(e.source);
    }

    // Force parameters
    const repulsion = 5000;
    const attraction = 0.005;
    const damping = 0.9;
    const iterations = 100;

    const velocities = new Map<string, { vx: number; vy: number }>();
    for (const p of papers) velocities.set(p.id, { vx: 0, vy: 0 });

    for (let iter = 0; iter < iterations; iter++) {
        // Repulsion between all pairs
        for (let i = 0; i < n; i++) {
            for (let j = i + 1; j < n; j++) {
                const a = positions.get(papers[i].id)!;
                const b = positions.get(papers[j].id)!;
                let dx = a.x - b.x;
                let dy = a.y - b.y;
                const dist = Math.sqrt(dx * dx + dy * dy) || 1;
                const force = repulsion / (dist * dist);
                dx = (dx / dist) * force;
                dy = (dy / dist) * force;

                const va = velocities.get(papers[i].id)!;
                const vb = velocities.get(papers[j].id)!;
                va.vx += dx; va.vy += dy;
                vb.vx -= dx; vb.vy -= dy;
            }
        }

        // Attraction along edges
        for (const e of edges) {
            const a = positions.get(e.source);
            const b = positions.get(e.target);
            if (!a || !b) continue;
            const dx = b.x - a.x;
            const dy = b.y - a.y;
            const dist = Math.sqrt(dx * dx + dy * dy) || 1;
            const force = dist * attraction;

            const va = velocities.get(e.source)!;
            const vb = velocities.get(e.target)!;
            va.vx += (dx / dist) * force;
            va.vy += (dy / dist) * force;
            vb.vx -= (dx / dist) * force;
            vb.vy -= (dy / dist) * force;
        }

        // Apply velocities with damping
        for (const p of papers) {
            const pos = positions.get(p.id)!;
            const vel = velocities.get(p.id)!;
            pos.x += vel.vx;
            pos.y += vel.vy;
            vel.vx *= damping;
            vel.vy *= damping;

            // Keep within bounds
            pos.x = Math.max(padding, Math.min(config.width - padding, pos.x));
            pos.y = Math.max(padding, Math.min(config.height - padding, pos.y));
        }
    }

    return positions;
}

/**
 * Apply a layout mode to papers and return GraphNode[].
 */
export function applyLayout(
    papers: Paper[],
    edges: CitationEdge[],
    mode: LayoutMode
): { nodes: GraphNode[]; links: GraphLink[] } {
    if (papers.length === 0) return { nodes: [], links: [] };

    const positions = (() => {
        switch (mode) {
            case 'radial': return radialLayout(papers, edges);
            case 'force': return forceDirectedLayout(papers, edges);
            case 'timeline':
            default: return timelineLayout(papers, edges);
        }
    })();

    const nodeMap = new Map<string, GraphNode>();
    const graphNodes: GraphNode[] = papers.map(paper => {
        const pos = positions.get(paper.id) || { x: 1000, y: 600 };
        const node: GraphNode = {
            ...paper,
            x: pos.x,
            y: pos.y,
            size: calculateNodeSize(paper.citation_count),
        };
        nodeMap.set(paper.id, node);
        return node;
    });

    const graphLinks: GraphLink[] = edges.map(edge => ({
        ...edge,
        sourceNode: nodeMap.get(edge.source),
        targetNode: nodeMap.get(edge.target),
    }));

    return { nodes: graphNodes, links: graphLinks };
}

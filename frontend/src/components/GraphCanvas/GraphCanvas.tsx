/**
 * GraphCanvas Component - D3.js DAG Visualization
 * Incremental rendering with enter/update/exit pattern.
 */
import React, { useEffect, useRef, useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { select, zoom, zoomIdentity, interpolateBlues, extent } from 'd3';
import { useGraphStore } from '../../stores/graphStore';
import type { GraphNode, GraphLink, CitationIntent } from '../../types';
import { applyGraphFilters } from '../../utils/graphFilters';
import './GraphCanvas.css';

// Intent colors
const INTENT_COLORS: Record<CitationIntent, string> = {
    SUPPORT: '#22c55e',   // Green
    OPPOSE: '#ef4444',    // Red
    NEUTRAL: '#6b7280',   // Gray
    UNKNOWN: '#9ca3af',   // Light gray
};

// Track whether initial SVG structure has been set up
const SETUP_FLAG = 'data-setup';

export const GraphCanvas: React.FC = () => {
    const { t } = useTranslation();
    const svgRef = useRef<SVGSVGElement>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const zoomRef = useRef<ReturnType<typeof zoom<SVGSVGElement, unknown>> | null>(null);

    const {
        nodes,
        links,
        setSelectedNode,
        setHoveredNode,
        selectedNode,
        hoveredNode,
        clusters,
        paperClusters,
        selectedEdge,
        setSelectedEdge,
        yearRange,
        intentFilter,
        clusterFilter,
    } = useGraphStore();

    const { nodes: visibleNodes, links: visibleLinks } = useMemo(
        () => applyGraphFilters(nodes, links, { yearRange, intentFilter, clusterFilter, paperClusters }),
        [nodes, links, yearRange, intentFilter, clusterFilter, paperClusters]
    );

    // Setup SVG structure once (defs, groups, zoom)
    const setupSvg = useCallback(() => {
        if (!svgRef.current || !containerRef.current) return;
        const svg = select(svgRef.current);

        // Already set up
        if ((svg.node() as SVGSVGElement)?.getAttribute(SETUP_FLAG)) return;

        const container = containerRef.current;
        const width = container.clientWidth;
        const height = container.clientHeight;

        svg.attr('width', width).attr('height', height);

        // Zoom behavior
        const zoomBehavior = zoom<SVGSVGElement, unknown>()
            .scaleExtent([0.1, 4])
            .on('zoom', (event) => {
                svg.select('g.main-group').attr('transform', event.transform);
            });

        svg.call(zoomBehavior);
        zoomRef.current = zoomBehavior;

        // Main group
        svg.append('g').attr('class', 'main-group');

        // Arrow marker definitions
        const defs = svg.append('defs');
        (['SUPPORT', 'OPPOSE', 'NEUTRAL', 'UNKNOWN'] as CitationIntent[]).forEach(intent => {
            defs.append('marker')
                .attr('id', `arrow-${intent}`)
                .attr('viewBox', '0 -6 12 12')
                .attr('refX', 20)
                .attr('refY', 0)
                .attr('markerWidth', 10)
                .attr('markerHeight', 10)
                .attr('orient', 'auto')
                .append('path')
                .attr('fill', INTENT_COLORS[intent])
                .attr('d', 'M0,-6L12,0L0,6Z');
        });

        // Edge and node sub-groups
        const g = svg.select('g.main-group');
        g.append('g').attr('class', 'edges');
        g.append('g').attr('class', 'nodes');

        // Click on background to deselect
        svg.on('click', () => {
            setSelectedNode(null);
            setSelectedEdge(null);
        });

        // Mark as set up
        (svg.node() as SVGSVGElement).setAttribute(SETUP_FLAG, 'true');
    }, [setSelectedNode, setSelectedEdge]);

    // Incremental data update using enter/update/exit
    const updateGraph = useCallback(() => {
        if (!svgRef.current || !containerRef.current || visibleNodes.length === 0) return;

        const svg = select(svgRef.current);
        const container = containerRef.current;
        const width = container.clientWidth;
        const height = container.clientHeight;

        svg.attr('width', width).attr('height', height);

        // Auto-fit on first data load (if zoom hasn't been used yet)
        if (zoomRef.current && visibleNodes.length > 0) {
            const xExt = extent(visibleNodes, d => d.x) as [number, number];
            const yExt = extent(visibleNodes, d => d.y) as [number, number];
            const graphW = xExt[1] - xExt[0] + 300;
            const graphH = yExt[1] - yExt[0] + 200;
            const scale = Math.min(width / graphW, height / graphH, 0.8);
            const cx = (width - graphW * scale) / 2 - xExt[0] * scale + 100;
            const cy = (height - graphH * scale) / 2 - yExt[0] * scale + 50;
            svg.call(zoomRef.current.transform, zoomIdentity.translate(cx, cy).scale(scale));
        }

        const nodeMap = new Map(visibleNodes.map(n => [n.id, n]));

        // ---- EDGES (enter/update/exit) ----
        const edgeGroup = svg.select('g.edges');

        const edgeSel = edgeGroup.selectAll<SVGGElement, GraphLink>('g.edge-group')
            .data(visibleLinks, d => `${d.source}->${d.target}`);

        // Exit
        edgeSel.exit().remove();

        // Enter
        const edgeEnter = edgeSel.enter()
            .append('g')
            .attr('class', 'edge-group');

        edgeEnter.append('path').attr('class', 'edge');
        edgeEnter.append('title');

        // Merge enter + update
        const edgeMerge = edgeSel.merge(edgeEnter);

        edgeMerge.select<SVGPathElement>('path.edge')
            .attr('d', (d) => {
                const source = nodeMap.get(d.source);
                const target = nodeMap.get(d.target);
                if (!source || !target) return '';
                const dx = target.x - source.x;
                const dy = target.y - source.y;
                const dist = Math.sqrt(dx * dx + dy * dy) || 1;
                const curveOffset = Math.min(dist * 0.2, 50);
                const midX = (source.x + target.x) / 2;
                const midY = (source.y + target.y) / 2;
                const perpX = -dy / dist * curveOffset;
                const perpY = dx / dist * curveOffset;
                return `M${source.x},${source.y} Q${midX + perpX},${midY + perpY} ${target.x},${target.y}`;
            })
            .attr('stroke', d => INTENT_COLORS[d.intent])
            .attr('stroke-width', d => d.intent === 'UNKNOWN' ? 1.5 : 2.5)
            .attr('stroke-dasharray', d => d.intent === 'UNKNOWN' ? '6,4' : 'none')
            .attr('fill', 'none')
            .attr('marker-end', d => `url(#arrow-${d.intent})`)
            .attr('opacity', 0.7)
            .style('cursor', 'pointer')
            .on('click', (event, d) => {
                event.stopPropagation();
                setSelectedEdge(d);
            });

        edgeMerge.select('title')
            .text(d => `${d.intent}\n${d.reasoning || t('graphCanvas.noDetail')}`);

        // ---- NODES (enter/update/exit) ----
        const nodeGroup = svg.select('g.nodes');

        const nodeSel = nodeGroup.selectAll<SVGGElement, GraphNode>('g.node')
            .data(visibleNodes, d => d.id);

        // Exit
        nodeSel.exit().remove();

        // Enter
        const nodeEnter = nodeSel.enter()
            .append('g')
            .attr('class', 'node')
            .style('cursor', 'pointer');

        nodeEnter.append('circle').attr('class', 'node-circle');
        nodeEnter.append('text').attr('class', 'node-year');
        nodeEnter.append('text').attr('class', 'node-label');

        // Attach event handlers to entering nodes
        nodeEnter.select('.node-circle')
            .on('click', (event, d) => {
                event.stopPropagation();
                setSelectedNode(d);
            })
            .on('mouseenter', (_, d) => setHoveredNode(d))
            .on('mouseleave', () => setHoveredNode(null));

        // Merge enter + update
        const nodeMerge = nodeSel.merge(nodeEnter);

        nodeMerge
            .attr('transform', d => `translate(${d.x}, ${d.y})`);

        nodeMerge.select('.node-circle')
            .attr('r', d => d.size)
            .attr('fill', d => {
                if (clusters.length > 0 && paperClusters[d.id] !== undefined) {
                    const clusterId = paperClusters[d.id];
                    const cluster = clusters.find(c => c.id === clusterId);
                    if (cluster) return cluster.color;
                }
                const year = d.year || 2000;
                const normalizedYear = Math.max(0, Math.min(1, (year - 1990) / 40));
                return interpolateBlues(0.35 + normalizedYear * 0.5);
            })
            .attr('stroke', '#fff')
            .attr('stroke-width', 2.5)
            .attr('opacity', 0.9);

        nodeMerge.select('.node-year')
            .attr('dy', 5)
            .attr('text-anchor', 'middle')
            .text(d => d.year || '')
            .attr('font-size', d => Math.max(9, Math.min(14, d.size * 0.5)))
            .attr('fill', '#fff')
            .attr('font-weight', '600')
            .style('pointer-events', 'none');

        nodeMerge.select('.node-label')
            .attr('dy', d => d.size + 16)
            .attr('text-anchor', 'middle')
            .text(d => {
                const maxLen = 25;
                return d.title.length > maxLen ? d.title.slice(0, maxLen) + '...' : d.title;
            })
            .attr('font-size', 11)
            .attr('fill', '#374151')
            .style('pointer-events', 'none');

    }, [visibleNodes, visibleLinks, clusters, paperClusters, setSelectedNode, setHoveredNode, setSelectedEdge, t]);

    // Setup SVG structure once
    useEffect(() => {
        setupSvg();
    }, [setupSvg]);

    // Update graph data incrementally
    useEffect(() => {
        updateGraph();
    }, [updateGraph]);

    // Handle window resize
    useEffect(() => {
        const handleResize = () => {
            if (!svgRef.current || !containerRef.current) return;
            const svg = select(svgRef.current);
            svg.attr('width', containerRef.current.clientWidth)
               .attr('height', containerRef.current.clientHeight);
        };
        window.addEventListener('resize', handleResize);
        return () => window.removeEventListener('resize', handleResize);
    }, []);

    // Highlight selected/hovered nodes and edges (attribute-only updates, no rebuild)
    useEffect(() => {
        if (!svgRef.current) return;
        const svg = select(svgRef.current);

        svg.selectAll<SVGGElement, GraphNode>('.node')
            .select('circle')
            .attr('stroke', (d) => {
                if (selectedNode?.id === d.id) return '#f59e0b';
                if (hoveredNode?.id === d.id) return '#3b82f6';
                return '#fff';
            })
            .attr('stroke-width', (d) => {
                if (selectedNode?.id === d.id || hoveredNode?.id === d.id) return 4;
                return 2.5;
            });

        svg.selectAll<SVGPathElement, GraphLink>('.edge')
            .attr('stroke', (d) => {
                const isActive = selectedEdge?.source === d.source && selectedEdge?.target === d.target;
                if (isActive) return '#f59e0b';
                return INTENT_COLORS[d.intent as CitationIntent];
            })
            .attr('stroke-width', (d) => {
                const isActive = selectedEdge?.source === d.source && selectedEdge?.target === d.target;
                if (isActive) return 4;
                return d.intent === 'UNKNOWN' ? 1.5 : 2.5;
            })
            .attr('opacity', (d) => {
                const isActive = selectedEdge?.source === d.source && selectedEdge?.target === d.target;
                return isActive ? 1.0 : 0.7;
            });

    }, [selectedNode, hoveredNode, selectedEdge]);

    return (
        <div className="graph-canvas" ref={containerRef}>
            {nodes.length === 0 ? (
                <div className="empty-state">
                    <div className="empty-icon">--</div>
                    <h3>{t('graphCanvas.emptyTitle')}</h3>
                    <p>{t('graphCanvas.emptyDescription')}</p>
                </div>
            ) : visibleNodes.length === 0 ? (
                <div className="empty-state">
                    <div className="empty-icon">--</div>
                    <h3>{t('graphCanvas.filterNoResult')}</h3>
                    <p>{t('graphCanvas.filterHint')}</p>
                </div>
            ) : (
                <>
                    <svg ref={svgRef} />
                    <div className="legend">
                        <div className="legend-title">{t('graphCanvas.legendTitle')}</div>
                        <div className="legend-item">
                            <span className="legend-arrow support">→</span>
                            <span>{t('graphCanvas.support')}</span>
                        </div>
                        <div className="legend-item">
                            <span className="legend-arrow oppose">→</span>
                            <span>{t('graphCanvas.oppose')}</span>
                        </div>
                        <div className="legend-item">
                            <span className="legend-arrow neutral">→</span>
                            <span>{t('graphCanvas.neutral')}</span>
                        </div>
                        <div className="legend-item">
                            <span className="legend-arrow unknown">⇢</span>
                            <span>{t('graphCanvas.unknown')}</span>
                        </div>
                        <div className="legend-note">
                            {t('graphCanvas.arrowNote')}
                        </div>
                        {clusters.length > 0 && (
                            <div className="legend-note" style={{ color: '#faad14', marginTop: 4 }}>
                                {t('graphCanvas.clusterNote')}
                            </div>
                        )}
                    </div>
                    <div className="stats">
                        {visibleNodes.length}{t('graphCanvas.papers')} · {visibleLinks.length}{t('graphCanvas.citations')}
                    </div>
                </>
            )}
        </div>
    );
};

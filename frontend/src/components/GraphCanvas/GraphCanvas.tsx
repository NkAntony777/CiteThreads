/**
 * GraphCanvas Component - D3.js DAG Visualization
 * Improved version with better layout, arrows, and interactions
 */
import React, { useEffect, useRef, useCallback, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import * as d3 from 'd3';
import { useGraphStore } from '../../stores/graphStore';
import type { GraphNode, CitationIntent } from '../../types';
import { applyGraphFilters } from '../../utils/graphFilters';
import './GraphCanvas.css';

// Intent colors
const INTENT_COLORS: Record<CitationIntent, string> = {
    SUPPORT: '#22c55e',   // Green
    OPPOSE: '#ef4444',    // Red  
    NEUTRAL: '#6b7280',   // Gray
    UNKNOWN: '#9ca3af',   // Light gray
};



export const GraphCanvas: React.FC = () => {
    const { t } = useTranslation();
    const svgRef = useRef<SVGSVGElement>(null);
    const containerRef = useRef<HTMLDivElement>(null);

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

    // Render graph with D3
    const renderGraph = useCallback(() => {
        if (!svgRef.current || !containerRef.current || visibleNodes.length === 0) return;

        const container = containerRef.current;
        const width = container.clientWidth;
        const height = container.clientHeight;

        // Clear previous content
        d3.select(svgRef.current).selectAll('*').remove();

        const svg = d3.select(svgRef.current)
            .attr('width', width)
            .attr('height', height);

        // Create zoom behavior
        const zoom = d3.zoom<SVGSVGElement, unknown>()
            .scaleExtent([0.1, 4])
            .on('zoom', (event) => {
                g.attr('transform', event.transform);
            });

        svg.call(zoom);

        // Main group for zoom/pan
        const g = svg.append('g');

        // Calculate bounds for auto-fit
        const xExtent = d3.extent(visibleNodes, d => d.x) as [number, number];
        const yExtent = d3.extent(visibleNodes, d => d.y) as [number, number];
        const graphWidth = xExtent[1] - xExtent[0] + 300;
        const graphHeight = yExtent[1] - yExtent[0] + 200;

        // Initial transform to fit and center the graph
        const scale = Math.min(width / graphWidth, height / graphHeight, 0.8);
        const centerX = (width - graphWidth * scale) / 2 - xExtent[0] * scale + 100;
        const centerY = (height - graphHeight * scale) / 2 - yExtent[0] * scale + 50;

        svg.call(zoom.transform, d3.zoomIdentity.translate(centerX, centerY).scale(scale));

        // Arrow marker definitions - larger and more visible
        const defs = svg.append('defs');

        ['SUPPORT', 'OPPOSE', 'NEUTRAL', 'UNKNOWN'].forEach(intent => {
            defs.append('marker')
                .attr('id', `arrow-${intent}`)
                .attr('viewBox', '0 -6 12 12')
                .attr('refX', 20)  // Offset from node edge
                .attr('refY', 0)
                .attr('markerWidth', 10)
                .attr('markerHeight', 10)
                .attr('orient', 'auto')
                .append('path')
                .attr('fill', INTENT_COLORS[intent as CitationIntent])
                .attr('d', 'M0,-6L12,0L0,6Z');
        });

        // Create node map for quick lookup
        const nodeMap = new Map(visibleNodes.map(n => [n.id, n]));

        // Draw edges
        const edgeGroup = g.append('g').attr('class', 'edges');

        const edgeSelection = edgeGroup.selectAll('g')
            .data(visibleLinks)
            .join('g')
            .attr('class', 'edge-group');

        // Edge paths
        edgeSelection.append('path')
            .attr('class', 'edge')
            .attr('d', (d) => {
                const source = nodeMap.get(d.source);
                const target = nodeMap.get(d.target);
                if (!source || !target) return '';

                // Calculate control point for curved line
                const dx = target.x - source.x;
                const dy = target.y - source.y;
                const dist = Math.sqrt(dx * dx + dy * dy);

                // Curve offset perpendicular to the line
                const curveOffset = Math.min(dist * 0.2, 50);
                const midX = (source.x + target.x) / 2;
                const midY = (source.y + target.y) / 2;

                // Perpendicular direction
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

        // Add tooltip for edge reasoning
        edgeSelection.append('title')
            .text(d => `${d.intent}\n${d.reasoning || t('graphCanvas.noDetail')}`);

        // Draw nodes - NO hover transform to prevent trembling
        const nodeGroup = g.append('g').attr('class', 'nodes');

        const nodeSelection = nodeGroup.selectAll('g')
            .data(visibleNodes)
            .join('g')
            .attr('class', 'node')
            .attr('transform', d => `translate(${d.x}, ${d.y})`)
            .style('cursor', 'pointer');

        // Node circles
        nodeSelection.append('circle')
            .attr('class', 'node-circle')
            .attr('r', d => d.size)
            .attr('fill', d => {
                // If clustered, use cluster color
                if (clusters.length > 0 && paperClusters[d.id] !== undefined) {
                    const clusterId = paperClusters[d.id];
                    const cluster = clusters.find(c => c.id === clusterId);
                    if (cluster) return cluster.color;
                }

                // Otherwise color based on year
                const year = d.year || 2000;
                const normalizedYear = Math.max(0, Math.min(1, (year - 1990) / 40));
                return d3.interpolateBlues(0.35 + normalizedYear * 0.5);
            })
            .attr('stroke', '#fff')
            .attr('stroke-width', 2.5)
            .attr('opacity', 0.9);

        // Year label inside node
        nodeSelection.append('text')
            .attr('class', 'node-year')
            .attr('dy', 5)
            .attr('text-anchor', 'middle')
            .text(d => d.year || '')
            .attr('font-size', d => Math.max(9, Math.min(14, d.size * 0.5)))
            .attr('fill', '#fff')
            .attr('font-weight', '600')
            .style('pointer-events', 'none');

        // Title label below node
        nodeSelection.append('text')
            .attr('class', 'node-label')
            .attr('dy', d => d.size + 16)
            .attr('text-anchor', 'middle')
            .text(d => {
                const maxLen = 25;
                return d.title.length > maxLen ? d.title.slice(0, maxLen) + '...' : d.title;
            })
            .attr('font-size', 11)
            .attr('fill', '#374151')
            .style('pointer-events', 'none');

        // Event handlers - attached to circles only to prevent trembling
        nodeSelection.select('.node-circle')
            .on('click', (event, d) => {
                event.stopPropagation();
                setSelectedNode(d);
            })
            .on('mouseenter', (_, d) => {
                setHoveredNode(d);
            })
            .on('mouseleave', () => {
                setHoveredNode(null);
            });

        // Click on background to deselect
        svg.on('click', () => {
            setSelectedNode(null);
            setSelectedEdge(null); // Deselect edges too
        });

    }, [visibleNodes, visibleLinks, clusters, paperClusters, setSelectedNode, setHoveredNode, setSelectedEdge, t]);

    // Initial render
    useEffect(() => {
        renderGraph();
    }, [renderGraph]);

    // Handle window resize
    useEffect(() => {
        const handleResize = () => {
            renderGraph();
        };
        window.addEventListener('resize', handleResize);
        return () => window.removeEventListener('resize', handleResize);
    }, [renderGraph]);

    // Highlight selected/hovered nodes and edges
    useEffect(() => {
        if (!svgRef.current) return;

        const svg = d3.select(svgRef.current);

        // Highlight nodes
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

        // Highlight edges
        svg.selectAll<SVGPathElement, any>('.edge')
            .attr('stroke', (d) => {
                const isActive = selectedEdge?.source === d.source && selectedEdge?.target === d.target;
                if (isActive) return '#f59e0b'; // Highlight color
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

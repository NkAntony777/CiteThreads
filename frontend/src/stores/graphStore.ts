/**
 * Graph Store - Global state management using Zustand
 */
import { create } from 'zustand';
import type { Paper, Project, GraphNode, GraphLink, CitationIntent, CrawlProgress, ClusterInfo } from '../types';
import { projectApi } from '../services/api';

interface GraphState {
    // Current project
    currentProject: Project | null;

    // Computed graph data with layout
    nodes: GraphNode[];
    links: GraphLink[];

    // UI state
    selectedNode: GraphNode | null;
    hoveredNode: GraphNode | null;
    selectedEdge: GraphLink | null;

    // Build progress
    buildProgress: CrawlProgress | null;
    isBuilding: boolean;

    // Filters
    yearRange: [number, number] | null;
    intentFilter: CitationIntent[];
    clusterFilter: number[];

    // Clustering
    clusters: ClusterInfo[];
    paperClusters: Record<string, number>;
    isClustering: boolean;

    // Actions
    setProject: (project: Project) => void;
    clearProject: () => void;

    setSelectedNode: (node: GraphNode | null) => void;
    setHoveredNode: (node: GraphNode | null) => void;
    setSelectedEdge: (edge: GraphLink | null) => void;

    setBuildProgress: (progress: CrawlProgress | null) => void;
    setIsBuilding: (building: boolean) => void;

    setYearRange: (range: [number, number] | null) => void;
    setIntentFilter: (intents: CitationIntent[]) => void;
    setClusterFilter: (clusters: number[]) => void;

    updateEdgeIntent: (source: string, target: string, intent: CitationIntent) => void;
    deleteNode: (nodeId: string) => Promise<void>;

    loadProject: (projectId: string) => Promise<void>;

    performClustering: (nClusters: number, useAbstract: boolean) => Promise<void>;
    analyzeProject: () => Promise<void>;
}

// Calculate node size based on citation count (log scale)
const calculateNodeSize = (citationCount: number): number => {
    return Math.max(12, Math.min(35, 12 + Math.log10(citationCount + 1) * 10));
};

// Build layout from project data - improved algorithm
const buildLayout = (project: Project): { nodes: GraphNode[]; links: GraphLink[] } => {
    const { nodes: papers, edges } = project.graph;

    if (papers.length === 0) {
        return { nodes: [], links: [] };
    }

    // Group papers by year
    const yearGroups: Map<number, Paper[]> = new Map();
    let minYear = Infinity;
    let maxYear = -Infinity;

    papers.forEach(paper => {
        const year = paper.year || 2000;
        minYear = Math.min(minYear, year);
        maxYear = Math.max(maxYear, year);

        if (!yearGroups.has(year)) {
            yearGroups.set(year, []);
        }
        yearGroups.get(year)!.push(paper);
    });

    // Sort each year group by citation count
    yearGroups.forEach(group => {
        group.sort((a, b) => b.citation_count - a.citation_count);
    });

    // Layout configuration
    const CANVAS_WIDTH = 2000;
    const CANVAS_HEIGHT = 1200;
    const PADDING_X = 150;
    const PADDING_Y = 100;
    const MIN_NODE_SPACING_Y = 140; // Increased to accommodate labels

    // Calculate X position based on year
    const yearRange = maxYear - minYear || 1;
    const xScale = (year: number) => PADDING_X + ((year - minYear) / yearRange) * (CANVAS_WIDTH - PADDING_X * 2);

    // Calculate Y positions with proper spacing
    const graphNodes: GraphNode[] = [];
    const nodeMap = new Map<string, GraphNode>();

    // Get sorted years
    const sortedYears = Array.from(yearGroups.keys()).sort((a, b) => a - b);

    sortedYears.forEach(year => {
        const group = yearGroups.get(year)!;
        const nodeCount = group.length;

        // Calculate required height for this group
        // const requiredHeight = nodeCount * MIN_NODE_SPACING_Y;
        const availableHeight = CANVAS_HEIGHT - PADDING_Y * 2;

        // Use spacing that prevents overlap
        const actualSpacing = Math.max(MIN_NODE_SPACING_Y, availableHeight / (nodeCount + 1));
        const startY = PADDING_Y + (availableHeight - (nodeCount - 1) * actualSpacing) / 2;

        group.forEach((paper, index) => {
            const size = calculateNodeSize(paper.citation_count);
            const node: GraphNode = {
                ...paper,
                x: xScale(year),
                y: startY + index * actualSpacing,
                size,
            };
            graphNodes.push(node);
            nodeMap.set(paper.id, node);
        });
    });

    // Build links with node references
    const graphLinks: GraphLink[] = edges.map(edge => ({
        ...edge,
        sourceNode: nodeMap.get(edge.source),
        targetNode: nodeMap.get(edge.target),
    }));

    return { nodes: graphNodes, links: graphLinks };
};

export const useGraphStore = create<GraphState>((set, get) => ({
    // Initial state
    currentProject: null,
    nodes: [],
    links: [],
    selectedNode: null,
    hoveredNode: null,
    selectedEdge: null,
    buildProgress: null,
    isBuilding: false,
    yearRange: null,
    intentFilter: [],
    clusterFilter: [],
    clusters: [],
    paperClusters: {},
    isClustering: false,

    // Actions
    setProject: (project) => {
        const { nodes, links } = buildLayout(project);
        set({
            currentProject: project,
            nodes,
            links,
            yearRange: null,
            intentFilter: [],
            clusterFilter: [],
        });
    },

    clearProject: () => {
        set({
            currentProject: null,
            nodes: [],
            links: [],
            selectedNode: null,
            hoveredNode: null,
            selectedEdge: null,
            yearRange: null,
            intentFilter: [],
            clusterFilter: [],
        });
    },

    setSelectedNode: (node) => set({ selectedNode: node, selectedEdge: null }),
    setHoveredNode: (node) => set({ hoveredNode: node }),
    setSelectedEdge: (edge) => set({ selectedEdge: edge, selectedNode: null }),

    setBuildProgress: (progress) => set({ buildProgress: progress }),
    setIsBuilding: (building) => set({ isBuilding: building }),

    setYearRange: (range) => set({ yearRange: range }),
    setIntentFilter: (intents) => set({ intentFilter: intents }),
    setClusterFilter: (clusters) => set({ clusterFilter: clusters }),

    loadProject: async (projectId: string) => {
        try {
            const project = await projectApi.get(projectId);
            const { nodes, links } = buildLayout(project);
            set({
                currentProject: project,
                nodes,
                links,
                clusters: [],
                paperClusters: {},
                yearRange: null,
                intentFilter: [],
                clusterFilter: [],
            });
        } catch (e) {
            console.error('Failed to load project:', e);
            throw e;
        }
    },

    performClustering: async (nClusters: number, useAbstract: boolean) => {
        const { currentProject } = get();
        if (!currentProject) return;

        set({ isClustering: true });
        try {
            const result = await projectApi.cluster(currentProject.metadata.id, nClusters, useAbstract);
            if (result.success) {
                set({
                    clusters: result.clusters,
                    paperClusters: result.paper_clusters
                });
            }
        } catch (e) {
            console.error('Clustering failed:', e);
            throw e;
        } finally {
            set({ isClustering: false });
        }
    },

    analyzeProject: async () => {
        const { currentProject } = get();
        if (!currentProject) return;

        return new Promise<void>((resolve, reject) => {
            let eventSource: EventSource;

            // Handle API call failure
            const handleError = (error: any) => {
                if (eventSource) eventSource.close();
                console.error('Analysis failed:', error);
                reject(error);
            };

            try {
                // 1. Setup SSE listener
                eventSource = projectApi.subscribeProgress(
                    currentProject.metadata.id,
                    (progress: CrawlProgress) => {
                        set({ buildProgress: progress });

                        if (progress.status === 'completed' || progress.status === 'failed') {
                            eventSource.close();

                            if (progress.status === 'completed') {
                                // Reload project to get new metadata
                                get().loadProject(currentProject.metadata.id)
                                    .then(() => resolve())
                                    .catch(handleError);
                            } else {
                                handleError(new Error(progress.message || 'Analysis failed'));
                            }
                        }
                    }
                );

                // 2. Trigger analysis
                projectApi.analyze(currentProject.metadata.id)
                    .catch(e => {
                        // If API call fails immediately
                        handleError(e);
                    });

            } catch (e) {
                handleError(e);
            }
        });
    },


    updateEdgeIntent: (source, target, intent) => {
        const { links, currentProject } = get();

        // Update links
        const updatedLinks = links.map(link => {
            if (link.source === source && link.target === target) {
                return { ...link, intent, confidence: 1.0 };
            }
            return link;
        });

        // Update project graph
        if (currentProject) {
            const updatedEdges = currentProject.graph.edges.map(edge => {
                if (edge.source === source && edge.target === target) {
                    return { ...edge, intent, confidence: 1.0 };
                }
                return edge;
            });

            set({
                links: updatedLinks,
                currentProject: {
                    ...currentProject,
                    graph: {
                        ...currentProject.graph,
                        edges: updatedEdges,
                    },
                },
            });
        } else {
            set({ links: updatedLinks });
        }
    },

    deleteNode: async (nodeId) => {
        const { currentProject, nodes, links } = get();
        if (!currentProject) return;

        try {
            await projectApi.deletePaper(currentProject.metadata.id, nodeId);

            // Update local state
            const updatedNodes = nodes.filter(n => n.id !== nodeId);

            // Helper to get ID from source/target which might be object or string
            const getId = (item: any) => (typeof item === 'object' && item !== null && 'id' in item) ? item.id : item;

            const updatedLinks = links.filter(l => getId(l.source) !== nodeId && getId(l.target) !== nodeId);

            // Update project graph
            const updatedEdges = currentProject.graph.edges.filter(
                e => e.source !== nodeId && e.target !== nodeId
            );
            const updatedPapers = currentProject.graph.nodes.filter(
                p => p.id !== nodeId
            );

            set({
                nodes: updatedNodes,
                links: updatedLinks,
                selectedNode: null, // Deselect
                currentProject: {
                    ...currentProject,
                    graph: {
                        ...currentProject.graph,
                        nodes: updatedPapers,
                        edges: updatedEdges,
                    },
                },
            });
        } catch (e) {
            console.error('Failed to delete node:', e);
            throw e;
        }
    },
}));

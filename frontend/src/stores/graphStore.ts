/**
 * Project Store — minimal state for the simplified writing flow.
 *
 * 2026-06 refactor: stripped out every graph-viz specific field
 * (nodes, links, selectedNode, hoveredNode, selectedEdge,
 * layoutMode, yearRange, intentFilter, clusterFilter, clusters,
 * paperClusters, isClustering) and their actions
 * (setSelectedNode, setHoveredNode, setSelectedEdge,
 * setLayoutMode, setYearRange, setIntentFilter,
 * setClusterFilter, updateEdgeIntent, deleteNode,
 * performClustering, analyzeProject). The store is now just
 * enough to:
 *   - hold the current Project (so the writing view can read its
 *     graph nodes as hidden context for the agent)
 *   - track the in-flight crawl progress
 *   - reload a project from the server
 *
 * The deprecated shims at the bottom of the file keep the
 * on-disk copies of GraphCanvas / NodePanel / EdgePanel /
 * ClusterPanel compilable so they can stay in git history. They
 * no-op; new code should read from currentProject directly.
 */
import { create } from 'zustand';
import type {
    Project,
    CrawlProgress,
    GraphNode,
    GraphLink,
    CitationIntent,
    ClusterInfo,
} from '../types';
import { projectApi } from '../services/api';
import { type LayoutMode } from '../utils/graphLayouts';

interface GraphState {
    // Current project (hidden graph data lives in project.graph)
    currentProject: Project | null;

    // Build / crawl progress
    buildProgress: CrawlProgress | null;
    isBuilding: boolean;

    // Actions
    setProject: (project: Project) => void;
    setProjectMetadata: (metadata: Project['metadata']) => void;
    clearProject: () => void;
    setBuildProgress: (progress: CrawlProgress | null) => void;
    setIsBuilding: (building: boolean) => void;
    loadProject: (projectId: string) => Promise<void>;

    // ---- Deprecated shims (kept for on-disk legacy components) ----
    /** @deprecated unused since 2026-06 refactor; use currentProject.graph.nodes. */
    nodes: GraphNode[];
    /** @deprecated unused since 2026-06 refactor; use currentProject.graph.edges. */
    links: GraphLink[];
    /** @deprecated unused since 2026-06 refactor. */
    selectedNode: GraphNode | null;
    /** @deprecated unused since 2026-06 refactor. */
    hoveredNode: GraphNode | null;
    /** @deprecated unused since 2026-06 refactor. */
    selectedEdge: GraphLink | null;
    /** @deprecated unused since 2026-06 refactor. */
    layoutMode: LayoutMode;
    /** @deprecated unused since 2026-06 refactor. */
    yearRange: [number, number] | null;
    /** @deprecated unused since 2026-06 refactor. */
    intentFilter: CitationIntent[];
    /** @deprecated unused since 2026-06 refactor. */
    clusterFilter: number[];
    /** @deprecated unused since 2026-06 refactor. */
    clusters: ClusterInfo[];
    /** @deprecated unused since 2026-06 refactor. */
    paperClusters: Record<string, number>;
    /** @deprecated unused since 2026-06 refactor. */
    isClustering: boolean;
    /** @deprecated no-op since 2026-06 refactor. */
    setSelectedNode: (node: GraphNode | null) => void;
    /** @deprecated no-op since 2026-06 refactor. */
    setHoveredNode: (node: GraphNode | null) => void;
    /** @deprecated no-op since 2026-06 refactor. */
    setSelectedEdge: (edge: GraphLink | null) => void;
    /** @deprecated no-op since 2026-06 refactor. */
    setLayoutMode: (mode: LayoutMode) => void;
    /** @deprecated no-op since 2026-06 refactor. */
    setYearRange: (range: [number, number] | null) => void;
    /** @deprecated no-op since 2026-06 refactor. */
    setIntentFilter: (intents: CitationIntent[]) => void;
    /** @deprecated no-op since 2026-06 refactor. */
    setClusterFilter: (clusters: number[]) => void;
    /** @deprecated no-op since 2026-06 refactor. */
    updateEdgeIntent: (source: string, target: string, intent: CitationIntent) => void;
    /** @deprecated no-op since 2026-06 refactor. */
    deleteNode: (nodeId: string) => Promise<void>;
    /** @deprecated no-op since 2026-06 refactor. */
    performClustering: (nClusters: number, useAbstract: boolean) => Promise<void>;
    /** @deprecated no-op since 2026-06 refactor. */
    analyzeProject: () => Promise<void>;
}

export const useGraphStore = create<GraphState>((set) => ({
    // Active state
    currentProject: null,
    buildProgress: null,
    isBuilding: false,

    // Active actions
    setProject: (project) => set({ currentProject: project }),
    setProjectMetadata: (metadata) =>
        set((s) =>
            s.currentProject
                ? { currentProject: { ...s.currentProject, metadata } }
                : s,
        ),
    clearProject: () =>
        set({
            currentProject: null,
            buildProgress: null,
            isBuilding: false,
        }),
    setBuildProgress: (progress) => set({ buildProgress: progress }),
    setIsBuilding: (building) => set({ isBuilding: building }),

    loadProject: async (projectId: string) => {
        try {
            const project = await projectApi.get(projectId);
            set({ currentProject: project });
        } catch (e) {
            console.error('Failed to load project:', e);
            throw e;
        }
    },

    // ---- Deprecated no-op shims ----
    nodes: [],
    links: [],
    selectedNode: null,
    hoveredNode: null,
    selectedEdge: null,
    layoutMode: 'timeline' as LayoutMode,
    yearRange: null,
    intentFilter: [],
    clusterFilter: [],
    clusters: [],
    paperClusters: {},
    isClustering: false,
    setSelectedNode: () => undefined,
    setHoveredNode: () => undefined,
    setSelectedEdge: () => undefined,
    setLayoutMode: () => undefined,
    setYearRange: () => undefined,
    setIntentFilter: () => undefined,
    setClusterFilter: () => undefined,
    updateEdgeIntent: () => undefined,
    deleteNode: async () => undefined,
    performClustering: async () => undefined,
    analyzeProject: async () => undefined,
}));

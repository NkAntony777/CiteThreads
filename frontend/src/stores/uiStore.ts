/**
 * UI Store - Selection and UI state management
 */
import { create } from 'zustand';
import type { GraphNode, GraphLink } from '../types';

interface UIState {
    // Selection state
    selectedNode: GraphNode | null;
    hoveredNode: GraphNode | null;
    selectedEdge: GraphLink | null;
    
    // Panel visibility (for future use)
    nodePanelVisible: boolean;
    edgePanelVisible: boolean;
    
    // Actions
    setSelectedNode: (node: GraphNode | null) => void;
    setHoveredNode: (node: GraphNode | null) => void;
    setSelectedEdge: (edge: GraphLink | null) => void;
    setNodePanelVisible: (visible: boolean) => void;
    setEdgePanelVisible: (visible: boolean) => void;
    
    // Computed
    clearSelection: () => void;
}

export const useUIStore = create<UIState>((set) => ({
    // Initial state
    selectedNode: null,
    hoveredNode: null,
    selectedEdge: null,
    nodePanelVisible: false,
    edgePanelVisible: false,
    
    // Actions
    setSelectedNode: (node) => set({ 
        selectedNode: node, 
        selectedEdge: null,
        nodePanelVisible: node !== null 
    }),
    
    setHoveredNode: (node) => set({ hoveredNode: node }),
    
    setSelectedEdge: (edge) => set({ 
        selectedEdge: edge, 
        selectedNode: null,
        edgePanelVisible: edge !== null 
    }),
    
    setNodePanelVisible: (visible) => set({ nodePanelVisible: visible }),
    setEdgePanelVisible: (visible) => set({ edgePanelVisible: visible }),
    
    clearSelection: () => set({
        selectedNode: null,
        selectedEdge: null,
        nodePanelVisible: false,
        edgePanelVisible: false,
    }),
}));

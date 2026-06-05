/**
 * Filter Store - Graph filtering state management
 */
import { create } from 'zustand';
import type { CitationIntent } from '../types';

interface FilterState {
    // Filter state
    yearRange: [number, number] | null;
    intentFilter: CitationIntent[];
    clusterFilter: number[];
    
    // Actions
    setYearRange: (range: [number, number] | null) => void;
    setIntentFilter: (intents: CitationIntent[]) => void;
    setClusterFilter: (clusters: number[]) => void;
    
    // Computed
    clearFilters: () => void;
    hasActiveFilters: () => boolean;
}

export const useFilterStore = create<FilterState>((set, get) => ({
    // Initial state
    yearRange: null,
    intentFilter: [],
    clusterFilter: [],
    
    // Actions
    setYearRange: (range) => set({ yearRange: range }),
    setIntentFilter: (intents) => set({ intentFilter: intents }),
    setClusterFilter: (clusters) => set({ clusterFilter: clusters }),
    
    // Computed
    clearFilters: () => set({
        yearRange: null,
        intentFilter: [],
        clusterFilter: [],
    }),
    
    hasActiveFilters: () => {
        const state = get();
        return state.yearRange !== null || 
               state.intentFilter.length > 0 || 
               state.clusterFilter.length > 0;
    },
}));

/**
 * API Service - Handles all backend API calls
 */
import axios from 'axios';
import type {
    Paper,
    PaperSearchResponse,
    ProjectMetadata,
    Project,
    CreateProjectRequest,
    CrawlProgress,
    CitationIntent
} from '../types';

const api = axios.create({
    baseURL: '/api',
    timeout: 120000,  // 120 seconds - allow time for rate limit waits
});

// Paper API
export const paperApi = {
    /**
     * Search for papers by DOI, arXiv ID, or title
     */
    search: async (query: string, queryType: string = 'auto', limit: number = 10): Promise<PaperSearchResponse> => {
        const response = await api.post<PaperSearchResponse>('/papers/search', {
            query,
            query_type: queryType,
            limit,
        });
        return response.data;
    },

    /**
     * Get paper by ID
     */
    getById: async (paperId: string): Promise<Paper> => {
        const response = await api.get<Paper>(`/papers/${encodeURIComponent(paperId)}`);
        return response.data;
    },
};

// Project API
export const projectApi = {
    /**
     * Create a new project and start building graph
     */
    create: async (request: CreateProjectRequest): Promise<ProjectMetadata> => {
        const response = await api.post<ProjectMetadata>('/projects', request);
        return response.data;
    },

    /**
     * List all projects
     */
    list: async (): Promise<ProjectMetadata[]> => {
        const response = await api.get<ProjectMetadata[]>('/projects');
        return response.data;
    },

    /**
     * Get project with full graph data
     */
    get: async (projectId: string): Promise<Project> => {
        const response = await api.get<Project>(`/projects/${projectId}`);
        return response.data;
    },

    /**
     * Get project build status
     */
    getStatus: async (projectId: string): Promise<CrawlProgress> => {
        const response = await api.get<CrawlProgress>(`/projects/${projectId}/status`);
        return response.data;
    },

    /**
     * Subscribe to build progress via SSE
     */
    subscribeProgress: (projectId: string, onProgress: (progress: CrawlProgress) => void): EventSource => {
        const eventSource = new EventSource(`/api/projects/${projectId}/stream`);

        eventSource.onmessage = (event) => {
            try {
                const progress = JSON.parse(event.data) as CrawlProgress;
                onProgress(progress);
            } catch (e) {
                console.error('Failed to parse progress:', e);
            }
        };

        eventSource.onerror = () => {
            eventSource.close();
        };

        return eventSource;
    },

    /**
     * Update edge annotation
     */
    updateEdge: async (
        projectId: string,
        source: string,
        target: string,
        intent: CitationIntent,
        note?: string
    ): Promise<void> => {
        await api.patch(`/projects/${projectId}/edges`, {
            intent,
            note,
        }, {
            params: { source, target },
        });
    },

    /**
     * Export project
     */
    exportUrl: (projectId: string, format: 'bibtex' | 'ris' | 'json' = 'bibtex'): string => {
        return `/api/projects/${projectId}/export?format=${format}`;
    },

    /**
     * Delete project
     */
    delete: async (projectId: string): Promise<void> => {
        await api.delete(`/projects/${projectId}`);
    },

    /**
     * Delete a paper from project
     */
    deletePaper: async (projectId: string, paperId: string): Promise<void> => {
        await api.delete(`/projects/${projectId}/papers/${encodeURIComponent(paperId)}`);
    },

    /**
     * Rename project
     */
    rename: async (projectId: string, name: string): Promise<{ status: string, name: string }> => {
        const response = await api.patch<{ status: string, name: string }>(`/projects/${projectId}/rename`, { name });
        return response.data;
    },

    /**
     * Cluster project papers
     */
    cluster: async (projectId: string, nClusters: number, useAbstract: boolean): Promise<any> => {
        const response = await api.post(`/projects/${projectId}/cluster`, {
            n_clusters: nClusters,
            use_abstract: useAbstract,
        });
        return response.data;
    },

    /**
     * Trigger AI intent analysis
     */
    analyze: async (projectId: string): Promise<{ status: string, message: string }> => {
        const response = await api.post<{ status: string, message: string }>(`/projects/${projectId}/analyze`);
        return response.data;
    },
};

export default api;

/**
 * Writing API Service - Handles AI writing assistant API calls
 */
import axios from 'axios';
import type { Paper, Reference, ChatMessage } from '../types';

const api = axios.create({
    baseURL: '/api',
    timeout: 300000,  // 5 minutes for AI generation and crawling
});

export const writingApi = {
    /**
     * Get all references for a project
     */
    getReferences: async (projectId: string): Promise<{ references: Reference[], total: number }> => {
        const response = await api.get(`/writing/projects/${projectId}/references`);
        return response.data;
    },

    /**
     * Add a reference from graph
     */
    addReference: async (projectId: string, paperId: string, source: string = 'graph'): Promise<any> => {
        const response = await api.post(`/writing/projects/${projectId}/references`, {
            paper_id: paperId,
            source,
        });
        return response.data;
    },

    /**
     * Add a reference from search results
     */
    addReferenceFromSearch: async (projectId: string, paper: Paper): Promise<any> => {
        const response = await api.post(`/writing/projects/${projectId}/references/from-search`, paper);
        return response.data;
    },

    /**
     * Remove a reference
     */
    removeReference: async (projectId: string, refId: string): Promise<void> => {
        await api.delete(`/writing/projects/${projectId}/references/${refId}`);
    },

    /**
     * Generate literature review
     */
    generateReview: async (
        projectId: string,
        referenceIds?: string[],
        style: string = 'academic',
        includeGraphInfo: boolean = true
    ): Promise<{ success: boolean, review: any }> => {
        const response = await api.post(`/writing/projects/${projectId}/review/generate`, {
            reference_ids: referenceIds,
            style,
            include_graph_info: includeGraphInfo,
        });
        return response.data;
    },

    /**
     * Get saved literature review
     */
    getReview: async (projectId: string): Promise<{ content: string }> => {
        const response = await api.get(`/writing/projects/${projectId}/review`);
        return response.data;
    },

    /**
     * Save literature review
     */
    saveReview: async (projectId: string, content: string): Promise<{ success: boolean }> => {
        const response = await api.post(`/writing/projects/${projectId}/review`, { content });
        return response.data;
    },

    /**
     * Chat with writing assistant
     */
    chat: async (
        projectId: string,
        message: string,
        history?: { role: string, content: string }[]
    ): Promise<{ success: boolean, message: ChatMessage }> => {
        const response = await api.post(`/writing/projects/${projectId}/writing/chat`, {
            message,
            history,
        });
        return response.data;
    },

    /**
     * Get chat history
     */
    getChatHistory: async (projectId: string): Promise<{ history: ChatMessage[] }> => {
        const response = await api.get(`/writing/projects/${projectId}/chat-history`);
        return response.data;
    },

    /**
     * Save chat history
     */
    saveChatHistory: async (projectId: string, history: ChatMessage[]): Promise<{ success: boolean }> => {
        const response = await api.post(`/writing/projects/${projectId}/chat-history`, { history });
        return response.data;
    },

    /**
     * Search papers for writing
     */
    searchPapers: async (
        projectId: string,
        query: string,
        sources?: string[],
        limit: number = 10
    ): Promise<{ success: boolean, papers: Paper[], total: number }> => {
        const response = await api.post(`/writing/projects/${projectId}/writing/search-papers`, {
            query,
            sources,
            limit,
        });
        return response.data;
    },

    /**
     * Generate a section
     */
    generateSection: async (
        projectId: string,
        sectionType: string,
        outline?: string,
        context?: string
    ): Promise<{ success: boolean, section: { type: string, content: string } }> => {
        const response = await api.post(`/writing/projects/${projectId}/writing/generate-section`, {
            section_type: sectionType,
            outline,
            context,
        });
        return response.data;
    },

    /**
     * Get canvas content for a project
     */
    getCanvas: async (projectId: string): Promise<{ content: string }> => {
        const response = await api.get(`/writing/projects/${projectId}/canvas`);
        return response.data;
    },

    /**
     * Save canvas content for a project
     */
    saveCanvas: async (projectId: string, content: string): Promise<{ success: boolean }> => {
        const response = await api.post(`/writing/projects/${projectId}/canvas`, { content });
        return response.data;
    },

    /**
     * Unified paper search (multi-source, no projectId required)
     * Uses OpenAlex, DBLP, arXiv, PubMed
     */
    searchPapersUnified: async (
        query: string,
        limit: number = 10,
        sources?: string[]
    ): Promise<{ success: boolean, papers: Paper[], total: number }> => {
        const response = await api.post('/papers/search-unified', {
            query,
            limit,
            sources,
        });
        return response.data;
    },

    /**
     * Export references as BibTeX
     */
    exportBibtexUrl: (projectId: string): string => {
        return `/api/writing/projects/${projectId}/references/export/bibtex`;
    },
};

export default writingApi;

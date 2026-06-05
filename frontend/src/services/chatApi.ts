/**
 * chatApi — REST helpers for the chat-driven UI.
 *
 * Reuses the existing project endpoints where possible. The new
 * routes here are just the chat-specific ones the SmartSearchPanel
 * / WritingAssistant flow didn't need.
 */
import api from './api';
import type { Project, ProjectMetadata } from '../types';

export interface ConversationListItem {
    id: string;
    name: string;
    updated_at: string;
    status: string;
    paper_count: number;
    section_draft_count: number;
    last_message_preview?: string;
}

export interface ConversationListResponse {
    items: ConversationListItem[];
}

export const chatApi = {
    /**
     * Create a fresh conversation (project) and return it as a
     * full Project (the POST endpoint returns just ProjectMetadata;
     * we follow up with a GET /{id}/full so the chat UI can rely
     * on the `metadata` + `graph` + `chat_history` shape).
     */
    create: async (name?: string): Promise<Project> => {
        const created = await api.post<ProjectMetadata>('/projects', {
            seed_paper_id: 'placeholder',  // blank chat doesn't need a real seed
            name: name || '新对话',
        });
        const full = await api.get<Project>(`/projects/${created.data.id}/full`);
        return full.data;
    },

    /**
     * List every conversation (project) as a lightweight summary.
     * Avoids shipping the full chat history.
     */
    list: async (): Promise<ConversationListItem[]> => {
        const resp = await api.get<ConversationListResponse>(
            '/projects/conversations',
        );
        return resp.data.items || [];
    },

    /**
     * Fetch a single conversation with its full chat_history.
     */
    getFull: async (id: string): Promise<Project> => {
        const resp = await api.get<Project>(`/projects/${id}/full`);
        return resp.data;
    },

    rename: async (id: string, name: string): Promise<ProjectMetadata> => {
        const resp = await api.patch<ProjectMetadata>(
            `/projects/${id}/rename`,
            { name },
        );
        return resp.data;
    },

    remove: async (id: string): Promise<void> => {
        await api.delete(`/projects/${id}`);
    },
};

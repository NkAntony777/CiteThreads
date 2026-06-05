/**
 * Tests for graphStore
 */
import { describe, it, expect, beforeEach } from 'vitest'
import { useGraphStore } from '../stores/graphStore'
import type { Paper, Project, CitationEdge } from '../types'

// Create a sample project for testing
const createMockProject = (): Project => ({
    metadata: {
        id: 'test-project-1',
        name: 'Test Project',
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-01T00:00:00Z',
        config: {
            seed_paper_id: 'test-paper-1',
            depth: 2,
            direction: 'both',
        },
        status: 'completed',
    },
    graph: {
        nodes: [
            {
                id: 'paper-1',
                title: 'Test Paper 1',
                authors: ['Author 1'],
                year: 2024,
                citation_count: 100,
                reference_count: 50,
                fields: ['CS'],
            },
            {
                id: 'paper-2',
                title: 'Test Paper 2',
                authors: ['Author 2'],
                year: 2023,
                citation_count: 50,
                reference_count: 30,
                fields: ['AI'],
            },
        ] as Paper[],
        edges: [
            {
                source: 'paper-1',
                target: 'paper-2',
                intent: 'SUPPORT',
                confidence: 0.9,
            },
        ] as CitationEdge[],
    },
})

describe('graphStore', () => {
    beforeEach(() => {
        // Reset store before each test
        useGraphStore.setState({
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
        })
    })

    it('should have correct initial state', () => {
        const state = useGraphStore.getState()
        expect(state.currentProject).toBeNull()
        expect(state.nodes).toEqual([])
        expect(state.links).toEqual([])
        expect(state.selectedNode).toBeNull()
    })

    it('should set project (slim store, no layout)', () => {
        // The 2026-06 refactor removed the layout-computed
        // nodes/links arrays. The store now just holds the
        // project; consumers read currentProject.graph directly.
        const project = createMockProject()
        const { setProject } = useGraphStore.getState()

        setProject(project)

        const state = useGraphStore.getState()
        expect(state.currentProject).toEqual(project)
        // Legacy shim — deprecated fields stay at their empty
        // defaults so the on-disk legacy components still compile.
        expect(state.nodes).toEqual([])
        expect(state.links).toEqual([])
    })

    it('should clear project', () => {
        const project = createMockProject()
        const { setProject, clearProject } = useGraphStore.getState()

        setProject(project)
        clearProject()

        const state = useGraphStore.getState()
        expect(state.currentProject).toBeNull()
        expect(state.buildProgress).toBeNull()
        expect(state.isBuilding).toBe(false)
    })

    it('should update only the metadata via setProjectMetadata', () => {
        // The slim store supports a partial update that just
        // renames a project without re-fetching the full graph.
        const project = createMockProject()
        const { setProject, setProjectMetadata } = useGraphStore.getState()
        setProject(project)

        const renamed = { ...project.metadata, name: 'Renamed' }
        setProjectMetadata(renamed)

        const state = useGraphStore.getState()
        expect(state.currentProject?.metadata.name).toBe('Renamed')
        // Graph data untouched
        expect(state.currentProject?.graph).toEqual(project.graph)
    })

    it('should set year range filter (legacy shim, no-op)', () => {
        // The yearRange field is kept on the store as a no-op
        // shim so the on-disk GraphFilters component still
        // compiles. Setting it must not throw and must not
        // surface the value back.
        const { setYearRange } = useGraphStore.getState()
        expect(() => setYearRange([2020, 2024])).not.toThrow()
        expect(useGraphStore.getState().yearRange).toBeNull()
    })
})

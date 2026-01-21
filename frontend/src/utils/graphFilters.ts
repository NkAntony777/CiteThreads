import type { CitationIntent, GraphLink, GraphNode } from '../types';

export interface GraphFilterOptions {
    yearRange: [number, number] | null;
    intentFilter: CitationIntent[];
    clusterFilter: number[];
    paperClusters: Record<string, number>;
}

export const applyGraphFilters = (
    nodes: GraphNode[],
    links: GraphLink[],
    options: GraphFilterOptions
): { nodes: GraphNode[]; links: GraphLink[] } => {
    const { yearRange, intentFilter, clusterFilter, paperClusters } = options;

    const filteredNodes = nodes.filter(node => {
        if (yearRange) {
            if (!node.year) return false;
            if (node.year < yearRange[0] || node.year > yearRange[1]) return false;
        }

        if (clusterFilter.length > 0) {
            const clusterId = paperClusters[node.id];
            if (clusterId === undefined || !clusterFilter.includes(clusterId)) {
                return false;
            }
        }

        return true;
    });

    const allowedNodeIds = new Set(filteredNodes.map(node => node.id));
    const filteredLinks = links.filter(link => {
        if (!allowedNodeIds.has(link.source) || !allowedNodeIds.has(link.target)) {
            return false;
        }
        if (intentFilter.length > 0 && !intentFilter.includes(link.intent as CitationIntent)) {
            return false;
        }
        return true;
    });

    return { nodes: filteredNodes, links: filteredLinks };
};

"""
Project Storage Service - JSON file-based storage
"""
import json
import os
import logging
from datetime import datetime
from typing import Optional, List
from pathlib import Path
import uuid

from ..models import (
    Paper, CitationEdge, GraphData, GraphStats,
    ProjectMetadata, ProjectConfig, ProjectResponse
)
from ..config import settings

logger = logging.getLogger(__name__)


class ProjectStorage:
    """File-based project storage using JSON"""
    
    def __init__(self):
        self.projects_dir = Path(settings.data_dir) / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)
    
    def _get_project_dir(self, project_id: str) -> Path:
        """Get project directory path"""
        return self.projects_dir / project_id
    
    def create_project(
        self,
        seed_paper_id: str,
        name: Optional[str] = None,
        depth: int = 2,
        direction: str = "both"
    ) -> ProjectMetadata:
        """Create a new project"""
        project_id = str(uuid.uuid4())[:8]
        project_dir = self._get_project_dir(project_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        
        # Default name from seed paper ID
        if not name:
            name = f"Project {project_id}"
        
        now = datetime.now()
        config = ProjectConfig(
            seed_paper_id=seed_paper_id,
            depth=depth,
            direction=direction
        )
        
        metadata = ProjectMetadata(
            id=project_id,
            name=name,
            created_at=now,
            updated_at=now,
            config=config,
            status="created"
        )
        
        # Save metadata
        self._save_metadata(project_id, metadata)
        
        # Create empty graph file
        self._save_graph(project_id, GraphData())
        
        return metadata
    
    def _save_metadata(self, project_id: str, metadata: ProjectMetadata):
        """Save project metadata"""
        path = self._get_project_dir(project_id) / "metadata.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(metadata.model_dump(mode="json"), f, ensure_ascii=False, indent=2, default=str)
    
    def _load_metadata(self, project_id: str) -> Optional[ProjectMetadata]:
        """Load project metadata"""
        path = self._get_project_dir(project_id) / "metadata.json"
        if not path.exists():
            return None
        
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return ProjectMetadata(**data)
    
    def _save_graph(self, project_id: str, graph: GraphData):
        """Save graph data"""
        path = self._get_project_dir(project_id) / "graph.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(graph.model_dump(mode="json"), f, ensure_ascii=False, indent=2)
    
    def _load_graph(self, project_id: str) -> Optional[GraphData]:
        """Load graph data"""
        path = self._get_project_dir(project_id) / "graph.json"
        if not path.exists():
            return None
        
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return GraphData(**data)
    
    def get_project(self, project_id: str) -> Optional[ProjectResponse]:
        """Get full project with metadata and graph"""
        metadata = self._load_metadata(project_id)
        if not metadata:
            return None
        
        graph = self._load_graph(project_id) or GraphData()
        
        return ProjectResponse(
            metadata=metadata,
            graph=graph
        )
    
    def update_project_status(self, project_id: str, status: str, stats: Optional[GraphStats] = None):
        """Update project status"""
        metadata = self._load_metadata(project_id)
        if metadata:
            metadata.status = status
            metadata.updated_at = datetime.now()
            if stats:
                metadata.stats = stats
            self._save_metadata(project_id, metadata)
    
    def save_graph(self, project_id: str, graph: GraphData):
        """Save graph and update stats"""
        self._save_graph(project_id, graph)
        
        # Calculate stats
        years = [p.year for p in graph.nodes if p.year]
        stats = GraphStats(
            total_nodes=len(graph.nodes),
            total_edges=len(graph.edges),
            year_range=(min(years), max(years)) if years else None
        )
        
        self.update_project_status(project_id, "completed", stats)
    
    def update_edge(self, project_id: str, source: str, target: str, intent: str, note: Optional[str] = None) -> bool:
        """Update a single edge's annotation"""
        graph = self._load_graph(project_id)
        if not graph:
            return False
        
        for edge in graph.edges:
            if edge.source == source and edge.target == target:
                edge.intent = intent
                edge.confidence = 1.0  # Manual annotation = full confidence
                if note:
                    edge.reasoning = note
                self._save_graph(project_id, graph)
                return True
        
        return False
    
    def list_projects(self) -> List[ProjectMetadata]:
        """List all projects"""
        projects = []
        for project_dir in self.projects_dir.iterdir():
            if project_dir.is_dir():
                metadata = self._load_metadata(project_dir.name)
                if metadata:
                    projects.append(metadata)
        
        return sorted(projects, key=lambda p: p.created_at, reverse=True)
    
    def delete_project(self, project_id: str) -> bool:
        """Delete a project"""
        import shutil
        project_dir = self._get_project_dir(project_id)
        if project_dir.exists():
            shutil.rmtree(project_dir)
            return True
        return False
    
    def delete_paper(self, project_id: str, paper_id: str) -> bool:
        """Delete a paper node and its connected edges"""
        graph = self._load_graph(project_id)
        if not graph:
            return False
            
        # Check if node exists
        node_exists = any(node.id == paper_id for node in graph.nodes)
        if not node_exists:
            return False
            
        # Remove node
        graph.nodes = [node for node in graph.nodes if node.id != paper_id]
        
        # Remove connected edges
        original_edge_count = len(graph.edges)
        graph.edges = [
            edge for edge in graph.edges 
            if edge.source != paper_id and edge.target != paper_id
        ]
        
        logger.info(f"Deleted paper {paper_id}. Removed {original_edge_count - len(graph.edges)} edges.")
        
        # Save updated graph
        self.save_graph(project_id, graph)
        return True
    
    def export_bibtex(self, project_id: str) -> Optional[str]:
        """Export project papers as BibTeX"""
        graph = self._load_graph(project_id)
        if not graph:
            return None
        
        entries = []
        for paper in graph.nodes:
            # Generate citation key
            first_author = paper.authors[0].split()[-1] if paper.authors else "Unknown"
            year = paper.year or "0000"
            key = f"{first_author}{year}_{paper.id[:6]}"
            
            entry = f"@article{{{key},\n"
            entry += f"  title = {{{paper.title}}},\n"
            entry += f"  author = {{{' and '.join(paper.authors)}}},\n"
            entry += f"  year = {{{year}}},\n"
            
            if paper.venue:
                entry += f"  journal = {{{paper.venue}}},\n"
            if paper.doi:
                entry += f"  doi = {{{paper.doi}}},\n"
            if paper.url:
                entry += f"  url = {{{paper.url}}},\n"
            if paper.abstract:
                # Truncate long abstracts
                abstract = paper.abstract[:500] + "..." if len(paper.abstract) > 500 else paper.abstract
                entry += f"  abstract = {{{abstract}}},\n"
            
            entry += "}\n"
            entries.append(entry)
        
        return "\n".join(entries)

    def export_ris(self, project_id: str) -> Optional[str]:
        """Export project papers as RIS"""
        graph = self._load_graph(project_id)
        if not graph:
            return None

        lines: List[str] = []
        for paper in graph.nodes:
            lines.append("TY  - JOUR")
            if paper.title:
                lines.append(f"TI  - {paper.title}")
            for author in paper.authors:
                lines.append(f"AU  - {author}")
            if paper.year:
                lines.append(f"PY  - {paper.year}")
            if paper.venue:
                lines.append(f"JO  - {paper.venue}")
            if paper.doi:
                lines.append(f"DO  - {paper.doi}")
            if paper.url:
                lines.append(f"UR  - {paper.url}")
            if paper.abstract:
                abstract = paper.abstract[:2000]
                lines.append(f"AB  - {abstract}")
            lines.append("ER  -")
            lines.append("")

        return "\n".join(lines).strip() + "\n"


# Singleton instance
project_storage = ProjectStorage()

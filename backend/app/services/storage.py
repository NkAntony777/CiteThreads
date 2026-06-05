"""
Project Storage Service - JSON file-based storage
"""
import json
import os
import re
import logging
import tempfile
from datetime import datetime
from typing import Optional, List
from pathlib import Path
import uuid

from ..models import (
    Paper, CitationEdge, GraphData, GraphStats,
    ProjectMetadata, ProjectConfig, ProjectResponse,
    ChatMessage, ConversationSummary,
)
from ..config import settings

logger = logging.getLogger(__name__)

# Project ID validation: alphanumeric, hyphens, underscores only
_PROJECT_ID_RE = re.compile(r'^[a-zA-Z0-9_-]+$')


def _validate_project_id(project_id: str) -> str:
    """Validate project_id to prevent directory traversal attacks."""
    if not project_id or not _PROJECT_ID_RE.match(project_id):
        raise ValueError(f"Invalid project ID format: {project_id!r}")
    return project_id


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON atomically: write to temp file, then rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), suffix='.tmp', prefix=path.stem
    )
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp_path, str(path))
    except Exception:
        # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class ProjectStorage:
    """File-based project storage using JSON"""

    def __init__(self):
        self.projects_dir = Path(settings.data_dir) / "projects"
        self.projects_dir.mkdir(parents=True, exist_ok=True)

    def _get_project_dir(self, project_id: str) -> Path:
        """Get project directory path (with validation)"""
        _validate_project_id(project_id)
        return self.projects_dir / project_id
    
    def create_project(
        self,
        seed_paper_id: str,
        name: Optional[str] = None,
        depth: int = 2,
        direction: str = "both",
        user_id: Optional[str] = None,
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
            status="created",
            user_id=user_id,
        )
        
        # Save metadata
        self._save_metadata(project_id, metadata)
        
        # Create empty graph file
        self._save_graph(project_id, GraphData())
        
        return metadata
    
    def _save_metadata(self, project_id: str, metadata: ProjectMetadata):
        """Save project metadata"""
        path = self._get_project_dir(project_id) / "metadata.json"
        _atomic_write_json(path, metadata.model_dump(mode="json"))
    
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
        _atomic_write_json(path, graph.model_dump(mode="json"))
    
    def _load_graph(self, project_id: str) -> Optional[GraphData]:
        """Load graph data"""
        path = self._get_project_dir(project_id) / "graph.json"
        if not path.exists():
            return None

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return GraphData(**data)

    # ----- Chat history (2026-06: each project is also a conversation) -----

    def _chat_history_path(self, project_id: str) -> Path:
        return self._get_project_dir(project_id) / "chat_history.json"

    def _load_chat_history(self, project_id: str) -> Optional[List[ChatMessage]]:
        path = self._chat_history_path(project_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return [ChatMessage(**m) for m in data]
        except (json.JSONDecodeError, ValueError):
            logger.warning("chat history file is corrupt; treating as empty")
            return None

    def get_chat_history(self, project_id: str) -> List[ChatMessage]:
        """Return the conversation history for ``project_id``.

        Returns an empty list for unknown or corrupt projects — the
        chat UI treats "no history" the same as "fresh conversation".
        """
        return self._load_chat_history(project_id) or []

    def append_chat_message(self, project_id: str, message: ChatMessage) -> bool:
        """Append a single message to the conversation history.

        The agent runtime calls this after each finalized turn so a
        refresh restores the thread. The history file is
        append-on-write (whole-file rewrite; the file is small so
        this is fine).
        """
        try:
            existing = self._load_chat_history(project_id) or []
        except Exception:
            existing = []
        existing.append(message)
        try:
            _atomic_write_json(
                self._chat_history_path(project_id),
                [m.model_dump(mode="json") for m in existing],
            )
        except Exception as exc:
            logger.error("failed to write chat history for %s: %s", project_id, exc)
            return False
        # Bump updated_at on the metadata too so the conversation
        # sider can sort by recency without an extra field.
        try:
            metadata = self._load_metadata(project_id)
            if metadata is not None:
                metadata.updated_at = datetime.now()
                self._save_metadata(project_id, metadata)
        except Exception:
            pass
        return True

    def list_conversations(self, user_id: Optional[str] = None) -> List[ConversationSummary]:
        """List conversation summaries for the sider.

        Avoids shipping the full chat history — the caller can
        fetch it per-conversation. Unreadable / broken projects are
        skipped silently (a corrupt metadata.json shouldn't take
        down the whole list).
        """
        out: List[ConversationSummary] = []
        for project_dir in self.projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            metadata = self._load_metadata(project_dir.name)
            if metadata is None or not self._user_can_access(metadata, user_id):
                continue
            try:
                history = self._load_chat_history(project_dir.name) or []
            except Exception:
                history = []
            last_preview: Optional[str] = None
            for msg in reversed(history):
                if msg.content:
                    last_preview = msg.content[:120]
                    break
            section_draft_count = sum(len(m.section_drafts) for m in history)
            graph = self._load_graph(project_dir.name) or GraphData()
            out.append(
                ConversationSummary(
                    id=metadata.id,
                    name=metadata.name,
                    created_at=metadata.created_at,
                    updated_at=metadata.updated_at,
                    status=metadata.status,
                    last_message_preview=last_preview,
                    paper_count=len(graph.nodes),
                    section_draft_count=section_draft_count,
                )
            )
        return sorted(out, key=lambda c: c.updated_at, reverse=True)
    
    def get_project(self, project_id: str, user_id: Optional[str] = None) -> Optional[ProjectResponse]:
        """Get full project with metadata and graph.

        When ``user_id`` is supplied, projects owned by a different
        user (or with no owner set in legacy mode) are hidden. Pass
        ``user_id=None`` to disable the filter (used by the router
        when no auth is configured).
        """
        metadata = self._load_metadata(project_id)
        if not metadata:
            return None

        if user_id is not None and not self._user_can_access(metadata, user_id):
            return None

        graph = self._load_graph(project_id) or GraphData()
        chat_history = self._load_chat_history(project_id) or []

        return ProjectResponse(
            metadata=metadata,
            graph=graph,
            chat_history=chat_history,
        )

    @staticmethod
    def _user_can_access(
        metadata: ProjectMetadata, user_id: Optional[str]
    ) -> bool:
        """True when ``user_id`` may read ``metadata``.

        - ``metadata.user_id is None`` (legacy project) is treated as
          "owned by everyone" so pre-P2-1 projects keep being
          readable after the auth layer lands.
        - ``metadata.user_id == user_id`` is the normal match.
        - Admins (caller is responsible for passing the role through
          if needed) are not special-cased here: the storage layer
          doesn't know about roles. The router layer checks
          ``is_admin`` for the admin-only endpoints.
        """
        if metadata.user_id is None:
            return True
        return metadata.user_id == user_id
    
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
    
    def list_projects(self, user_id: Optional[str] = None) -> List[ProjectMetadata]:
        """List all projects visible to ``user_id``.

        When ``user_id`` is None, every project is returned (used by
        the router in dev / single-secret mode). When ``user_id`` is
        set, only projects owned by that user *or* legacy projects
        with no owner are included.
        """
        projects = []
        for project_dir in self.projects_dir.iterdir():
            if project_dir.is_dir():
                metadata = self._load_metadata(project_dir.name)
                if metadata and self._user_can_access(metadata, user_id):
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

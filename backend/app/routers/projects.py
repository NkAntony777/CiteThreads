"""
Projects API Router - Manage citation graph projects
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks, Response, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional
import asyncio
import json

from ..auth import UserAuthDep
from ..models import (
    ProjectCreateRequest, ProjectMetadata, ProjectResponse,
    AnnotationUpdate, GraphData, CrawlProgress, NetworkMetrics,
    ResearchGapResponse, ResearchGapItem, ConversationListResponse,
)
from ..services import project_storage, graph_builder
from ..services.network_analysis import network_analyzer
from ..services.gap_detection import gap_detector
from ..users import ANONYMOUS_ADMIN, UserContext

router = APIRouter(prefix="/projects", tags=["projects"])


def _user_filter(user: UserContext) -> Optional[str]:
    """Return the ``user_id`` to pass to ``project_storage`` for
    access-filtered queries.

    In single-secret / dev mode the resolved identity is
    :data:`ANONYMOUS_ADMIN`; we want that case to see *all* projects
    (so existing tests + the frontend's pre-auth flows keep working).
    In users-file mode the resolved user is a real ``user_id`` and we
    filter by it so cross-user isolation holds.
    """
    if user.user_id == ANONYMOUS_ADMIN.user_id:
        return None
    return user.user_id

# In-memory task status tracking
_task_status: dict[str, CrawlProgress] = {}


async def build_graph_task(project_id: str, seed_paper_id: str, depth: int, direction: str, max_papers: int):
    """Background task to build citation graph"""
    import logging
    logger = logging.getLogger(__name__)
    
    def progress_callback(progress: CrawlProgress):
        _task_status[project_id] = progress
    
    try:
        logger.info(f"Starting graph build: project={project_id}, seed={seed_paper_id}, depth={depth}, max={max_papers}")
        project_storage.update_project_status(project_id, "crawling")
        
        graph = await graph_builder.build_graph(
            seed_paper_id=seed_paper_id,
            depth=depth,
            direction=direction,
            classify_intent=False,
            max_papers=max_papers,
            progress_callback=progress_callback
        )
        
        logger.info(f"Graph built: {len(graph.nodes)} nodes, {len(graph.edges)} edges")
        project_storage.save_graph(project_id, graph)
        project_storage.update_project_status(project_id, "completed")
        
        _task_status[project_id] = CrawlProgress(
            status="completed",
            progress=100,
            total=100,
            message=f"完成！{len(graph.nodes)} 篇论文，{len(graph.edges)} 条引用"
        )
        
    except Exception as e:
        logger.error(f"Graph build failed: {e}", exc_info=True)
        project_storage.update_project_status(project_id, "failed")
        _task_status[project_id] = CrawlProgress(
            status="failed",
            progress=0,
            total=0,
            message=f"Error: {str(e)}"
        )


@router.post("", response_model=ProjectMetadata)
async def create_project(
    request: ProjectCreateRequest,
    background_tasks: BackgroundTasks,
    user: UserContext = UserAuthDep,
):
    """
    Create a new project and start building citation graph.

    - **seed_paper_id**: Starting paper ID (DOI, arXiv ID, or S2 ID)
    - **depth**: How many levels to crawl (1-3, default 1)
    - **direction**: "forward" (references), "backward" (citations), or "both"
    - **max_papers**: Maximum papers to fetch (10-200, default 50)
    """
    import logging
    logger = logging.getLogger(__name__)

    logger.info(f"Create project request: seed={request.seed_paper_id}, depth={request.depth}, max={request.max_papers}")

    # Create project, stamping it with the requester so cross-user
    # isolation works.
    metadata = project_storage.create_project(
        seed_paper_id=request.seed_paper_id,
        name=request.name,
        depth=request.depth,
        direction=request.direction,
        user_id=user.user_id,
    )

    # Start background build task
    background_tasks.add_task(
        build_graph_task,
        metadata.id,
        request.seed_paper_id,
        request.depth,
        request.direction,
        request.max_papers
    )

    return metadata


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(user: UserContext = UserAuthDep):
    """
    Lightweight conversation sider payload: id, name, updated_at,
    paper / section counts, and the latest message preview.
    Avoids shipping the full chat history on every list call.
    """
    items = project_storage.list_conversations(user_id=_user_filter(user))
    return ConversationListResponse(items=items)


@router.get("/{project_id}/full", response_model=ProjectResponse)
async def get_project_full(
    project_id: str,
    user: UserContext = UserAuthDep,
):
    """
    Full project payload including the entire chat_history. The
    ChatView calls this on mount of a conversation to rehydrate the
    message thread verbatim.
    """
    project = project_storage.get_project(project_id, user_id=_user_filter(user))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    user: UserContext = UserAuthDep,
):
    """Get project details with full graph data"""
    project = project_storage.get_project(project_id, user_id=_user_filter(user))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.get("/{project_id}/status")
async def get_project_status(
    project_id: str,
    user: UserContext = UserAuthDep,
):
    """Get build status for a project"""
    # Check in-memory status first
    if project_id in _task_status:
        return _task_status[project_id]

    # Fall back to stored status
    project = project_storage.get_project(project_id, user_id=_user_filter(user))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return CrawlProgress(
        status=project.metadata.status,
        progress=100 if project.metadata.status == "completed" else 0,
        total=100,
        message=f"Status: {project.metadata.status}"
    )


@router.get("/{project_id}/stream")
async def stream_project_status(
    project_id: str,
    user: UserContext = UserAuthDep,
):
    """
    Stream build progress via Server-Sent Events (SSE)
    """
    project = project_storage.get_project(project_id, user_id=_user_filter(user))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    async def event_generator():
        last_status = None
        while True:
            status = _task_status.get(project_id)
            
            if status and status != last_status:
                yield f"data: {json.dumps(status.model_dump())}\n\n"
                last_status = status
                
                if status.status in ["completed", "failed"]:
                    break
            
            await asyncio.sleep(0.5)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@router.patch("/{project_id}/edges")
async def update_edge_annotation(
    project_id: str,
    source: str,
    target: str,
    annotation: AnnotationUpdate,
    user: UserContext = UserAuthDep,
):
    """Update citation intent annotation for an edge"""
    project = project_storage.get_project(project_id, user_id=_user_filter(user))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    success = project_storage.update_edge(
        project_id=project_id,
        source=source,
        target=target,
        intent=annotation.intent.value,
        note=annotation.note
    )

    if not success:
        raise HTTPException(status_code=404, detail="Edge not found")

    return {"status": "updated"}


@router.get("/{project_id}/export")
async def export_project(
    project_id: str,
    format: str = "bibtex",
    user: UserContext = UserAuthDep,
):
    """
    Export project papers.

    - **format**: Export format ("bibtex", "ris", or "json")
    """
    project = project_storage.get_project(project_id, user_id=_user_filter(user))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if format == "bibtex":
        content = project_storage.export_bibtex(project_id)
        if not content:
            raise HTTPException(status_code=404, detail="Project not found")

        return Response(
            content=content,
            media_type="application/x-bibtex",
            headers={"Content-Disposition": f"attachment; filename={project_id}.bib"}
        )
    elif format == "ris":
        content = project_storage.export_ris(project_id)
        if not content:
            raise HTTPException(status_code=404, detail="Project not found")

        return Response(
            content=content,
            media_type="application/x-research-info-systems",
            headers={"Content-Disposition": f"attachment; filename={project_id}.ris"}
        )
    elif format == "json":
        return Response(
            content=json.dumps(project.graph.model_dump(), indent=2, ensure_ascii=False),
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename={project_id}.json"}
        )

    else:
        raise HTTPException(status_code=400, detail=f"Unknown format: {format}")


@router.delete("/{project_id}")
async def delete_project(
    project_id: str,
    user: UserContext = UserAuthDep,
):
    """Delete a project"""
    project = project_storage.get_project(project_id, user_id=_user_filter(user))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    success = project_storage.delete_project(project_id)

    if not success:
        raise HTTPException(status_code=404, detail="Project not found")

    # Clean up task status
    if project_id in _task_status:
        del _task_status[project_id]

    return {"status": "deleted"}


@router.delete("/{project_id}/papers/{paper_id}")
async def delete_paper(
    project_id: str,
    paper_id: str,
    user: UserContext = UserAuthDep,
):
    """Delete a paper node from the project"""
    project = project_storage.get_project(project_id, user_id=_user_filter(user))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    success = project_storage.delete_paper(project_id, paper_id)
    if not success:
        raise HTTPException(status_code=404, detail="Paper not found or project not found")

    return {"status": "deleted", "paper_id": paper_id}


@router.get("", response_model=List[ProjectMetadata])
async def list_projects(user: UserContext = UserAuthDep):
    """
    List all saved projects visible to the calling user.
    Returns projects sorted by creation time (newest first).
    """
    return project_storage.list_projects(user_id=_user_filter(user))


class RenameRequest(BaseModel):
    name: str


@router.patch("/{project_id}/rename")
async def rename_project(
    project_id: str,
    request: RenameRequest,
    user: UserContext = UserAuthDep,
):
    """Rename a project"""
    project = project_storage.get_project(project_id, user_id=_user_filter(user))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    project.metadata.name = request.name
    project_storage._save_metadata(project_id, project.metadata)

    return {"status": "renamed", "name": request.name}


@router.post("/{project_id}/analyze")
async def analyze_project_intents(
    project_id: str,
    background_tasks: BackgroundTasks,
    user: UserContext = UserAuthDep,
):
    """
    Trigger AI citation intent analysis for an existing project.
    Running in background.
    """
    project = project_storage.get_project(project_id, user_id=_user_filter(user))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Check if analysis is already running
    if project_id in _task_status and _task_status[project_id].status == "analyzing":
        return {"status": "analyzing", "message": "Analysis already in progress"}

    async def run_analysis_task():
        try:
            # Update status
            def progress_callback(progress: CrawlProgress):
                _task_status[project_id] = progress
            
            project_storage.update_project_status(project_id, "analyzing")
            
            # Run classification
            # Need to get papers as dict map
            papers_dict = {p.id: p for p in project.graph.nodes}
            edges = project.graph.edges
            
            # Update edges with classification
            new_edges = await graph_builder._classify_intents(
                papers=papers_dict,
                edges=edges,
                progress_callback=progress_callback
            )
            
            # Update graph in project
            project.graph.edges = new_edges
            project_storage.save_graph(project_id, project.graph)
            project_storage.update_project_status(project_id, "completed")
            
            _task_status[project_id] = CrawlProgress(
                status="completed",
                progress=100,
                total=100,
                message="AI 分析完成",
            )
            
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Analysis failed: {e}")
            project_storage.update_project_status(project_id, "failed")
            _task_status[project_id] = CrawlProgress(
                status="failed",
                progress=0,
                total=0,
                message=f"Analysis failed: {str(e)}"
            )

    background_tasks.add_task(run_analysis_task)

    return {"status": "started", "message": "AI analysis started in background"}


@router.get("/{project_id}/metrics", response_model=NetworkMetrics)
async def get_network_metrics(
    project_id: str,
    user: UserContext = UserAuthDep,
):
    """
    Compute network analysis metrics for a project's citation graph.

    Returns:
    - **pagerank**: PageRank score for each paper (importance)
    - **betweenness**: Betweenness centrality (bridging importance)
    - **communities**: Community assignment for each paper
    - **community_count**: Total number of communities detected
    """
    project = project_storage.get_project(project_id, user_id=_user_filter(user))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    results = network_analyzer.analyze(project.graph)

    return NetworkMetrics(
        pagerank=results["pagerank"],
        betweenness=results["betweenness"],
        communities=results["communities"],
        community_count=len(set(results["communities"].values())) if results["communities"] else 0,
    )


@router.get("/{project_id}/gaps", response_model=ResearchGapResponse)
async def detect_research_gaps(
    project_id: str,
    user: UserContext = UserAuthDep,
):
    """
    Detect research gaps in a project's citation graph.

    Identifies:
    - **unrefuted_claim**: Papers with supporting citations but no opposing views
    - **sparse_region**: Well-cited papers with few graph connections
    - **broken_chain**: References to papers not in the graph
    - **stale_frontier**: High-impact papers uncited for 5+ years
    """
    project = project_storage.get_project(project_id, user_id=_user_filter(user))
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    gaps = gap_detector.detect(project.graph)

    return ResearchGapResponse(
        gaps=[ResearchGapItem(**g) for g in gaps],
        total=len(gaps),
    )

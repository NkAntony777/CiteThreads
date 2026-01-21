"""
Writing API Router
Endpoints for literature review and AI writing assistant
"""
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional, List
import logging

from ..models import Paper
from ..models.references import (
    Reference,
    LiteratureReviewDraft, 
    WritingContext, 
    ChatMessage,
    ReferenceSource,
    ReferenceList
)
from ..services.review_generator import review_generator
from ..services.writing_assistant import writing_assistant
from ..services.paper_search_service import paper_search_service, SearchFilters
from ..services.storage import project_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/writing", tags=["writing"])


# ============================================
# REQUEST/RESPONSE MODELS
# ============================================

class AddReferenceRequest(BaseModel):
    """Request to add a reference"""
    paper_id: str
    source: str = "search"  # 'graph', 'search', 'upload'


class UploadReferenceRequest(BaseModel):
    """Request to upload a reference via URL"""
    url: str


class ReviewGenerateRequest(BaseModel):
    """Request to generate literature review"""
    reference_ids: Optional[List[str]] = None  # If None, use all references
    style: str = "academic"  # 'academic', 'concise', 'detailed'
    include_graph_info: bool = True


class ChatRequest(BaseModel):
    """Chat message request"""
    message: str
    history: Optional[List[dict]] = None


class SearchPapersRequest(BaseModel):
    """Request to search papers for writing"""
    query: str
    sources: Optional[List[str]] = None
    limit: int = 10


class GenerateSectionRequest(BaseModel):
    """Request to generate a section"""
    section_type: str  # 'introduction', 'methodology', 'discussion', etc.
    outline: Optional[str] = None
    context: Optional[str] = None


class ReferenceResponse(BaseModel):
    """Response with reference data"""
    id: str
    paper: dict
    citation_key: str
    source: str


class ReviewResponse(BaseModel):
    """Response with generated review"""
    content: str
    references: List[ReferenceResponse]
    style: str


class CanvasSaveRequest(BaseModel):
    """Request to save canvas content"""
    content: str


class ReviewSaveRequest(BaseModel):
    """Request to save review content"""
    content: str


class ChatHistorySaveRequest(BaseModel):
    """Request to save chat history"""
    history: List[dict]


# ============================================
# CANVAS ENDPOINTS
# ============================================

@router.get("/projects/{project_id}/canvas")
async def get_canvas(project_id: str):
    """Get canvas content for a project"""
    from pathlib import Path
    from ..config import settings
    path = Path(settings.data_dir) / "projects" / project_id / "canvas.md"
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return {"content": content}


@router.post("/projects/{project_id}/canvas")
async def save_canvas(project_id: str, request: CanvasSaveRequest):
    """Save canvas content for a project"""
    from pathlib import Path
    from ..config import settings
    path = Path(settings.data_dir) / "projects" / project_id / "canvas.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(request.content, encoding="utf-8")
    return {"success": True}


# ============================================
# PERSISTENCE ENDPOINTS (REVIEW & CHAT)
# ============================================

@router.get("/projects/{project_id}/review")
async def get_review(project_id: str):
    """Get saved literature review for a project"""
    from pathlib import Path
    from ..config import settings
    path = Path(settings.data_dir) / "projects" / project_id / "review.md"
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    return {"content": content}


@router.post("/projects/{project_id}/review")
async def save_review(project_id: str, request: ReviewSaveRequest):
    """Save literature review for a project"""
    from pathlib import Path
    from ..config import settings
    path = Path(settings.data_dir) / "projects" / project_id / "review.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(request.content, encoding="utf-8")
    return {"success": True}


@router.get("/projects/{project_id}/chat-history")
async def get_chat_history(project_id: str):
    """Get saved chat history for a project"""
    import json
    from pathlib import Path
    from ..config import settings
    path = Path(settings.data_dir) / "projects" / project_id / "chat_history.json"
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                history = json.load(f)
            return {"history": history}
        except Exception as e:
            logger.error(f"Error loading chat history: {e}")
            return {"history": []}
    return {"history": []}


@router.post("/projects/{project_id}/chat-history")
async def save_chat_history(project_id: str, request: ChatHistorySaveRequest):
    """Save chat history for a project"""
    import json
    from pathlib import Path
    from ..config import settings
    try:
        path = Path(settings.data_dir) / "projects" / project_id / "chat_history.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(request.history, f, ensure_ascii=False, indent=2)
        return {"success": True}
    except Exception as e:
        logger.error(f"Error saving chat history: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# REFERENCE MANAGEMENT ENDPOINTS
# ============================================

# In-memory storage for references (in production, use database)
_reference_lists: dict[str, ReferenceList] = {}


def _get_reference_list(project_id: str) -> ReferenceList:
    """Get or create reference list for a project with file persistence"""
    if project_id in _reference_lists:
        return _reference_lists[project_id]
        
    # Try to load from disk
    import json
    import os
    from pathlib import Path
    from ..config import settings
    
    path = Path(settings.data_dir) / "projects" / project_id / "references.json"
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                ref_list = ReferenceList(**data)
                _reference_lists[project_id] = ref_list
                return ref_list
        except Exception as e:
            logger.error(f"Error loading references for {project_id}: {e}")
            
    # Create new if not found or error
    ref_list = ReferenceList(project_id=project_id)
    _reference_lists[project_id] = ref_list
    return ref_list


def _save_reference_list(project_id: str, ref_list: ReferenceList):
    """Save reference list to disk"""
    import json
    from pathlib import Path
    from ..config import settings
    
    _reference_lists[project_id] = ref_list
    
    try:
        path = Path(settings.data_dir) / "projects" / project_id / "references.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(ref_list.model_dump(mode="json"), f, ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        logger.error(f"Error saving references for {project_id}: {e}")


@router.get("/projects/{project_id}/references")
async def get_references(project_id: str):
    """Get all references for a project"""
    ref_list = _get_reference_list(project_id)
    return {
        "project_id": project_id,
        "references": [
            {
                "id": ref.id,
                "paper": ref.paper.model_dump(),
                "citation_key": ref.citation_key,
                "source": ref.source.value,
                "notes": ref.notes,
                "added_at": ref.added_at.isoformat()
            }
            for ref in ref_list.references
        ],
        "total": len(ref_list.references)
    }


@router.post("/projects/{project_id}/references")
async def add_reference(project_id: str, request: AddReferenceRequest):
    """Add a reference from a paper ID"""
    try:
        # Load project to get paper data
        project = project_storage.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        
        # Find the paper in the graph
        paper = None
        for node in project.graph.nodes:
            if node.id == request.paper_id:
                paper = node
                break
        
        if not paper:
            raise HTTPException(status_code=404, detail="Paper not found in project")
        
        # Create Reference
        source = ReferenceSource(request.source)
        ref = Reference.from_paper(paper, source)
        
        # Add to list
        ref_list = _get_reference_list(project_id)
        success = ref_list.add_reference(ref)
        
        if success:
            _save_reference_list(project_id, ref_list)
        else:
            return {"success": False, "message": "Reference already exists"}
        
        return {
            "success": True,
            "reference": {
                "id": ref.id,
                "citation_key": ref.citation_key,
                "paper": paper.model_dump()
            }
        }
        
    except Exception as e:
        logger.error(f"Error adding reference: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/projects/{project_id}/references/{ref_id}")
async def remove_reference(project_id: str, ref_id: str):
    """Remove a reference by ID"""
    ref_list = _get_reference_list(project_id)
    success = ref_list.remove_reference(ref_id)
    
    if success:
        _save_reference_list(project_id, ref_list)
        return {"success": True, "message": "Reference removed"}
    else:
        raise HTTPException(status_code=404, detail="Reference not found")


@router.post("/projects/{project_id}/references/from-search")
async def add_reference_from_search(project_id: str, paper: Paper):
    """Add a reference from search results"""
    try:
        ref = Reference.from_paper(paper, ReferenceSource.SEARCH)
        ref_list = _get_reference_list(project_id)
        success = ref_list.add_reference(ref)
        
        if success:
            _save_reference_list(project_id, ref_list)
        else:
            return {"success": False, "message": "Reference already exists"}
        
        return {
            "success": True,
            "reference": {
                "id": ref.id,
                "citation_key": ref.citation_key,
                "paper": paper.model_dump()
            }
        }
        
    except Exception as e:
        logger.error(f"Error adding reference from search: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# LITERATURE REVIEW ENDPOINTS
# ============================================

@router.post("/projects/{project_id}/review/generate")
async def generate_review(project_id: str, request: ReviewGenerateRequest):
    """Generate a literature review from references"""
    try:
        ref_list = _get_reference_list(project_id)
        
        if not ref_list.references:
            raise HTTPException(status_code=400, detail="No references available. Please add references first.")
        
        # Filter references if specific IDs provided
        if request.reference_ids:
            refs = [r for r in ref_list.references if r.id in request.reference_ids]
        else:
            refs = ref_list.references
        
        if not refs:
            raise HTTPException(status_code=400, detail="No matching references found")
        
        # Get graph structure if requested
        graph_structure = None
        if request.include_graph_info:
            project = project_storage.get_project(project_id)
            if project:
                graph_structure = project.graph
        
        # Generate review
        draft = await review_generator.generate(
            references=refs,
            graph_structure=graph_structure,
            style=request.style
        )
        
        return {
            "success": True,
            "review": {
                "content": draft.content,
                "style": draft.style,
                "generated_at": draft.generated_at.isoformat(),
                "reference_count": len(draft.references)
            }
        }
        
    except TimeoutError:
        raise HTTPException(status_code=504, detail="AI 响应超时，请稍后重试")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error generating review: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# AI WRITING ASSISTANT ENDPOINTS
# ============================================

@router.post("/projects/{project_id}/writing/chat")
async def writing_chat(project_id: str, request: ChatRequest):
    """Chat with the AI writing assistant"""
    try:
        ref_list = _get_reference_list(project_id)
        
        # Build writing context
        context = WritingContext(
            project_id=project_id,
            references=ref_list.references,
            current_document=""
        )
        
        # Convert history to ChatMessage objects
        history = None
        if request.history:
            history = [
                ChatMessage(
                    role=msg.get("role", "user"),
                    content=msg.get("content", "")
                )
                for msg in request.history
            ]
        
        # Get response
        response = await writing_assistant.chat(
            message=request.message,
            context=context,
            history=history
        )
        
        return {
            "success": True,
            "message": {
                "role": response.role,
                "content": response.content,
                "timestamp": response.timestamp.isoformat(),
                "paper_suggestions": [p.model_dump() for p in response.paper_suggestions] if response.paper_suggestions else None,
                "action_type": response.action_type
            }
        }
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error in writing chat: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/writing/search-papers")
async def search_papers_for_writing(project_id: str, request: SearchPapersRequest):
    """Search papers for writing assistance"""
    try:
        filters = None
        if request.sources:
            filters = SearchFilters()
        
        result = await paper_search_service.search(
            query=request.query,
            sources=request.sources,
            filters=filters,
            limit=request.limit
        )
        
        return {
            "success": True,
            "papers": [p.model_dump() for p in result.papers],
            "total": result.total,
            "sources_searched": result.sources_searched,
            "errors": result.errors
        }
        
    except Exception as e:
        logger.error(f"Error searching papers: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/projects/{project_id}/writing/generate-section")
async def generate_section(project_id: str, request: GenerateSectionRequest):
    """Generate a specific section of the paper"""
    try:
        ref_list = _get_reference_list(project_id)
        
        if not ref_list.references:
            raise HTTPException(status_code=400, detail="No references available")
        
        content = await writing_assistant.generate_section(
            section_type=request.section_type,
            references=ref_list.references,
            context=request.context,
            outline=request.outline
        )
        
        return {
            "success": True,
            "section": {
                "type": request.section_type,
                "content": content
            }
        }
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error generating section: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/projects/{project_id}/references/export/bibtex")
async def export_bibtex(project_id: str):
    """Export references as BibTeX"""
    ref_list = _get_reference_list(project_id)
    bibtex = ref_list.to_bibtex()
    
    return {
        "content": bibtex,
        "format": "bibtex",
        "count": len(ref_list.references)
    }

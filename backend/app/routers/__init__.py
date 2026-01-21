# Routers package
from .papers import router as papers_router
from .projects import router as projects_router
from .writing import router as writing_router

__all__ = ["papers_router", "projects_router", "writing_router"]

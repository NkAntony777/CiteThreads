"""
CiteThreads Backend - FastAPI Application Entry Point
"""
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
import logging

from .config import settings
from .routers import papers_router, projects_router, writing_router
from .routers.admin import router as admin_router
from .routers.agent import router as agent_router
from .routers.ai import router as ai_router
from .routers.draft import router as draft_router
from .auth import require_bearer_token
from .logging_config import configure_json_logging, RequestIdMiddleware
from .metrics import metrics, render_prometheus
from .users import initialise_user_store

# Configure structured JSON logging. This replaces the default
# basicConfig text format and rewires uvicorn's loggers so request
# lines land in the same JSON stream as application logs.
configure_json_logging(
    level=logging.INFO if not settings.debug else logging.DEBUG,
)

logger = logging.getLogger(__name__)

# Initialise the per-user store once at process start so the auth
# dependency has the user table ready before the first request.
initialise_user_store()

# Create FastAPI app
app = FastAPI(
    title="CiteThreads API",
    description="学术引用脉络可视化引擎 - Citation Thread Visualization Engine",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Add the request-id middleware first so every other middleware /
# route handler runs inside its scope. The middleware also emits
# the access log line that includes status and duration.
app.add_middleware(RequestIdMiddleware)

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Wire the bearer-token auth dependency into every route. FastAPI lets
# us attach it at the app level so we don't have to repeat it on each
# router. Public paths (/, /health, /docs, /redoc, /openapi.json) are
# exempted inside the dependency itself.
from fastapi import Depends as _Depends
from .auth import require_bearer_token as _require_bearer_token
_bearer = _Depends(_require_bearer_token)
app.include_router(papers_router, prefix="/api", dependencies=[_bearer])
app.include_router(projects_router, prefix="/api", dependencies=[_bearer])
app.include_router(ai_router, prefix="/api", dependencies=[_bearer])
app.include_router(writing_router, prefix="/api", dependencies=[_bearer])
app.include_router(agent_router, prefix="/api", dependencies=[_bearer])
app.include_router(draft_router, prefix="/api", dependencies=[_bearer])
app.include_router(admin_router, prefix="/api", dependencies=[_bearer])


@app.get("/")
async def root():
    """API root - health check"""
    return {
        "name": "CiteThreads API",
        "version": "0.1.0",
        "status": "running",
        "docs": "/docs"
    }


@app.get("/health")
async def health_check():
    """Combined health report.

    Returns ``status`` as ``ok``, ``degraded``, or ``down`` plus a
    per-check breakdown. HTTP status is 200 for ``ok``/``degraded``
    and 503 for ``down`` so a load balancer can take the pod out of
    rotation on a critical failure.
    """
    from .health import run_health_checks
    report = run_health_checks()
    status_code = 200 if report["status"] != "down" else 503
    return Response(
        content=_json_dumps(report),
        media_type="application/json",
        status_code=status_code,
    )


@app.get("/health/live")
async def health_live():
    """Liveness probe — process is up, no dependency checks.

    Suitable for Kubernetes ``livenessProbe``. Must not depend on
    external services.
    """
    from .health import live
    return live()


@app.get("/health/ready")
async def health_ready():
    """Readiness probe — process can serve traffic.

    Returns 503 if any critical check fails. Suitable for Kubernetes
    ``readinessProbe`` and load-balancer health checks.
    """
    from .health import ready
    report = ready()
    status_code = 200 if report["status"] != "down" else 503
    return Response(
        content=_json_dumps(report),
        media_type="application/json",
        status_code=status_code,
    )


@app.get("/api/metrics")
async def api_metrics():
    """JSON snapshot of every metric in the in-memory store."""
    return metrics.snapshot()


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus text exposition.

    Scrapers expect ``text/plain; version=0.0.4`` per the Prometheus
    spec. The content is regenerated on every request from the
    in-memory store, so there is no caching layer to keep in sync.
    """
    return Response(
        content=render_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


def _json_dumps(obj) -> str:
    """Local json.dumps wrapper that falls back to ``str`` on
    non-serializable values. Keeps the health endpoints robust to
    a future field that happens to carry a Path or a datetime."""
    import json
    return json.dumps(obj, ensure_ascii=False, default=str)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug
    )

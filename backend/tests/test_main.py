"""
Tests for the main FastAPI application
"""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.fixture
def anyio_backend():
    """Use asyncio backend for anyio"""
    return "asyncio"


@pytest.fixture
async def client():
    """Create async test client"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


class TestHealthCheck:
    """Tests for health check endpoints"""

    @pytest.mark.asyncio
    async def test_root_endpoint(self, client):
        """Test the root endpoint returns API info"""
        response = await client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "CiteThreads API"
        assert data["status"] == "running"

    @pytest.mark.asyncio
    async def test_health_endpoint(self, client):
        """The combined health endpoint returns a status payload.

        Status is one of ``ok``, ``degraded``, or ``down``. The
        LLM-less test environment will report ``ok`` (LLM is treated
        as informational) so 200 is the expected status code here.
        """
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in {"ok", "degraded", "down"}
        assert "checks" in data

    @pytest.mark.asyncio
    async def test_health_live_endpoint(self, client):
        """Liveness must always be 200 — no downstream checks."""
        response = await client.get("/health/live")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_ready_endpoint(self, client):
        """Readiness reports the same checks as ``/health``."""
        response = await client.get("/health/ready")
        assert response.status_code in (200, 503)
        data = response.json()
        assert "checks" in data

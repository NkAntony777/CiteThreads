"""Tests for AI base_url SSRF validation."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class _MockResponse:
    status_code = 200
    headers = {"content-type": "application/json"}
    text = "{}"

    def json(self):
        return {"data": [{"id": "gpt-4"}]}


class _MockAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None, params=None):
        return _MockResponse()

    async def post(self, url, headers=None, params=None, json=None):
        return _MockResponse()


@pytest.mark.asyncio
async def test_ai_test_rejects_localhost_ip(client):
    resp = await client.post(
        "/api/ai/test",
        json={
            "provider": "openai",
            "api_key": "x",
            "model": "gpt-4",
            "base_url": "http://127.0.0.1:8000/v1",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert "API 基础 URL" in data["message"]


@pytest.mark.asyncio
async def test_ai_test_rejects_link_local_metadata_ip(client):
    resp = await client.post(
        "/api/ai/test",
        json={
            "provider": "openai",
            "api_key": "x",
            "model": "gpt-4",
            "base_url": "http://169.254.169.254/v1",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False


@pytest.mark.asyncio
async def test_ai_test_rejects_unsafe_scheme(client):
    resp = await client.post(
        "/api/ai/test",
        json={
            "provider": "openai",
            "api_key": "x",
            "model": "gpt-4",
            "base_url": "file:///etc/passwd",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert "http/https" in data["message"]


@pytest.mark.asyncio
async def test_ai_test_allows_public_ip_with_mocked_httpx(client):
    with patch("app.routers.ai.httpx.AsyncClient", _MockAsyncClient):
        resp = await client.post(
            "/api/ai/test",
            json={
                "provider": "openai",
                "api_key": "x",
                "model": "gpt-4",
                "base_url": "https://93.184.216.34/v1",
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True


@pytest.mark.asyncio
async def test_ai_test_embedding_rejects_query_fragment(client):
    resp = await client.post(
        "/api/ai/test-embedding",
        json={
            "provider": "openai",
            "api_key": "x",
            "model": "text-embedding-3-small",
            "base_url": "https://93.184.216.34/v1?x=1",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False

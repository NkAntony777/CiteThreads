"""AI Configuration API Router - Test AI connections."""

from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Optional
from urllib.parse import urlsplit, urlunsplit

from anyio import to_thread
import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from ..config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])


_MAX_BASE_URL_LEN = 2048


def _is_disallowed_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve_host_sync(hostname: str, port: int) -> set[str]:
    addrs: set[str] = set()
    for family, _socktype, _proto, _canonname, sockaddr in socket.getaddrinfo(
        hostname,
        port,
        type=socket.SOCK_STREAM,
    ):
        if family == socket.AF_INET:
            addrs.add(str(sockaddr[0]))
        elif family == socket.AF_INET6:
            addrs.add(str(sockaddr[0]))
    return addrs


async def _validate_and_normalize_base_url(raw_base_url: str) -> str:
    base_url = (raw_base_url or "").strip()
    if not base_url:
        raise ValueError("未提供 API 基础 URL")
    if len(base_url) > _MAX_BASE_URL_LEN:
        raise ValueError("API 基础 URL 过长")

    parts = urlsplit(base_url)

    if parts.scheme not in {"http", "https"}:
        raise ValueError("仅允许 http/https 协议")
    if not parts.netloc:
        raise ValueError("API 基础 URL 必须包含主机名")
    if parts.username or parts.password:
        raise ValueError("API 基础 URL 不允许包含用户名/密码")
    if parts.query or parts.fragment:
        raise ValueError("API 基础 URL 不允许包含 query/fragment")

    hostname = parts.hostname
    if not hostname:
        raise ValueError("API 基础 URL 主机名无效")

    host_lc = hostname.lower().rstrip(".")
    if host_lc == "localhost" or host_lc.endswith(".localhost"):
        raise ValueError("不允许访问 localhost")
    if host_lc.endswith(".local"):
        raise ValueError("不允许访问 .local 域名")

    port = parts.port
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    if port < 1 or port > 65535:
        raise ValueError("端口号无效")

    resolved_ips: set[str] = set()
    try:
        ip_obj = ipaddress.ip_address(host_lc)
        resolved_ips.add(str(ip_obj))
    except ValueError:
        try:
            resolved_ips = await to_thread.run_sync(
                _resolve_host_sync,
                hostname,
                port,
            )
        except socket.gaierror as e:
            raise ValueError("无法解析 API 主机名") from e

    if not resolved_ips:
        raise ValueError("无法解析 API 主机名")

    for ip_str in resolved_ips:
        try:
            ip_obj = ipaddress.ip_address(ip_str)
        except ValueError:
            raise ValueError("解析到无效 IP")

        if _is_disallowed_ip(ip_obj) or not ip_obj.is_global:
            raise ValueError("不允许访问本地/内网/保留地址")

    normalized_path = (parts.path or "").rstrip("/")
    normalized = urlunsplit((parts.scheme, parts.netloc, normalized_path, "", ""))
    return normalized


class AITestRequest(BaseModel):
    """Request to test AI connection"""

    provider: str
    api_key: str
    model: str
    base_url: Optional[str] = None


class AITestConfigRequest(BaseModel):
    """Server-side connection test. No ``api_key`` is accepted: the
    server uses its configured default key. The endpoint exists so
    the frontend can verify a model + base URL combo without ever
    shipping the raw key to the browser."""

    provider: str
    model: str
    base_url: Optional[str] = None


class AITestResponse(BaseModel):
    """AI connection test response"""

    success: bool
    message: str
    model_info: Optional[str] = None


# Provider configurations
PROVIDER_CONFIGS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "test_endpoint": "/models",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "test_endpoint": "/models",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
    },
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1",
        "test_endpoint": "/models",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com/v1",
        "test_endpoint": "/messages",
        "auth_header": "x-api-key",
        "auth_prefix": "",
    },
    "google": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "test_endpoint": "/models",
        "auth_header": None,  # Uses query param
        "auth_prefix": "",
    },
}


@router.post("/test", response_model=AITestResponse)
async def test_ai_connection(request: AITestRequest):
    """
    Test AI provider connection by making a simple API call.

    Returns success status and any error messages.
    """
    provider = request.provider
    api_key = request.api_key
    model = request.model
    base_url = request.base_url

    logger.info(f"Testing AI connection: provider={provider}, model={model}")

    # Get provider config
    config = PROVIDER_CONFIGS.get(
        provider,
        {
            "base_url": base_url or "",
            "test_endpoint": "/models",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer ",
        },
    )

    raw_selected_base_url = (base_url or config.get("base_url") or "").strip()
    try:
        config["base_url"] = await _validate_and_normalize_base_url(
            raw_selected_base_url
        )
    except ValueError as e:
        return AITestResponse(success=False, message=f"API 基础 URL 无效: {e}")

    try:
        # Build request
        headers = {}
        params = {}

        if provider == "google":
            # Google uses query param for auth
            params["key"] = api_key
            url = f"{config['base_url']}{config['test_endpoint']}"
        elif provider == "anthropic":
            # Anthropic uses different auth header and needs a test message
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
            headers["Content-Type"] = "application/json"

            # Test with minimal message
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{config['base_url']}/messages",
                    headers=headers,
                    json={
                        "model": model,
                        "max_tokens": 10,
                        "messages": [{"role": "user", "content": "Hi"}],
                    },
                )

                if response.status_code == 200:
                    return AITestResponse(
                        success=True,
                        message="连接成功！该 API 密钥与模型可用。",
                        model_info=model,
                    )
                elif response.status_code == 401:
                    return AITestResponse(success=False, message="无效的 API 密钥")
                elif response.status_code == 404:
                    return AITestResponse(success=False, message=f"模型不存在: {model}")
                else:
                    data = (
                        response.json()
                        if response.headers.get("content-type", "").startswith(
                            "application/json"
                        )
                        else {}
                    )
                    error_msg = data.get("error", {}).get(
                        "message", response.text[:100]
                    )
                    return AITestResponse(
                        success=False, message=f"API 错误: {error_msg}"
                    )
        else:
            # OpenAI-compatible APIs
            headers[config["auth_header"]] = f"{config['auth_prefix']}{api_key}"
            url = f"{config['base_url']}{config['test_endpoint']}"

        # Make test request
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, headers=headers, params=params)

            logger.info(f"AI test response: status={response.status_code}")

            if response.status_code == 200:
                # Check if model exists in response
                data = response.json()
                models = data.get("data", [])
                model_ids = [m.get("id", "") for m in models] if models else []

                # For some APIs, we just check if the call succeeded
                model_exists = True
                if model_ids and model not in model_ids:
                    # Model not in list, but API key works
                    model_exists = False

                if model_exists:
                    return AITestResponse(
                        success=True,
                        message="连接成功！该 API 密钥与模型可用。",
                        model_info=f"已验证模型: {model}",
                    )
                else:
                    return AITestResponse(
                        success=True,
                        message=f"API 密钥有效，但模型 '{model}' 可能不存在。请确认模型名称。",
                        model_info=f"可用模型: {', '.join(model_ids[:5])}...",
                    )

            elif response.status_code == 401:
                return AITestResponse(success=False, message="无效的 API 密钥")

            elif response.status_code == 403:
                return AITestResponse(success=False, message="API 密钥权限不足")

            elif response.status_code == 429:
                return AITestResponse(success=False, message="请求频率过高，请稍后重试")

            else:
                try:
                    error_data = response.json()
                    error_msg = error_data.get("error", {}).get(
                        "message", str(error_data)
                    )
                except Exception:
                    error_msg = response.text[:200]

                return AITestResponse(
                    success=False,
                    message=f"API 错误 ({response.status_code}): {error_msg}",
                )

    except httpx.TimeoutException:
        return AITestResponse(success=False, message="连接超时，请检查网络或 API 地址")

    except httpx.ConnectError:
        return AITestResponse(
            success=False, message="无法连接到 API 服务器，请检查网络"
        )

    except Exception as e:
        logger.error(f"AI test error: {e}")
        return AITestResponse(success=False, message=f"测试失败: {str(e)}")


@router.post("/test-config", response_model=AITestResponse)
async def test_ai_connection_with_default_key(request: AITestConfigRequest):
    """
    Test the AI connection using the server-side default key.

    This is the post-review replacement for ``/api/ai/test``: the
    frontend no longer has a raw key, so it can only test the
    provider + model + base URL combo the server is configured to
    use. The server uses ``settings.siliconflow_api_key`` (or, for
    providers other than ``siliconflow``, the matching env var if
    one is wired in later) to perform the test.
    """
    if not settings.siliconflow_api_key:
        return AITestResponse(
            success=False,
            message="服务器未配置默认 API 密钥: 请在部署时设置 SILICONFLOW_API_KEY 环境变量。",
        )

    # Build a synthetic AITestRequest so the existing test logic can
    # be reused. The internal ``test_ai_connection`` validates the
    # base URL, makes the outbound call, and shapes the response.
    internal = AITestRequest(
        provider=request.provider,
        api_key=settings.siliconflow_api_key,
        model=request.model,
        base_url=request.base_url,
    )
    return await test_ai_connection(internal)


# ============================================
# EMBEDDING MODEL TEST ENDPOINT
# ============================================


class EmbeddingTestRequest(BaseModel):
    """Request to test embedding connection"""

    provider: str
    api_key: str
    model: str
    base_url: Optional[str] = None


EMBEDDING_PROVIDER_CONFIGS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "endpoint": "/embeddings",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
    },
    "cohere": {
        "base_url": "https://api.cohere.ai/v1",
        "endpoint": "/embed",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
    },
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1",
        "endpoint": "/embeddings",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
    },
    "voyageai": {
        "base_url": "https://api.voyageai.com/v1",
        "endpoint": "/embeddings",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
    },
}


@router.post("/test-embedding", response_model=AITestResponse)
async def test_embedding_connection(request: EmbeddingTestRequest):
    """
    Test embedding provider connection by generating a test embedding.
    """
    provider = request.provider
    api_key = request.api_key
    model = request.model
    base_url = request.base_url

    logger.info(f"Testing embedding connection: provider={provider}, model={model}")

    config = EMBEDDING_PROVIDER_CONFIGS.get(
        provider,
        {
            "base_url": base_url or "",
            "endpoint": "/embeddings",
            "auth_header": "Authorization",
            "auth_prefix": "Bearer ",
        },
    )

    raw_selected_base_url = (base_url or config.get("base_url") or "").strip()
    try:
        config["base_url"] = await _validate_and_normalize_base_url(
            raw_selected_base_url
        )
    except ValueError as e:
        return AITestResponse(success=False, message=f"API 基础 URL 无效: {e}")

    try:
        headers = {
            "Content-Type": "application/json",
            config["auth_header"]: f"{config['auth_prefix']}{api_key}",
        }

        # Test text
        test_input = "This is a test for embedding model connection."

        # Build request based on provider
        if provider == "cohere":
            # Cohere uses different request format
            payload = {
                "model": model,
                "texts": [test_input],
                "input_type": "search_document",
            }
        else:
            # OpenAI-compatible format
            payload = {"model": model, "input": test_input}

        async with httpx.AsyncClient(timeout=20.0) as client:
            url = f"{config['base_url']}{config['endpoint']}"
            response = await client.post(url, headers=headers, json=payload)

            logger.info(f"Embedding test response: status={response.status_code}")

            if response.status_code == 200:
                data = response.json()

                # Check response structure
                if provider == "cohere":
                    embeddings = data.get("embeddings", [])
                    dimensions = len(embeddings[0]) if embeddings else 0
                else:
                    embeddings = data.get("data", [])
                    dimensions = (
                        len(embeddings[0].get("embedding", [])) if embeddings else 0
                    )

                return AITestResponse(
                    success=True,
                    message=f"连接成功！嵌入维度: {dimensions}",
                    model_info=f"模型: {model}, 维度: {dimensions}",
                )

            elif response.status_code == 401:
                return AITestResponse(success=False, message="无效的 API 密钥")

            elif response.status_code == 404:
                return AITestResponse(success=False, message=f"嵌入模型不存在: {model}")

            elif response.status_code == 429:
                return AITestResponse(success=False, message="请求频率过高，请稍后重试")

            else:
                try:
                    error_data = response.json()
                    error_msg = error_data.get("error", {}).get(
                        "message", str(error_data)
                    )
                except Exception:
                    error_msg = response.text[:200]

                return AITestResponse(
                    success=False,
                    message=f"API 错误 ({response.status_code}): {error_msg}",
                )

    except httpx.TimeoutException:
        return AITestResponse(success=False, message="连接超时，请检查网络")

    except httpx.ConnectError:
        return AITestResponse(success=False, message="无法连接到 API 服务器")

    except Exception as e:
        logger.error(f"Embedding test error: {e}")
        return AITestResponse(success=False, message=f"测试失败: {str(e)}")


# ============================================
# CONFIGURE SERVICES ENDPOINT
# ============================================


class ConfigureEmbeddingRequest(BaseModel):
    """Request to configure embedding service

    ``api_key`` is optional: when omitted, the server falls back to
    ``settings.siliconflow_api_key`` (or whatever the deployment
    configured). Frontends must never need to ship a raw key.
    """

    provider: str
    model: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None


class ConfigureLLMRequest(BaseModel):
    """Request to configure LLM service.

    ``api_key`` is optional: when omitted, the server falls back to
    ``settings.siliconflow_api_key`` (or whatever the deployment
    configured). Frontends must never need to ship a raw key.
    """

    provider: str
    model: str
    base_url: Optional[str] = None
    api_key: Optional[str] = None


class ConfigResponse(BaseModel):
    """Configuration response"""

    success: bool
    message: str


@router.post("/configure/embedding", response_model=ConfigResponse)
async def configure_embedding_service(request: ConfigureEmbeddingRequest):
    """
    Configure the embedding service.

    ``request.api_key`` is optional: when omitted, the server uses
    ``settings.siliconflow_api_key`` so the frontend never needs to
    handle the raw key. Callers that do supply a key are accepted for
    the BYOK path, but it's no longer required.
    """
    from ..services import embedding_service

    api_key = request.api_key or settings.siliconflow_api_key
    if not api_key:
        return ConfigResponse(
            success=False,
            message="未配置嵌入服务 API 密钥: 请在服务器端设置 SILICONFLOW_API_KEY 或随请求一起发送 api_key。",
        )

    try:
        embedding_service.configure(
            provider=request.provider,
            api_key=api_key,
            model=request.model,
            base_url=request.base_url,
        )

        logger.info(f"Embedding service configured: {request.provider}/{request.model}")

        return ConfigResponse(
            success=True,
            message=f"嵌入服务已配置: {request.provider} / {request.model}",
        )

    except Exception as e:
        logger.error(f"Failed to configure embedding: {e}")
        return ConfigResponse(success=False, message=f"配置失败: {str(e)}")


@router.post("/configure/llm", response_model=ConfigResponse)
async def configure_llm_service(request: ConfigureLLMRequest):
    """
    Configure the LLM service.

    ``request.api_key`` is optional: when omitted, the server falls
    back to ``settings.siliconflow_api_key`` so the frontend never
    needs to ship the raw key. Supplying a key still works (BYOK), but
    the field is no longer required.
    """
    from ..services import smart_classifier, review_generator, writing_assistant

    api_key = request.api_key or settings.siliconflow_api_key
    if not api_key:
        return ConfigResponse(
            success=False,
            message="未配置 LLM API 密钥: 请在服务器端设置 SILICONFLOW_API_KEY 或随请求一起发送 api_key。",
        )

    try:
        # Determine base URL
        base_url = request.base_url
        if not base_url:
            base_urls = {
                "openai": "https://api.openai.com/v1",
                "deepseek": "https://api.deepseek.com/v1",
                "siliconflow": "https://api.siliconflow.cn/v1",
            }
            base_url = base_urls.get(request.provider, "https://api.openai.com/v1")

        # Configure all services
        smart_classifier.configure_llm(
            api_key=api_key, model=request.model, base_url=base_url
        )

        review_generator.configure_llm(
            api_key=api_key, model=request.model, base_url=base_url
        )

        writing_assistant.configure_llm(
            api_key=api_key, model=request.model, base_url=base_url
        )

        # Also wire the agent runtime so SmartSearch (and the standalone
        # agent chat panel) pick up the same key. Without this the agent
        # singleton stays ``client=None`` and every /api/agent/* call
        # short-circuits with "LLM client not configured" — a real
        # confusion for users who set up the LLM in AISettings and then
        # see the agent stream fail.
        try:
            from ..agent_runtime import agent_runtime

            await agent_runtime.configure(
                api_key=api_key, model=request.model, base_url=base_url
            )
        except Exception as agent_exc:
            # Don't fail the whole configure call if the agent wire-up
            # rejects the base URL — log and continue. The other
            # services are configured and the user can still chat.
            logger.warning(
                "agent_runtime.configure skipped: %s", agent_exc
            )

        logger.info(f"AI services configured: {request.provider}/{request.model}")

        # Auto-configure embedding if possible (Side-effect to enable Clustering)
        try:
            from ..services import embedding_service

            if not embedding_service.is_configured():
                embedding_model = None
                embedding_provider = request.provider

                if request.provider == "siliconflow":
                    embedding_model = "BAAI/bge-m3"
                elif request.provider == "openai":
                    embedding_model = "text-embedding-3-small"

                if embedding_model:
                    embedding_service.configure(
                        provider=embedding_provider,
                        api_key=api_key,
                        model=embedding_model,
                        base_url=base_url,
                    )
                    logger.info(
                        f"Auto-configured default embedding service: {embedding_provider}/{embedding_model}"
                    )
        except Exception as e:
            logger.warning(f"Auto-configuration of embedding service failed: {e}")

        return ConfigResponse(
            success=True,
            message=f"LLM 服务已配置: {request.provider} / {request.model}",
        )

    except Exception as e:
        logger.error(f"Failed to configure LLM: {e}")
        return ConfigResponse(success=False, message=f"配置失败: {str(e)}")


@router.get("/status")
async def get_ai_status():
    """
    Get current AI service configuration status.
    """
    from ..services import embedding_service, smart_classifier

    stats = smart_classifier._stats

    return {
        "embedding_configured": embedding_service.is_configured(),
        "llm_configured": smart_classifier.llm_client is not None,
        "default_key_configured": bool(settings.siliconflow_api_key),
        "default_model": settings.ai_model,
        "default_base_url": settings.ai_base_url,
        "classification_stats": {
            "total": stats.total,
            "auto_classified": getattr(stats, "auto_classified", 0),
            "llm_classified": stats.llm_classified,
            "errors": getattr(stats, "errors", 0),
            "tokens_saved": getattr(stats, "tokens_saved_estimate", 0),
        },
    }

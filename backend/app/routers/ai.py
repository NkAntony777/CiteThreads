"""
AI Configuration API Router - Test AI connections
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import httpx
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])


class AITestRequest(BaseModel):
    """Request to test AI connection"""
    provider: str
    api_key: str
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
    config = PROVIDER_CONFIGS.get(provider, {
        "base_url": base_url or "",
        "test_endpoint": "/models",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
    })
    
    # Use custom base_url if provided
    if base_url:
        config["base_url"] = base_url.rstrip("/")
    
    if not config["base_url"]:
        return AITestResponse(
            success=False,
            message="未提供 API 基础 URL"
        )
    
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
                        "messages": [{"role": "user", "content": "Hi"}]
                    }
                )
                
                if response.status_code == 200:
                    return AITestResponse(
                        success=True,
                        message="连接成功！该 API 密钥与模型可用。",
                        model_info=model
                    )
                elif response.status_code == 401:
                    return AITestResponse(success=False, message="无效的 API 密钥")
                elif response.status_code == 404:
                    return AITestResponse(success=False, message=f"模型不存在: {model}")
                else:
                    data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
                    error_msg = data.get("error", {}).get("message", response.text[:100])
                    return AITestResponse(success=False, message=f"API 错误: {error_msg}")
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
                        model_info=f"已验证模型: {model}"
                    )
                else:
                    return AITestResponse(
                        success=True,
                        message=f"API 密钥有效，但模型 '{model}' 可能不存在。请确认模型名称。",
                        model_info=f"可用模型: {', '.join(model_ids[:5])}..."
                    )
            
            elif response.status_code == 401:
                return AITestResponse(
                    success=False,
                    message="无效的 API 密钥"
                )
            
            elif response.status_code == 403:
                return AITestResponse(
                    success=False,
                    message="API 密钥权限不足"
                )
            
            elif response.status_code == 429:
                return AITestResponse(
                    success=False,
                    message="请求频率过高，请稍后重试"
                )
            
            else:
                try:
                    error_data = response.json()
                    error_msg = error_data.get("error", {}).get("message", str(error_data))
                except:
                    error_msg = response.text[:200]
                
                return AITestResponse(
                    success=False,
                    message=f"API 错误 ({response.status_code}): {error_msg}"
                )
    
    except httpx.TimeoutException:
        return AITestResponse(
            success=False,
            message="连接超时，请检查网络或 API 地址"
        )
    
    except httpx.ConnectError:
        return AITestResponse(
            success=False,
            message="无法连接到 API 服务器，请检查网络"
        )
    
    except Exception as e:
        logger.error(f"AI test error: {e}")
        return AITestResponse(
            success=False,
            message=f"测试失败: {str(e)}"
        )


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
    
    config = EMBEDDING_PROVIDER_CONFIGS.get(provider, {
        "base_url": base_url or "",
        "endpoint": "/embeddings",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
    })
    
    if base_url:
        config["base_url"] = base_url.rstrip("/")
    
    if not config["base_url"]:
        return AITestResponse(success=False, message="未提供 API 基础 URL")
    
    try:
        headers = {
            "Content-Type": "application/json",
            config["auth_header"]: f"{config['auth_prefix']}{api_key}"
        }
        
        # Test text
        test_input = "This is a test for embedding model connection."
        
        # Build request based on provider
        if provider == "cohere":
            # Cohere uses different request format
            payload = {
                "model": model,
                "texts": [test_input],
                "input_type": "search_document"
            }
        else:
            # OpenAI-compatible format
            payload = {
                "model": model,
                "input": test_input
            }
        
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
                    dimensions = len(embeddings[0].get("embedding", [])) if embeddings else 0
                
                return AITestResponse(
                    success=True,
                    message=f"连接成功！嵌入维度: {dimensions}",
                    model_info=f"模型: {model}, 维度: {dimensions}"
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
                    error_msg = error_data.get("error", {}).get("message", str(error_data))
                except:
                    error_msg = response.text[:200]
                
                return AITestResponse(
                    success=False,
                    message=f"API 错误 ({response.status_code}): {error_msg}"
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
    """Request to configure embedding service"""
    provider: str
    api_key: str
    model: str
    base_url: Optional[str] = None


class ConfigureLLMRequest(BaseModel):
    """Request to configure LLM service"""
    provider: str
    api_key: str
    model: str
    base_url: Optional[str] = None


class ConfigResponse(BaseModel):
    """Configuration response"""
    success: bool
    message: str


@router.post("/configure/embedding", response_model=ConfigResponse)
async def configure_embedding_service(request: ConfigureEmbeddingRequest):
    """
    Configure the embedding service with user's API credentials.
    This enables embedding-based pre-filtering for citation classification.
    """
    from ..services import embedding_service
    
    try:
        embedding_service.configure(
            provider=request.provider,
            api_key=request.api_key,
            model=request.model,
            base_url=request.base_url
        )
        
        logger.info(f"Embedding service configured: {request.provider}/{request.model}")
        
        return ConfigResponse(
            success=True,
            message=f"嵌入服务已配置: {request.provider} / {request.model}"
        )
    
    except Exception as e:
        logger.error(f"Failed to configure embedding: {e}")
        return ConfigResponse(
            success=False,
            message=f"配置失败: {str(e)}"
        )


@router.post("/configure/llm", response_model=ConfigResponse)
async def configure_llm_service(request: ConfigureLLMRequest):
    """
    Configure the LLM service for citation intent classification.
    """
    try:
        from ..services import smart_classifier, review_generator, writing_assistant
        
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
            api_key=request.api_key,
            model=request.model,
            base_url=base_url
        )
        
        review_generator.configure_llm(
            api_key=request.api_key,
            model=request.model,
            base_url=base_url
        )
        
        writing_assistant.configure_llm(
            api_key=request.api_key,
            model=request.model,
            base_url=base_url
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
                        api_key=request.api_key,
                        model=embedding_model,
                        base_url=base_url
                    )
                    logger.info(f"Auto-configured default embedding service: {embedding_provider}/{embedding_model}")
        except Exception as e:
            logger.warning(f"Auto-configuration of embedding service failed: {e}")

        return ConfigResponse(
            success=True,
            message=f"LLM 服务已配置: {request.provider} / {request.model}"
        )
    
    except Exception as e:
        logger.error(f"Failed to configure LLM: {e}")
        return ConfigResponse(
            success=False,
            message=f"配置失败: {str(e)}"
        )


@router.get("/status")
async def get_ai_status():
    """
    Get current AI service configuration status.
    """
    from ..services import embedding_service, smart_classifier
    
    return {
        "embedding_configured": embedding_service.is_configured(),
        "llm_configured": smart_classifier.llm_client is not None,
        "classification_stats": {
            "total": smart_classifier._stats.total,
            "auto_classified": smart_classifier._stats.auto_classified,
            "llm_classified": smart_classifier._stats.llm_classified,
            "tokens_saved": smart_classifier._stats.tokens_saved_estimate,
        }
    }


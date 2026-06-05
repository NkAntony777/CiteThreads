"""
CiteThreads Backend Configuration
"""
from pydantic_settings import BaseSettings
from typing import List
import os


class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    
    # CORS
    cors_origins: List[str] = ["http://localhost:5173", "http://localhost:3000"]
    
    # API Keys
    siliconflow_api_key: str = ""
    semantic_scholar_api_key: str = ""

    # Auth: optional shared bearer token. When set (or
    # CITETHREADS_AUTH_TOKEN env var is set), every non-public endpoint
    # requires `Authorization: Bearer <token>`. See app/auth.py.
    auth_token: str = ""

    # Per-user store (P2-1). When CITETHREADS_USERS_JSON points at a
    # JSON file of {user_id, token, role, rate_limit_per_minute,
    # monthly_token_budget} entries, the auth layer uses per-user
    # tokens and the dev single-secret fallback is disabled.
    # Otherwise the legacy single-token behaviour is preserved.
    users_json_path: str = ""

    # Data Storage
    data_dir: str = "./data"
    
    # Rate Limiting
    semantic_scholar_rate_limit: int = 100  # requests per minute
    arxiv_rate_limit: int = 3  # requests per second
    
    # AI Model
    ai_model: str = "deepseek-ai/DeepSeek-V3"
    ai_base_url: str = "https://api.siliconflow.cn/v1"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

# Ensure data directory exists
os.makedirs(os.path.join(settings.data_dir, "projects"), exist_ok=True)

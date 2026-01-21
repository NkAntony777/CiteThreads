"""
Embedding Service - Cloud-based text embedding for similarity calculation
Supports multiple providers: OpenAI, Cohere, SiliconFlow, VoyageAI
"""
import httpx
import logging
import numpy as np
from typing import List, Optional, Tuple
from dataclasses import dataclass
import os

logger = logging.getLogger(__name__)


@dataclass
class EmbeddingConfig:
    """Embedding provider configuration"""
    provider: str
    api_key: str
    model: str
    base_url: str


# Default provider configs
PROVIDER_CONFIGS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "endpoint": "/embeddings",
    },
    "cohere": {
        "base_url": "https://api.cohere.ai/v1",
        "endpoint": "/embed",
    },
    "siliconflow": {
        "base_url": "https://api.siliconflow.cn/v1",
        "endpoint": "/embeddings",
    },
    "voyageai": {
        "base_url": "https://api.voyageai.com/v1",
        "endpoint": "/embeddings",
    },
}


class EmbeddingService:
    """
    Cloud-based embedding service for computing text similarities.
    Used for pre-filtering citation intent classification.
    """
    
    def __init__(self, config: Optional[EmbeddingConfig] = None):
        self.config = config
        self._client: Optional[httpx.AsyncClient] = None
    
    def configure(self, provider: str, api_key: str, model: str, base_url: Optional[str] = None):
        """Configure the embedding service"""
        if not base_url:
            base_url = PROVIDER_CONFIGS.get(provider, {}).get("base_url", "")
        
        self.config = EmbeddingConfig(
            provider=provider,
            api_key=api_key,
            model=model,
            base_url=base_url.rstrip("/")
        )
        logger.info(f"Embedding service configured: provider={provider}, model={model}")
    
    def is_configured(self) -> bool:
        """Check if the service is configured"""
        return self.config is not None and bool(self.config.api_key)
    
    async def get_embedding(self, text: str) -> Optional[List[float]]:
        """
        Get embedding vector for a single text.
        
        Args:
            text: Text to embed
            
        Returns:
            Embedding vector as list of floats, or None on error
        """
        if not self.is_configured():
            logger.warning("Embedding service not configured")
            return None
        
        embeddings = await self.get_embeddings([text])
        return embeddings[0] if embeddings else None
    
    async def get_embeddings(self, texts: List[str]) -> List[Optional[List[float]]]:
        """
        Get embedding vectors for multiple texts.
        
        Args:
            texts: List of texts to embed
            
        Returns:
            List of embedding vectors (or None for failed ones)
        """
        if not self.is_configured():
            logger.warning("Embedding service not configured")
            return [None] * len(texts)
        
        config = self.config
        provider = config.provider
        
        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config.api_key}"
            }
            
            endpoint = PROVIDER_CONFIGS.get(provider, {}).get("endpoint", "/embeddings")
            url = f"{config.base_url}{endpoint}"
            
            # Build request based on provider
            if provider == "cohere":
                payload = {
                    "model": config.model,
                    "texts": texts,
                    "input_type": "search_document"
                }
            else:
                # OpenAI-compatible format
                payload = {
                    "model": config.model,
                    "input": texts
                }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, headers=headers, json=payload)
                
                if response.status_code != 200:
                    logger.error(f"Embedding API error: {response.status_code} - {response.text[:200]}")
                    return [None] * len(texts)
                
                data = response.json()
                
                # Parse response based on provider
                if provider == "cohere":
                    embeddings = data.get("embeddings", [])
                    return embeddings if len(embeddings) == len(texts) else [None] * len(texts)
                else:
                    # OpenAI format
                    embedding_data = data.get("data", [])
                    # Sort by index to ensure correct order
                    embedding_data.sort(key=lambda x: x.get("index", 0))
                    return [item.get("embedding") for item in embedding_data]
        
        except Exception as e:
            logger.error(f"Embedding error: {e}")
            return [None] * len(texts)
    
    @staticmethod
    def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
        """
        Compute cosine similarity between two vectors.
        
        Args:
            vec1: First embedding vector
            vec2: Second embedding vector
            
        Returns:
            Cosine similarity score (0 to 1)
        """
        if not vec1 or not vec2:
            return 0.0
        
        a = np.array(vec1)
        b = np.array(vec2)
        
        dot_product = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        
        if norm_a == 0 or norm_b == 0:
            return 0.0
        
        return float(dot_product / (norm_a * norm_b))
    
    async def compute_similarity(self, text1: str, text2: str) -> float:
        """
        Compute similarity between two texts.
        
        Args:
            text1: First text
            text2: Second text
            
        Returns:
            Similarity score (0 to 1)
        """
        embeddings = await self.get_embeddings([text1, text2])
        
        if embeddings[0] is None or embeddings[1] is None:
            return 0.5  # Default neutral similarity on error
        
        return self.cosine_similarity(embeddings[0], embeddings[1])
    
    async def batch_compute_similarities(
        self, 
        pairs: List[Tuple[str, str]]
    ) -> List[float]:
        """
        Compute similarities for multiple text pairs efficiently.
        
        Args:
            pairs: List of (text1, text2) tuples
            
        Returns:
            List of similarity scores
        """
        if not pairs:
            return []
        
        # Collect all unique texts
        all_texts = []
        text_to_idx = {}
        
        for text1, text2 in pairs:
            for text in [text1, text2]:
                if text not in text_to_idx:
                    text_to_idx[text] = len(all_texts)
                    all_texts.append(text)
        
        # Get all embeddings in one batch
        embeddings = await self.get_embeddings(all_texts)
        
        # Compute similarities
        similarities = []
        for text1, text2 in pairs:
            idx1 = text_to_idx[text1]
            idx2 = text_to_idx[text2]
            
            emb1 = embeddings[idx1]
            emb2 = embeddings[idx2]
            
            if emb1 is None or emb2 is None:
                similarities.append(0.5)  # Default on error
            else:
                similarities.append(self.cosine_similarity(emb1, emb2))
        
        return similarities


# Singleton instance
embedding_service = EmbeddingService()

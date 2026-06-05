"""
Base Crawler - Shared HTTP request logic for all crawlers.
Provides rate limiting, retry, and consistent error handling.
"""

import asyncio
import logging
from typing import Optional, Dict, Any

import httpx

logger = logging.getLogger(__name__)


class BaseCrawler:
    """
    Base class for API crawlers with shared HTTP request logic.
    Subclasses should set self.base_url and self.headers in __init__.
    """

    base_url: str = ""
    headers: Dict[str, str] = {}

    def __init__(self, timeout: float = 30.0, max_concurrency: int = 3):
        self._timeout = timeout
        self._sem = asyncio.Semaphore(max_concurrency)

    async def _request(
        self,
        endpoint: str,
        params: Optional[Dict] = None,
        method: str = "GET",
        json_body: Optional[Dict] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Make a rate-limited HTTP request.

        Returns parsed JSON dict, or None on error/not-found.
        """
        async with self._sem:
            url = f"{self.base_url}{endpoint}" if not endpoint.startswith("http") else endpoint
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout, follow_redirects=True
                ) as client:
                    if method == "POST":
                        response = await client.post(
                            url, headers=self.headers, params=params, json=json_body
                        )
                    else:
                        response = await client.get(
                            url, headers=self.headers, params=params
                        )

                    if response.status_code == 200:
                        return response.json()
                    elif response.status_code == 404:
                        return None
                    elif response.status_code == 429:
                        logger.warning(f"{self.__class__.__name__} rate limited (429): {url}")
                        return None
                    else:
                        logger.warning(
                            f"{self.__class__.__name__} API error {response.status_code}: "
                            f"{response.text[:200]}"
                        )
                        return None
            except httpx.TimeoutException:
                logger.warning(f"{self.__class__.__name__} timeout: {url}")
                return None
            except Exception as e:
                logger.error(f"{self.__class__.__name__} request failed: {e}")
                return None

    async def _request_text(
        self,
        url: str,
        params: Optional[Dict] = None,
    ) -> Optional[str]:
        """
        Make a rate-limited HTTP request returning raw text (for XML APIs).
        """
        async with self._sem:
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout, follow_redirects=True
                ) as client:
                    response = await client.get(url, headers=self.headers, params=params)

                    if response.status_code == 200:
                        return response.text
                    elif response.status_code == 404:
                        return None
                    else:
                        logger.warning(
                            f"{self.__class__.__name__} API error {response.status_code}: {url}"
                        )
                        return None
            except Exception as e:
                logger.error(f"{self.__class__.__name__} request failed: {e}")
                return None

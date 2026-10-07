"""
Robust HTTP Client without Tor.

High-performance HTTP client with connection pooling, automatic retries,
exponential backoff, and comprehensive error handling.
"""

import logging
import random
import threading
import time
from typing import Optional, Tuple

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from config import config

logger = logging.getLogger(__name__)


class RobustHttpClient:
    """Thread-safe HTTP client with connection pooling, retry logic, and timeouts."""

    _instance: Optional["RobustHttpClient"] = None
    _client: Optional[httpx.Client] = None
    _lock = threading.Lock()
    _last_request_time = 0.0
    _request_delay_lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._client is None:
            self._initialize_client()

    def _initialize_client(self):
        """Initialize the httpx client with optimal settings."""
        self._client = httpx.Client(
            timeout=httpx.Timeout(
                connect=10.0,
                read=30.0,
                write=10.0,
                pool=5.0,
            ),
            limits=httpx.Limits(
                max_connections=config.MAX_WORKERS,
                max_keepalive_connections=max(1, config.MAX_WORKERS // 2),
                keepalive_expiry=30.0,
            ),
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Cache-Control": "max-age=0",
            },
            follow_redirects=True,
            verify=True,
            http2=True,
        )
        logger.info(f"[HttpClient] Initialized with {config.MAX_WORKERS} workers, "
                   f"delay={config.REQUEST_DELAY_SECONDS}s, HTTP/2, retry logic")

    def _respect_rate_limit(self):
        """Enforce minimum delay between requests to be respectful to the server."""
        delay = config.REQUEST_DELAY_SECONDS
        if delay <= 0:
            return
        with self._request_delay_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < delay:
                time.sleep(delay - elapsed)
            self._last_request_time = time.monotonic()

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type((
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
            httpx.ProtocolError,
            ConnectionError,
            OSError,
        )),
        reraise=True,
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def get(self, url: str, **kwargs) -> httpx.Response:
        """GET request with automatic retry and rate limiting."""
        self._respect_rate_limit()
        response = self._client.get(url, **kwargs)
        # Do NOT raise on 4xx/5xx - let caller handle it
        return response

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type((
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
            httpx.ProtocolError,
            ConnectionError,
            OSError,
        )),
        reraise=True,
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def post(self, url: str, **kwargs) -> httpx.Response:
        """POST request with automatic retry and rate limiting."""
        self._respect_rate_limit()
        response = self._client.post(url, **kwargs)
        # Do NOT raise on 4xx/5xx - let caller handle it
        return response

    def close(self):
        """Close the HTTP client and release resources."""
        if self._client:
            self._client.close()
            self._client = None
            logger.info("[HttpClient] Closed")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# Global singleton instance
http_client = RobustHttpClient()


def get_client() -> RobustHttpClient:
    """Get the global HTTP client instance."""
    return http_client


def close_client():
    """Close the global HTTP client."""
    http_client.close()
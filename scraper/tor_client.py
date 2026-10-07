"""
High-performance HTTP client using Tor SOCKS5 proxy.
"""

import time
import random
import threading
from typing import Optional, Tuple
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import config


class TorClient:
    """High-performance HTTP client with Tor SOCKS5 proxy support."""

    def __init__(self):
        self.session: Optional[requests.Session] = None
        self.request_count: int = 0
        self.lock = threading.Lock()
        self.last_url: Optional[str] = None

    def start(self) -> bool:
        """Initialize the HTTP session with optional Tor proxy."""
        try:
            self.session = requests.Session()

            # Configure retry strategy
            retry_strategy = Retry(
                total=config.MAX_RETRIES,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["GET"]
            )
            adapter = HTTPAdapter(
                max_retries=retry_strategy,
                pool_connections=config.MAX_WORKERS,
                pool_maxsize=config.MAX_WORKERS * 2
            )
            self.session.mount("http://", adapter)
            self.session.mount("https://", adapter)

            # Try to configure Tor proxy if enabled
            if config.USE_TOR:
                tor_working = self._test_tor_connection()
                if tor_working:
                    print("[TorClient] Tor SOCKS5 proxy is working")
                else:
                    print("[TorClient] Tor SOCKS5 proxy not available, falling back to direct HTTP")
                    config.USE_TOR = False
            else:
                print("[TorClient] Direct HTTP requests (no Tor)")

            # Set headers
            self.session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'de,en-US;q=0.7,en;q=0.3',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            })

            print("[TorClient] HTTP session initialized")
            return True

        except Exception as e:
            print(f"[TorClient] Failed to initialize: {e}")
            return False

    def _test_tor_connection(self) -> bool:
        """Test if Tor SOCKS5 proxy is reachable and working."""
        try:
            test_session = requests.Session()
            test_session.proxies = {
                'http': f'socks5h://{config.TOR_SOCKS_HOST}:{config.TOR_SOCKS_PORT}',
                'https': f'socks5h://{config.TOR_SOCKS_HOST}:{config.TOR_SOCKS_PORT}'
            }
            test_session.headers.update({'User-Agent': 'Mozilla/5.0'})
            response = test_session.get('https://antraege.gruene.de/52bdk', timeout=10)
            return response.status_code < 500
        except Exception:
            return False

    def stop(self):
        """Close the HTTP session."""
        if self.session:
            try:
                self.session.close()
                print("[TorClient] Session closed")
            except Exception as e:
                print(f"[TorClient] Error closing session: {e}")

    def _rotate_circuit_if_needed(self):
        """Rotate Tor circuit by changing the proxy connection."""
        if config.USE_TOR and self.request_count > 0 and self.request_count % config.TOR_CIRCUIT_ROTATION_INTERVAL == 0:
            self._rotate_circuit()

    def _rotate_circuit(self):
        """Rotate Tor circuit."""
        try:
            # Close current session and create new one
            if self.session:
                self.session.close()
            
            self.session = requests.Session()
            
            retry_strategy = Retry(
                total=config.MAX_RETRIES,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["GET"]
            )
            adapter = HTTPAdapter(
                max_retries=retry_strategy,
                pool_connections=config.MAX_WORKERS,
                pool_maxsize=config.MAX_WORKERS * 2
            )
            self.session.mount("http://", adapter)
            self.session.mount("https://", adapter)

            if config.USE_TOR:
                self.session.proxies = {
                    'http': f'socks5h://{config.TOR_SOCKS_HOST}:{config.TOR_SOCKS_PORT}',
                    'https': f'socks5h://{config.TOR_SOCKS_HOST}:{config.TOR_SOCKS_PORT}'
                }

            self.session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'de,en-US;q=0.7,en;q=0.3',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1'
            })

            print("[TorClient] Circuit rotated")
        except Exception as e:
            print(f"[TorClient] Circuit rotation failed: {e}")

    def get(self, url: str, delay: Optional[float] = None) -> Tuple[str, str, bool]:
        """
        Make a GET request to the given URL.

        Args:
            url: The URL to request
            delay: Optional delay before request (for rate limiting)

        Returns:
            Tuple of (html_content, final_url, success)
        """
        if delay is None:
            delay = config.REQUEST_DELAY_SECONDS

        time.sleep(delay + random.uniform(0, 0.3))

        with self.lock:
            self._rotate_circuit_if_needed()
            self.request_count += 1

        if not self.session:
            return self._direct_request(url)

        try:
            response = self.session.get(
                url,
                timeout=config.REQUEST_TIMEOUT_SECONDS,
                allow_redirects=True
            )
            
            final_url = response.url
            html_content = response.text

            self.last_url = final_url
            return html_content, final_url, True

        except requests.exceptions.Timeout:
            print(f"[TorClient] Timeout loading {url}")
            return "", url, False
        except requests.exceptions.RequestException as e:
            print(f"[TorClient] Request error: {e}")
            return "", url, False
        except Exception as e:
            print(f"[TorClient] Unexpected error: {e}")
            return "", url, False

    def _direct_request(self, url: str) -> Tuple[str, str, bool]:
        """Fallback: Direct HTTP request without Tor."""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(url, headers=headers, timeout=config.REQUEST_TIMEOUT_SECONDS)
            return response.text, response.url, True
        except Exception as e:
            print(f"[TorClient] Direct request failed: {e}")
            return "", url, False

    def get_final_url(self, url: str) -> Tuple[str, bool]:
        """
        Get the final URL after following redirects.

        Returns:
            Tuple of (final_url, success)
        """
        html, final_url, success = self.get(url)
        return final_url, success

    def is_tor_active(self) -> bool:
        """Check if Tor proxy is configured."""
        return self.session is not None and config.USE_TOR

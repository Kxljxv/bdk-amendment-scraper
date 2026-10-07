"""
Amendment ID Scanner (Phase 1).
Scans amendment IDs to find all existing amendments for the convention.
"""

import re
import logging
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import config
from http_client import get_client
from storage import Storage

logger = logging.getLogger(__name__)


class AmendmentScanner:
    """Scans amendment IDs to find existing amendments."""

    def __init__(self, storage: Storage):
        self.http_client = get_client()
        self.storage = storage
        self.results: List[Dict] = []

    def _build_scan_url(self, amendment_id: int) -> str:
        """Build the URL for scanning an amendment ID."""
        return (
            f"{config.BASE_URL}/{config.CONVENTION}/"
            f"{config.KNOWN_MOTION_SLUG}/{amendment_id}"
        )

    def _is_error_page(self, html: str) -> bool:
        """Check if the HTML is an error page (amendment exists but not readable)."""
        if config.ERROR_INDICATOR_TEXT in html:
            return True
        if "<title>error</title>" in html.lower():
            return True
        return False

    def _is_not_found_redirect(self, final_url: str) -> bool:
        """Check if the final URL indicates the amendment was not found."""
        path = final_url.rstrip("/").split("?")[0]

        if path == f"{config.BASE_URL}/{config.CONVENTION}":
            return True

        return False

    def _extract_motion_slug_from_final_url(self, final_url: str) -> Optional[str]:
        """Extract motion slug from the final URL after redirect."""
        pattern = rf"/{config.CONVENTION}/([^/]+)/\d+$"
        match = re.search(pattern, final_url)
        if match:
            return match.group(1)
        return None

    def scan_single_id(self, amendment_id: int) -> Dict:
        """
        Scan a single amendment ID.

        Returns:
            Dict with keys: amendment_id, motion_slug, readable, final_url, exists
        """
        url = self._build_scan_url(amendment_id)

        try:
            response = self.http_client.get(url)
            html = response.text
            final_url = str(response.url)
            status_code = response.status_code
        except Exception as e:
            logger.warning(f"[Scanner] Request failed for ID {amendment_id}: {e}")
            return {
                "amendment_id": amendment_id,
                "motion_slug": None,
                "readable": False,
                "final_url": url,
                "exists": False,
                "error": True
            }

        # Handle 404 - check if it's a draft/error page with content
        if status_code == 404:
            # Check if the 404 page contains draft content
            if self._is_error_page(html):
                # This is a draft amendment - extract motion slug from the HTML
                motion_slug = self._extract_motion_slug_from_html(html)
                if not motion_slug:
                    # Fallback: try to extract from final URL
                    motion_slug = self._extract_motion_slug_from_final_url(final_url)
                
                if motion_slug:
                    logger.info(f"[Scanner] ID {amendment_id}: DRAFT (404) -> {motion_slug}")
                    return {
                        "amendment_id": amendment_id,
                        "motion_slug": motion_slug,
                        "readable": False,  # Draft = not readable
                        "final_url": final_url,
                        "exists": True,
                        "error": False
                    }
            
            # True 404 - not found
            return {
                "amendment_id": amendment_id,
                "motion_slug": None,
                "readable": False,
                "final_url": final_url,
                "exists": False,
                "error": False
            }

        if self._is_not_found_redirect(final_url):
            return {
                "amendment_id": amendment_id,
                "motion_slug": None,
                "readable": False,
                "final_url": final_url,
                "exists": False,
                "error": False
            }

        motion_slug = self._extract_motion_slug_from_final_url(final_url)
        if not motion_slug:
            logger.warning(f"[Scanner] ID {amendment_id}: Could not extract motion slug from {final_url}")
            return {
                "amendment_id": amendment_id,
                "motion_slug": None,
                "readable": False,
                "final_url": final_url,
                "exists": False,
                "error": True
            }

        readable = not self._is_error_page(html)

        status = "READABLE" if readable else "NOT READABLE"
        logger.info(f"[Scanner] ID {amendment_id}: {status} -> {motion_slug}")

        return {
            "amendment_id": amendment_id,
            "motion_slug": motion_slug,
            "readable": readable,
            "final_url": final_url,
            "exists": True,
            "error": False
        }

    def _extract_motion_slug_from_html(self, html: str) -> Optional[str]:
        """Extract motion slug from breadcrumb or page content on error/draft pages."""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            
            # Try breadcrumb first
            breadcrumb = soup.find(class_="breadcrumb")
            if breadcrumb:
                items = breadcrumb.find_all("li")
                if len(items) >= 2:
                    # Second item usually has the motion link
                    link = items[1].find("a", href=True)
                    if link:
                        href = link["href"]
                        import re
                        match = re.search(rf"/{config.CONVENTION}/([^/]+)", href)
                        if match:
                            return match.group(1)
            
            # Try to find any link to a motion page
            for link in soup.find_all("a", href=True):
                href = link["href"]
                if f"/{config.CONVENTION}/" in href and "/amendment/" not in href:
                    import re
                    match = re.search(rf"/{config.CONVENTION}/([^/]+)", href)
                    if match:
                        slug = match.group(1)
                        if slug not in ["login", "motion", "rest", "page", "pdfcollection"]:
                            return slug
                        
        except Exception:
            pass
        return None

    def scan_range(
        self,
        start_id: int,
        max_consecutive_not_found: int = 50,
    ) -> List[Dict]:
        """
        Scan a range of amendment IDs starting from start_id.

        Stops after max_consecutive_not_found consecutive IDs that don't exist.

        Args:
            start_id: The starting amendment ID
            max_consecutive_not_found: Stop after this many consecutive not-found IDs

        Returns:
            List of scan results
        """
        logger.info(f"[Scanner] Starting scan from ID {start_id}")
        logger.info(f"[Scanner] Will stop after {max_consecutive_not_found} consecutive not-found IDs")
        logger.info(f"[Scanner] Using {config.MAX_WORKERS} workers")

        current_id = start_id
        consecutive_not_found = 0
        found_count = 0
        batch_size = config.MAX_WORKERS * 2

        while True:
            # Process in batches
            batch_ids = list(range(current_id, current_id + batch_size))
            batch_results = self._scan_batch(batch_ids)

            for result in batch_results:
                if not result["exists"]:
                    consecutive_not_found += 1

                    if consecutive_not_found >= max_consecutive_not_found:
                        logger.info(
                            f"\n[Scanner] Stopping: {max_consecutive_not_found} consecutive "
                            f"not-found IDs reached at ID {result['amendment_id']}"
                        )
                        return self.results
                else:
                    consecutive_not_found = 0
                    found_count += 1
                    self.results.append(result)
                    self.storage.add_amendment_url(
                        result["amendment_id"],
                        result["motion_slug"],
                        result["readable"],
                        result["final_url"],
                    )

            current_id += batch_size

        logger.info(f"\n[Scanner] Scan complete. Found {found_count} amendments.")
        logger.info(f"[Scanner] ID range scanned: {start_id} - {current_id - 1}")

        return self.results

    def _scan_batch(self, amendment_ids: List[int]) -> List[Dict]:
        """
        Scan a batch of amendment IDs in parallel.

        Args:
            amendment_ids: List of amendment IDs to scan

        Returns:
            List of scan results
        """
        results = []

        if config.USE_THREADING:
            with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as executor:
                future_to_id = {
                    executor.submit(self.scan_single_id, aid): aid
                    for aid in amendment_ids
                }

                for future in as_completed(future_to_id):
                    try:
                        result = future.result()
                        results.append(result)
                    except Exception as e:
                        aid = future_to_id[future]
                        logger.error(f"[Scanner] Error processing ID {aid}: {e}")
                        results.append({
                            "amendment_id": aid,
                            "motion_slug": None,
                            "readable": False,
                            "final_url": "",
                            "exists": False,
                            "error": True
                        })
        else:
            # Sequential processing for debugging
            for aid in amendment_ids:
                result = self.scan_single_id(aid)
                results.append(result)

        return results

    def get_results(self) -> List[Dict]:
        """Get the scan results."""
        return self.results

    def get_found_ids(self) -> List[int]:
        """Get list of found amendment IDs."""
        return [r["amendment_id"] for r in self.results if r["exists"]]

    def get_readable_ids(self) -> List[int]:
        """Get list of readable amendment IDs."""
        return [r["amendment_id"] for r in self.results if r["exists"] and r["readable"]]

    def get_not_readable_ids(self) -> List[int]:
        """Get list of non-readable amendment IDs."""
        return [r["amendment_id"] for r in self.results if r["exists"] and not r["readable"]]
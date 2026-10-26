"""
Content Scraper (Phase 2).
Scrapes the actual content for found amendments and motions.
"""

import re
import logging
from typing import Optional, List, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import config
from http_client import get_client
from storage import Storage
from cloudflare_sync import fetch_ajax_diff, parse_amendment_page

logger = logging.getLogger(__name__)


class ContentScraper:
    """Scrapes content for amendments and motions."""

    def __init__(self, storage: Storage):
        self.http_client = get_client()
        self.storage = storage

    def extract_main_content(self, html: str) -> tuple[str, str]:
        """
        Extract the main content element and breadcrumb from HTML.

        Args:
            html: The HTML content of the page

        Returns:
            Tuple of (content_html, breadcrumb_html)
        """
        try:
            import bs4
            soup = bs4.BeautifulSoup(html, "html.parser")
            content = soup.find(class_=config.CONTENT_CLASS)
            content_html = str(content) if content else '<p class="error">Element "antragsgruen-width-main" not found</p>'

            breadcrumb = soup.find(class_="breadcrumb")
            breadcrumb_html = str(breadcrumb) if breadcrumb else ""

            return content_html, breadcrumb_html
        except Exception as e:
            logger.error(f"[ContentScraper] Error parsing HTML: {e}")
            return f'<p class="error">HTML parsing error: {e}</p>', ""

    def scrape_single_amendment(self, result: Dict) -> bool:
        """
        Scrape a single amendment and save it.

        Args:
            result: Dict with keys: amendment_id, motion_slug, readable, final_url

        Returns:
            True if successful, False otherwise
        """
        amendment_id = result["amendment_id"]
        motion_slug = result["motion_slug"]
        readable = result["readable"]
        url = result["final_url"]

        try:
            response = self.http_client.get(url)
            html = response.text
        except Exception as e:
            logger.error(f"[ContentScraper] Failed to load amendment {amendment_id}: {e}")
            return False

        content_html, breadcrumb_html = self.extract_main_content(html)

        self.storage.save_amendment(
            amendment_id=amendment_id,
            motion_slug=motion_slug,
            readable=readable,
            url=url,
            content_html=content_html,
            breadcrumb_html=breadcrumb_html,
        )

        # Structured metadata for the Cloudflare sync
        try:
            record = parse_amendment_page(
                html,
                amendment_id=amendment_id,
                motion_slug=motion_slug,
                url=url,
            )
            if not record.get("motion_slug"):
                record["motion_slug"] = motion_slug

            # The affected paragraphs are needed to place the amendment markers
            # next to the lines that the amendment changes.
            try:
                record["diff_blocks"] = fetch_ajax_diff(self.http_client, url)
            except Exception as e:
                logger.warning(f"[ContentScraper] Error loading the diff of {amendment_id}: {e}")
                record["diff_blocks"] = []

            self.storage.save_amendment_record(record)
        except Exception as e:
            logger.error(f"[ContentScraper] Error parsing amendment {amendment_id}: {e}")

        return True

    def scrape_amendments_batch(self, scan_results: List[Dict]) -> int:
        """
        Scrape multiple amendments from scan results.

        Args:
            scan_results: List of dicts from AmendmentScanner

        Returns:
            Number of successfully scraped amendments
        """
        valid_results = [r for r in scan_results if r.get("exists", False)]
        logger.info(f"[ContentScraper] Starting to scrape {len(valid_results)} amendments")
        logger.info(f"[ContentScraper] Using {config.MAX_WORKERS} workers")

        success_count = 0

        if config.USE_THREADING:
            with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as executor:
                future_to_id = {
                    executor.submit(self.scrape_single_amendment, result): result["amendment_id"]
                    for result in valid_results
                }

                for future in as_completed(future_to_id):
                    try:
                        success = future.result()
                        if success:
                            success_count += 1
                    except Exception as e:
                        aid = future_to_id[future]
                        logger.error(f"[ContentScraper] Error scraping amendment {aid}: {e}")
        else:
            # Sequential processing for debugging
            for result in valid_results:
                success = self.scrape_single_amendment(result)
                if success:
                    success_count += 1

        logger.info(f"[ContentScraper] Scraping complete. Success: {success_count}/{len(valid_results)}")
        return success_count

    def scrape_single_motion(self, motion_slug: str, url: str) -> bool:
        """
        Scrape a single motion and save it.

        Args:
            motion_slug: The motion slug
            url: The full URL of the motion

        Returns:
            True if successful, False otherwise
        """
        try:
            response = self.http_client.get(url)
            html = response.text
        except Exception as e:
            logger.error(f"[ContentScraper] Failed to load motion {motion_slug}: {e}")
            return False

        content_html, _ = self.extract_main_content(html)

        self.storage.save_motion(
            motion_slug=motion_slug,
            url=url,
            content_html=content_html,
        )

        return True

    def scrape_motions_batch(self, motion_urls: List[str]) -> int:
        """
        Scrape multiple motions in parallel.

        Args:
            motion_urls: List of motion URLs

        Returns:
            Number of successfully scraped motions
        """
        logger.info(f"[ContentScraper] Starting to scrape {len(motion_urls)} motions")
        logger.info(f"[ContentScraper] Using {config.MAX_WORKERS} workers")

        success_count = 0

        if config.USE_THREADING:
            with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as executor:
                future_to_url = {
                    executor.submit(self.scrape_single_motion_from_url, url): url
                    for url in motion_urls
                }

                for future in as_completed(future_to_url):
                    try:
                        success = future.result()
                        if success:
                            success_count += 1
                    except Exception as e:
                        url = future_to_url[future]
                        logger.error(f"[ContentScraper] Error scraping motion {url}: {e}")
        else:
            # Sequential processing for debugging
            for url in motion_urls:
                success = self.scrape_single_motion_from_url(url)
                if success:
                    success_count += 1

        logger.info(f"[ContentScraper] Motion scraping complete. Success: {success_count}/{len(motion_urls)}")
        return success_count

    def scrape_single_motion_from_url(self, url: str) -> bool:
        """
        Scrape a single motion from URL.

        Args:
            url: The full URL of the motion

        Returns:
            True if successful, False otherwise
        """
        slug_match = re.search(rf"/{config.CONVENTION}/([^/]+)$", url)
        if not slug_match:
            logger.error(f"[ContentScraper] Could not extract slug from {url}")
            return False

        motion_slug = slug_match.group(1)
        return self.scrape_single_motion(motion_slug, url)
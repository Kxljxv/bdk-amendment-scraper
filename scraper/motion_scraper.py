"""
Motion Scraper (Phase 3).
Scrapes motion pages from the convention start page.
"""

import re
import logging
from typing import List

from bs4 import BeautifulSoup

from config import config
from http_client import get_client
from storage import Storage

logger = logging.getLogger(__name__)


class MotionScraper:
    """Scrapes motion pages from the convention start page."""

    def __init__(self, storage: Storage):
        self.http_client = get_client()
        self.storage = storage

    def get_start_page_url(self) -> str:
        """Get the URL of the convention start page."""
        return f"{config.BASE_URL}/{config.CONVENTION}"

    def extract_motion_links(self, html: str) -> List[str]:
        """
        Extract all motion links from the start page.

        Args:
            html: The HTML content of the start page

        Returns:
            List of absolute URLs to motion pages
        """
        soup = BeautifulSoup(html, "html.parser")
        motion_links = []

        # Find all links
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")

            # Filter out amendment links, user links, etc.
            if "/amendment/" in href:
                continue
            if href.startswith(f"/{config.CONVENTION}/"):
                # Exclude non-motion links
                if any(exclude in href for exclude in ["/user/", "/login", "/logout", "/rest/"]):
                    continue

                absolute_url = f"{config.BASE_URL}{href}" if href.startswith("/") else href
                if absolute_url not in motion_links:
                    motion_links.append(absolute_url)

        return motion_links

    def scrape_start_page(self) -> List[str]:
        """
        Scrape the start page and extract all motion links.

        Returns:
            List of motion URLs
        """
        url = self.get_start_page_url()
        logger.info(f"[MotionScraper] Scraping start page: {url}")

        try:
            response = self.http_client.get(url)
            html = response.text
        except Exception as e:
            logger.error(f"[MotionScraper] Failed to load start page: {e}")
            return []

        motion_links = self.extract_motion_links(html)
        logger.info(f"[MotionScraper] Found {len(motion_links)} motion links")

        return motion_links

    def scrape_motion(self, url: str) -> bool:
        """
        Scrape a single motion and save it.

        Args:
            url: The full URL of the motion

        Returns:
            True if successful, False otherwise
        """
        try:
            response = self.http_client.get(url)
            html = response.text
            final_url = str(response.url)
        except Exception as e:
            slug_match = re.search(rf"/{config.CONVENTION}/([^/]+)$", url)
            slug = slug_match.group(1) if slug_match else url
            logger.error(f"[MotionScraper] Failed to load motion {slug}: {e}")
            return False

        # Extract motion slug from URL
        slug_match = re.search(rf"/{config.CONVENTION}/([^/]+)$", final_url)
        if not slug_match:
            logger.error(f"[MotionScraper] Could not extract slug from {final_url}")
            return False

        motion_slug = slug_match.group(1)

        # Extract main content
        try:
            soup = BeautifulSoup(html, "html.parser")
            content = soup.find(class_=config.CONTENT_CLASS)
            content_html = str(content) if content else '<p class="error">Element not found</p>'
        except Exception as e:
            logger.error(f"[MotionScraper] Error parsing HTML: {e}")
            content_html = f'<p class="error">HTML parsing error: {e}</p>'

        self.storage.save_motion(
            motion_slug=motion_slug,
            url=final_url,
            content_html=content_html,
        )

        return True

    def scrape_all_motions(self) -> int:
        """
        Scrape all motions from the start page.

        Returns:
            Number of successfully scraped motions
        """
        motion_urls = self.scrape_start_page()

        if not motion_urls:
            logger.info("[MotionScraper] No motions found to scrape")
            return 0

        logger.info(f"[MotionScraper] Starting to scrape {len(motion_urls)} motions")
        logger.info(f"[MotionScraper] Using {config.MAX_WORKERS} workers")

        success_count = 0

        if config.USE_THREADING:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as executor:
                future_to_url = {
                    executor.submit(self.scrape_motion, url): url
                    for url in motion_urls
                }

                for future in as_completed(future_to_url):
                    try:
                        success = future.result()
                        if success:
                            success_count += 1
                    except Exception as e:
                        url = future_to_url[future]
                        logger.error(f"[MotionScraper] Error scraping motion {url}: {e}")
        else:
            # Sequential processing for debugging
            for url in motion_urls:
                success = self.scrape_motion(url)
                if success:
                    success_count += 1

        logger.info(f"[MotionScraper] Motion scraping complete. Success: {success_count}/{len(motion_urls)}")
        return success_count
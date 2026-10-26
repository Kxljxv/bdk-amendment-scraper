"""
Motion Scraper (Phase 3).
Scrapes motion pages from the convention start page.
"""

import re
import logging
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

from config import config
from http_client import get_client
from storage import Storage
from cloudflare_sync import parse_motion_page

logger = logging.getLogger(__name__)

#: Motion URLs always consist of the convention and the motion slug.
_MOTION_URL_RE = re.compile(rf"^/{re.escape(config.CONVENTION)}/([^/?#]+)/?$")

#: Amendment URLs consist of the convention, the motion slug and the id.
_AMENDMENT_URL_RE = re.compile(rf"^/{re.escape(config.CONVENTION)}/([^/?#]+)/\d+/?$")


class MotionScraper:
    """Scrapes motion pages from the convention start page."""

    def __init__(self, storage: Storage):
        self.http_client = get_client()
        self.storage = storage
        self.motion_ids: Dict[str, int] = {}
        self._motion_urls: Optional[List[str]] = None

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

        # Primary source: the motion list markup carries a "motionLink<id>"
        # class on every link that points to a motion page.
        for link in soup.find_all("a", href=True):
            classes = link.get("class") or []
            href = link.get("href", "")

            if any(name.startswith("motionLink") for name in classes):
                match = _MOTION_URL_RE.match(href)
                if not match:
                    continue
                absolute_url = f"{config.BASE_URL}/{config.CONVENTION}/{match.group(1)}"
                if absolute_url not in motion_links:
                    motion_links.append(absolute_url)
            elif any(name.startswith("amendmentLink") for name in classes):
                # Statutes ("Gesetze") are listed as amendments of type
                # "amendmentsOnly". Their parent motion is not linked as a
                # motion, so it is added explicitly here.
                match = _AMENDMENT_URL_RE.match(href)
                if not match:
                    continue
                parent_url = f"{config.BASE_URL}/{config.CONVENTION}/{match.group(1)}"
                if parent_url not in motion_links:
                    motion_links.append(parent_url)

        if motion_links:
            return motion_links

        # Fallback: use every link that points to a motion-like path.
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            if not href.startswith(f"/{config.CONVENTION}/"):
                continue
            if any(exclude in href for exclude in ["/user/", "/login", "/logout", "/rest/", "/amendment/"]):
                continue
            absolute_url = f"{config.BASE_URL}{href}"
            if absolute_url not in motion_links:
                motion_links.append(absolute_url)

        return motion_links

    def extract_motion_ids(self, html: str) -> Dict[str, int]:
        """
        Extract the numeric motion id for every motion listed on a page.

        The motion list markup carries the id in the ``motionRow<id>`` /
        ``motionLink<id>`` class names.

        Args:
            html: The HTML content of a motion list page

        Returns:
            Mapping of motion slug to motion id
        """
        soup = BeautifulSoup(html, "html.parser")
        result: Dict[str, int] = {}

        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            match = _MOTION_URL_RE.match(href)
            if not match:
                continue
            slug = match.group(1)
            for class_name in link.get("class") or []:
                id_match = re.match(r"^motionLink(\d+)$", class_name)
                if id_match:
                    result[slug] = int(id_match.group(1))
                    break

        return result

    def scrape_start_page(self) -> List[str]:
        """
        Scrape the start page and extract all motion links.

        The result is cached, so that the start page is only requested once per
        run.

        Returns:
            List of motion URLs
        """
        if self._motion_urls is not None:
            return self._motion_urls

        url = self.get_start_page_url()
        logger.info(f"[MotionScraper] Scraping start page: {url}")

        try:
            response = self.http_client.get(url)
            html = response.text
        except Exception as e:
            logger.error(f"[MotionScraper] Failed to load start page: {e}")
            return []

        motion_links = self.extract_motion_links(html)
        self.motion_ids.update(self.extract_motion_ids(html))
        logger.info(
            f"[MotionScraper] Found {len(motion_links)} motion links "
            f"({len(self.motion_ids)} motion ids)"
        )

        self._motion_urls = motion_links
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

        # Structured metadata (including the officially visible amendments)
        try:
            record = parse_motion_page(html, slug=motion_slug, url=final_url)
            record["motion_id"] = self.motion_ids.get(motion_slug)
            self.storage.save_motion_record(record)
        except Exception as e:
            logger.error(f"[MotionScraper] Error parsing motion {motion_slug}: {e}")

        return True

    def count_motion_pages(self, extra_slugs: Optional[List[str]] = None) -> int:
        """
        Returns the number of motion pages that are going to be scraped.

        Args:
            extra_slugs: Additional motion slugs that are not linked on the
                start page

        Returns:
            Number of motion pages
        """
        return len(self.collect_motion_urls(extra_slugs))

    def collect_motion_urls(self, extra_slugs: Optional[List[str]] = None) -> List[str]:
        """Returns the URLs of all motion pages, including the extra slugs."""
        motion_urls = list(self.scrape_start_page())
        for slug in extra_slugs or []:
            url = f"{config.BASE_URL}/{config.CONVENTION}/{slug}"
            if url not in motion_urls:
                motion_urls.append(url)
        return motion_urls

    def scrape_all_motions(self, extra_slugs: Optional[List[str]] = None) -> int:
        """
        Scrape all motions from the start page.

        Args:
            extra_slugs: Additional motion slugs that are not linked on the
                start page (for example the parents of statutes that are not
                publicly listed yet).

        Returns:
            Number of successfully scraped motions
        """
        motion_urls = self.collect_motion_urls(extra_slugs)

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

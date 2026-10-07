"""
Storage module for saving scraped data to files.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import config


class Storage:
    """Handles saving scraped data to files."""

    def __init__(self, output_dir: Optional[Path] = None):
        if output_dir:
            self.output_dir = output_dir
        else:
            self.output_dir = config.OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.url_list_path = self.output_dir / "amendment_urls.txt"
        self._init_url_list()

    def _init_url_list(self):
        """Initialize the URL list file if it doesn't exist."""
        if not self.url_list_path.exists():
            self.url_list_path.write_text(
                f"# Amendment URLs for {config.CONVENTION}\n"
                f"# Generated: {datetime.now().isoformat()}\n"
                f"# Format: amendment_id | motion_slug | readable | url\n\n"
            )

    def _sanitize_filename(self, text: str) -> str:
        """Sanitize text for use in filename."""
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            text = text.replace(char, '_')
        return text[:100]

    def save_amendment(
        self,
        amendment_id: int,
        motion_slug: str,
        readable: bool,
        url: str,
        content_html: str,
        breadcrumb_html: str = "",
    ):
        """
        Save amendment data to a .txt file with metadata header.

        Args:
            amendment_id: The amendment ID
            motion_slug: The motion slug
            readable: Whether the amendment is readable (public) or not
            url: The full URL of the amendment
            content_html: The HTML content of the antragsgruen-width-main element
            breadcrumb_html: The HTML content of the breadcrumb element
        """
        timestamp = datetime.now().strftime("%Y%m%d-%H%M")
        filename = f"{self._sanitize_filename(motion_slug)}-{amendment_id}-{timestamp}.txt"
        filepath = self.output_dir / filename

        metadata = (
            f"=== METADATEN ===\n"
            f"amendmentId: {amendment_id}\n"
            f"motionSlug: {motion_slug}\n"
            f"readable: {readable}\n"
            f"url: {url}\n"
            f"timestamp: {datetime.now().isoformat()}\n"
            f"=== BREADCRUMB ===\n"
            f"{breadcrumb_html}\n"
            f"=== INHALT ===\n"
        )

        full_content = metadata + content_html

        try:
            filepath.write_text(full_content, encoding="utf-8")
            print(f"[Storage] Saved: {filename}")
        except Exception as e:
            print(f"[Storage] Error saving {filename}: {e}")

    def save_motion(
        self,
        motion_slug: str,
        url: str,
        content_html: str,
    ):
        """
        Save motion data to a .txt file with metadata header.

        Args:
            motion_slug: The motion slug
            url: The full URL of the motion
            content_html: The HTML content of the antragsgruen-width-main element
        """
        timestamp = datetime.now().strftime("%Y%m%d-%H%M")
        filename = f"motion-{self._sanitize_filename(motion_slug)}-{timestamp}.txt"
        filepath = self.output_dir / filename

        metadata = (
            f"=== METADATEN ===\n"
            f"type: motion\n"
            f"motionSlug: {motion_slug}\n"
            f"url: {url}\n"
            f"timestamp: {datetime.now().isoformat()}\n"
            f"=== INHALT ===\n"
        )

        full_content = metadata + content_html

        try:
            filepath.write_text(full_content, encoding="utf-8")
            print(f"[Storage] Saved motion: {filename}")
        except Exception as e:
            print(f"[Storage] Error saving motion {filename}: {e}")

    def add_amendment_url(
        self,
        amendment_id: int,
        motion_slug: str,
        readable: bool,
        url: str,
    ):
        """
        Add an amendment URL to the URL list.

        Args:
            amendment_id: The amendment ID
            motion_slug: The motion slug
            readable: Whether the amendment is readable
            url: The full URL
        """
        line = f"{amendment_id} | {motion_slug} | {readable} | {url}\n"
        try:
            with open(self.url_list_path, "a", encoding="utf-8") as f:
                f.write(line)
        except Exception as e:
            print(f"[Storage] Error adding URL to list: {e}")

    def save_scan_results(self, results: list):
        """
        Save scan results to a JSON file for later use.

        Args:
            results: List of dicts with keys: amendment_id, motion_slug, readable, final_url
        """
        timestamp = datetime.now().strftime("%Y%m%d-%H%M")
        filepath = self.output_dir / f"scan_results-{timestamp}.json"

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            print(f"[Storage] Saved scan results: {filepath.name}")
        except Exception as e:
            print(f"[Storage] Error saving scan results: {e}")

    def load_scan_results(self) -> Optional[list]:
        """
        Load the most recent scan results.

        Returns:
            List of scan results or None if no results found
        """
        try:
            json_files = sorted(
                self.output_dir.glob("scan_results-*.json"),
                reverse=True
            )
            if not json_files:
                return None

            with open(json_files[0], "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[Storage] Error loading scan results: {e}")
            return None

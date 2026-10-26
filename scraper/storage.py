"""
Storage module for saving scraped data to files.
"""

import json
import os
import threading
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from config import config

#: Number of records after which the structured files are written to disk.
FLUSH_INTERVAL = 20


class Storage:
    """Handles saving scraped data to files."""

    def __init__(self, output_dir: Optional[Path] = None):
        if output_dir:
            self.output_dir = output_dir
        else:
            self.output_dir = config.OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.url_list_path = self.output_dir / "amendment_urls.txt"
        self.motion_records_path = self.output_dir / "structured_motions.json"
        self.amendment_records_path = self.output_dir / "structured_amendments.json"
        self._motion_records: Dict[str, dict] = {}
        self._amendment_records: Dict[int, dict] = {}
        self._lock = threading.RLock()
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

        After a run the output directory is archived and cleared, so the
        results are taken from the newest archive if they are not available
        any more.

        Returns:
            List of scan results or None if no results found
        """
        try:
            json_files = sorted(
                self.output_dir.glob("scan_results-*.json"),
                reverse=True
            )
            if json_files:
                with open(json_files[0], "r", encoding="utf-8") as f:
                    return json.load(f)

            data = self._read_from_archive("scan_results-*.json")
            if data is not None:
                return json.loads(data.decode("utf-8"))
            return None
        except Exception as e:
            print(f"[Storage] Error loading scan results: {e}")
            return None

    # ------------------------------------------------------------------
    # Structured records (used by the Cloudflare sync exporter)
    # ------------------------------------------------------------------

    def save_motion_record(self, record: dict) -> None:
        """
        Stores the structured metadata of a motion.

        Args:
            record: Dictionary as returned by cloudflare_sync.parse_motion_page()
        """
        slug = record.get("slug")
        if not slug:
            return
        with self._lock:
            self._motion_records[slug] = record
            if len(self._motion_records) % FLUSH_INTERVAL == 0:
                self._write_json(self.motion_records_path, list(self._motion_records.values()))

    def save_amendment_record(self, record: dict) -> None:
        """
        Stores the structured metadata of an amendment.

        Args:
            record: Dictionary as returned by cloudflare_sync.parse_amendment_page()
        """
        amendment_id = record.get("id")
        if amendment_id is None:
            return
        with self._lock:
            self._amendment_records[int(amendment_id)] = record
            if len(self._amendment_records) % FLUSH_INTERVAL == 0:
                self._write_json(
                    self.amendment_records_path, list(self._amendment_records.values())
                )

    def flush_structured_records(self) -> None:
        """Writes the structured records to disk (called at the end of a phase)."""
        with self._lock:
            self._write_json(self.motion_records_path, list(self._motion_records.values()))
            self._write_json(
                self.amendment_records_path, list(self._amendment_records.values())
            )

    def get_motion_records(self) -> list:
        """Returns all collected motion records."""
        return list(self._motion_records.values())

    def get_amendment_records(self) -> list:
        """Returns all collected amendment records."""
        return list(self._amendment_records.values())

    def clear_structured_records(self) -> None:
        """Drops all structured records (called at the start of a cycle)."""
        self._motion_records.clear()
        self._amendment_records.clear()

    def load_structured_records(self) -> None:
        """Loads the structured records of the previous run from disk."""
        motions = self._read_json(self.motion_records_path)
        amendments = self._read_json(self.amendment_records_path)
        self._motion_records = {
            record["slug"]: record for record in motions if record.get("slug")
        }
        self._amendment_records = {
            int(record["id"]): record for record in amendments if record.get("id") is not None
        }

    def _read_from_archive(self, pattern: str) -> Optional[bytes]:
        """
        Reads a file from the newest archive of the output directory.

        After a run the output directory is archived and cleared. The archive
        keeps the data of the last run available for a later "--sync-only".

        Args:
            pattern: Glob pattern of the file inside the archive

        Returns:
            The content of the file or None
        """
        archive_dir = self.output_dir / "archive"
        if not archive_dir.is_dir():
            return None

        from fnmatch import fnmatch

        for zip_path in sorted(archive_dir.glob("*.zip"), reverse=True):
            try:
                with zipfile.ZipFile(zip_path) as archive:
                    for name in archive.namelist():
                        if fnmatch(name, pattern):
                            return archive.read(name)
            except Exception as e:
                print(f"[Storage] Error reading {zip_path.name}: {e}")
                continue
        return None

    def _read_json(self, path: Path) -> list:
        """Reads a list of records from a JSON file or from the archive."""
        try:
            if path.is_file():
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, list) else []

            data = self._read_from_archive(path.name)
            if data is None:
                return []
            parsed = json.loads(data.decode("utf-8"))
            return parsed if isinstance(parsed, list) else []
        except Exception as e:
            print(f"[Storage] Error reading {path.name}: {e}")
            return []

    @staticmethod
    def _write_json(path: Path, data: list) -> None:
        """Writes a list of records to a JSON file, ignoring write errors."""
        temporary = path.with_name(path.name + ".tmp")
        try:
            with open(temporary, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(temporary, path)
        except Exception as e:
            print(f"[Storage] Error writing {path.name}: {e}")
            try:
                if temporary.exists():
                    temporary.unlink()
            except Exception:
                pass

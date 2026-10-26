"""
Configuration for the Antragsgrün Amendment Scraper.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path


def _load_secret_file(path: Path) -> dict:
    """Reads a JSON file with secret configuration; missing files are ignored."""
    try:
        if not path.is_file():
            return {}
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return {}
        return {str(key): str(value) for key, value in data.items() if value is not None}
    except Exception:
        return {}


# Workspace root: <workspace>/bdk-amendment-scraper-main/scraper/config.py
PROJECT_ROOT = Path(__file__).parent.parent
# WORKSPACE_ROOT = PROJECT_ROOT.parent isn't needed, because the folder bdk-amendment-scraper-main should work on its own indepentendly from the other folders, so the secrets have to be in there too.

#: File holding the Cloudflare credentials. Never committed to git.
SECRETS_FILE = Path(
    os.environ.get("ANTRAGSGRUEN_SECRETS_FILE", str(PROJECT_ROOT / "secrets.local.json"))
)

_SECRETS = _load_secret_file(SECRETS_FILE)


@dataclass
class Config:
    """Scraper configuration."""

    # Target
    BASE_URL: str = "https://antraege.gruene.de"
    CONVENTION: str = "52bdk"
    CONVENTION_TITLE: str = "52. Bundesdelegiertenkonferenz Berlin"
    KNOWN_MOTION_SLUG: str = "vorschlag-zur-tagesordnung-38811"

    # Scanning
    START_AMENDMENT_ID: int = 25220
    MAX_CONSECUTIVE_NOT_FOUND: int = 50
    #: The scan always covers this many IDs beyond the highest known ID, even
    #: if there are gaps. The IDs are handed out globally for all
    #: consultations, so a gap of more than MAX_CONSECUTIVE_NOT_FOUND IDs can
    #: occur inside the range.
    SCAN_MARGIN_IDS: int = 300
    REQUEST_DELAY_SECONDS: float = 0.5
    RETRY_DELAY_SECONDS: float = 2.0
    MAX_RETRIES: int = 3
    REQUEST_TIMEOUT_SECONDS: int = 15

    # Parallelization
    MAX_WORKERS: int = 4
    USE_THREADING: bool = True

    # Paths
    PROJECT_ROOT: Path = PROJECT_ROOT
    OUTPUT_DIR: Path = PROJECT_ROOT / "output"
    ARCHIVE_DIR: Path = OUTPUT_DIR / "archive"

    # Periodic execution
    SCRAPE_INTERVAL_MINUTES: int = 5

    # Content extraction
    CONTENT_CLASS: str = "antragsgruen-width-main"
    ERROR_INDICATOR_TEXT: str = "Dieser Änderungsantrag kann nicht angezeigt werden"

    # Logging
    LOG_EVERY_N_IDS: int = 10

    # Google Sheets
    GOOGLE_SERVICE_ACCOUNT_FILE: str = "scraper/dienstkonto-schluessel/bdk-scraper-2-bf757e265e90.json"
    GOOGLE_SPREADSHEET_ID: str = "1JAin755g5os186VAK4g6PRIyX67JB8_aJdJWmapJEjI"
    GOOGLE_WORKSHEET_NAME: str = "Amendments"

    # Cloudflare sync
    # The URL and the token can be provided via environment variables or via the
    # workspace secret file (see SECRETS_FILE above).
    CLOUDFLARE_SYNC_URL: str = (
        os.environ.get("CLOUDFLARE_SYNC_URL") or _SECRETS.get("WORKER_URL", "")
    )
    CLOUDFLARE_SYNC_TOKEN: str = (
        os.environ.get("CLOUDFLARE_SYNC_TOKEN") or _SECRETS.get("SYNC_SECRET", "")
    )
    CLOUDFLARE_SYNC_ENABLED: bool = bool(
        os.environ.get("CLOUDFLARE_SYNC_ENABLED", "1") not in ("0", "false", "False")
    )
    CLOUDFLARE_SYNC_TIMEOUT: float = 30.0

    def __post_init__(self):
        """Ensure output and archive directories exist."""
        self.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def cloudflare_configured(self) -> bool:
        """True when both the worker URL and the sync token are available."""
        return bool(self.CLOUDFLARE_SYNC_URL and self.CLOUDFLARE_SYNC_TOKEN)

    def describe_sources(self) -> dict:
        """
        Describes where the Cloudflare configuration comes from.

        The result is logged at the start of every run so that a missing
        configuration can be traced back to its cause.
        """
        url_from_env = bool(os.environ.get("CLOUDFLARE_SYNC_URL"))
        token_from_env = bool(os.environ.get("CLOUDFLARE_SYNC_TOKEN"))
        return {
            "secrets_file": str(SECRETS_FILE),
            "secrets_file_exists": SECRETS_FILE.is_file(),
            "url_from_env": url_from_env,
            "token_from_env": token_from_env,
            "url_from_file": bool(_SECRETS.get("WORKER_URL")) and not url_from_env,
            "token_from_file": bool(_SECRETS.get("SYNC_SECRET")) and not token_from_env,
        }


# Global config instance
config = Config()

"""
Configuration for the Antragsgrün Amendment Scraper.
"""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    """Scraper configuration."""

    # Target
    BASE_URL: str = "https://antraege.gruene.de"
    CONVENTION: str = "52bdk"
    KNOWN_MOTION_SLUG: str = "vorschlag-zur-tagesordnung-38811"

    # Scanning
    START_AMENDMENT_ID: int = 25220
    MAX_CONSECUTIVE_NOT_FOUND: int = 50
    REQUEST_DELAY_SECONDS: float = 0.5
    RETRY_DELAY_SECONDS: float = 2.0
    MAX_RETRIES: int = 3
    REQUEST_TIMEOUT_SECONDS: int = 15

    # Parallelization
    MAX_WORKERS: int = 4
    USE_THREADING: bool = True

    # Paths
    PROJECT_ROOT: Path = Path(__file__).parent.parent
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

    def __post_init__(self):
        """Ensure output and archive directories exist."""
        self.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)


# Global config instance
config = Config()
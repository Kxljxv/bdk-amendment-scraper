"""
Health Check Endpoint for Uptime Kuma Monitoring.

Run alongside the scraper service to provide HTTP health checks.
Exposes /health endpoint returning JSON with service status.
"""

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

# Add project root to path
import sys
sys.path.insert(0, str(Path(__file__).parent))

from config import config

HEALTH_PORT = int(os.environ.get("HEALTH_PORT", "8080"))
LAST_SUCCESS_FILE = config.OUTPUT_DIR / "last_success.txt"
SCRAPER_LOG = config.OUTPUT_DIR / "scraper.log"


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_health()
        elif self.path == "/metrics":
            self.send_metrics()
        else:
            self.send_response(404)
            self.end_headers()

    def send_health(self):
        """Return health status as JSON."""
        status = self.check_health()
        self.send_response(200 if status["healthy"] else 503)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(status, indent=2).encode())

    def send_metrics(self):
        """Return Prometheus-style metrics."""
        status = self.check_health()
        metrics = [
            f'bdk_scraper_healthy {1 if status["healthy"] else 0}',
            f'bdk_scraper_last_run_seconds_ago {status.get("last_run_seconds_ago", -1)}',
            f'bdk_scraper_last_archive_age_hours {status.get("last_archive_age_hours", -1)}',
            f'bdk_scraper_log_size_bytes {status.get("log_size_bytes", 0)}',
        ]
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write("\n".join(metrics).encode())

    def check_health(self):
        """Check scraper health."""
        now = time.time()
        result = {
            "healthy": True,
            "timestamp": datetime.now().isoformat(),
            "service": "bdk-amendment-scraper",
            "checks": {}
        }

        # Check 1: Last successful run (should be within last 30 minutes for 10-min interval)
        if LAST_SUCCESS_FILE.exists():
            try:
                last_success = float(LAST_SUCCESS_FILE.read_text().strip())
                seconds_ago = now - last_success
                result["last_run_seconds_ago"] = round(seconds_ago)
                result["checks"]["last_run"] = {
                    "status": "ok" if seconds_ago < 1800 else "stale",
                    "seconds_ago": round(seconds_ago)
                }
                if seconds_ago > 1800:
                    result["healthy"] = False
            except Exception:
                result["checks"]["last_run"] = {"status": "error", "message": "Invalid timestamp file"}
                result["healthy"] = False
        else:
            result["checks"]["last_run"] = {"status": "missing", "message": "No success timestamp found"}
            result["healthy"] = False

        # Check 2: Archive directory has recent archives
        archive_dir = config.OUTPUT_DIR / "archive"
        if archive_dir.exists():
            archives = list(archive_dir.glob("*.zip"))
            if archives:
                latest = max(archives, key=lambda f: f.stat().st_mtime)
                age_hours = (now - latest.stat().st_mtime) / 3600
                result["last_archive_age_hours"] = round(age_hours, 1)
                result["checks"]["archive"] = {
                    "status": "ok" if age_hours < 2 else "stale",
                    "count": len(archives),
                    "latest_age_hours": round(age_hours, 1)
                }
                if age_hours > 2:
                    result["healthy"] = False
            else:
                result["checks"]["archive"] = {"status": "empty", "message": "No archives found"}
                result["healthy"] = False
        else:
            result["checks"]["archive"] = {"status": "missing", "message": "Archive directory missing"}
            result["healthy"] = False

        # Check 3: Log file exists and is recent
        if SCRAPER_LOG.exists():
            log_stat = SCRAPER_LOG.stat()
            result["log_size_bytes"] = log_stat.st_size
            log_age_hours = (now - log_stat.st_mtime) / 3600
            result["checks"]["log"] = {
                "status": "ok" if log_age_hours < 1 else "stale",
                "size_bytes": log_stat.st_size,
                "age_hours": round(log_age_hours, 1)
            }
        else:
            result["checks"]["log"] = {"status": "missing", "message": "Log file missing"}

        return result

    def log_message(self, format, *args):
        # Suppress default logging
        pass


def run_server():
    """Run the health check HTTP server."""
    server = HTTPServer(("0.0.0.0", HEALTH_PORT), HealthHandler)
    print(f"[HealthEndpoint] Starting on port {HEALTH_PORT}")
    server.serve_forever()


def mark_success():
    """Mark a successful scraper run. Call from main.py after each cycle."""
    LAST_SUCCESS_FILE.write_text(str(time.time()))


if __name__ == "__main__":
    import time
    run_server()
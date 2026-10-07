"""
Antragsgruen Amendment Scraper for 52bdk - Periodic Mode

This script runs all scrape phases in a loop, archives results
after each successful run, and logs everything to a file.
"""

import argparse
import logging
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

from config import config
from http_client import get_client, close_client
from storage import Storage
from amendment_scanner import AmendmentScanner
from content_scraper import ContentScraper
from motion_scraper import MotionScraper
from google_sheets_exporter import GoogleSheetsExporter
from health_endpoint import mark_success


def setup_logging():
    """Setup logging to file and console."""
    log_file = config.OUTPUT_DIR / "scraper.log"
    logger = logging.getLogger("scraper")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(formatter)
        logger.addHandler(fh)
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(formatter)
        logger.addHandler(ch)
    return logger


def print_banner(logger):
    """Print the scraper banner."""
    banner = f"""
    ================================================================
       Antragsgruen Amendment Scraper fuer {config.CONVENTION}
       Started: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
       Interval: {config.SCRAPE_INTERVAL_MINUTES} minutes
    ================================================================
    """
    logger.info(banner)


def zip_and_clear_output(output_dir: Path, archive_dir: Path, logger):
    """Zip all files in output_dir and move to archive_dir, then clear output_dir."""
    try:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M")
        zip_name = f"{timestamp}.zip"
        zip_path = archive_dir / zip_name

        archive_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in output_dir.rglob("*"):
                if file_path.is_file() and archive_dir not in file_path.parents:
                    if file_path.name == "scraper.log":
                        continue
                    arcname = file_path.relative_to(output_dir)
                    zf.write(file_path, arcname)

        logger.info(f"[Archive] Created archive: {zip_path}")

        # Clear output directory (except archive and log)
        for file_path in output_dir.rglob("*"):
            if archive_dir in file_path.parents:
                continue
            if file_path.is_file():
                if file_path.name == "scraper.log":
                    continue
                file_path.unlink()
            elif file_path.is_dir():
                try:
                    file_path.rmdir()
                except OSError:
                    pass

        logger.info("[Archive] Output directory cleared")
        return True
    except Exception as e:
        logger.error(f"[Archive] Failed to archive output: {e}")
        return False


def run_phase_1(storage: Storage, logger):
    """Run Phase 1: Scan amendment IDs."""
    logger.info("=" * 70)
    logger.info("PHASE 1: Scanning Amendment IDs")
    logger.info("=" * 70)

    scanner = AmendmentScanner(storage)
    results = scanner.scan_range(config.START_AMENDMENT_ID, config.MAX_CONSECUTIVE_NOT_FOUND)

    if results:
        storage.save_scan_results(results)
        logger.info(f"[Phase 1] Found {len(results)} amendments")
    else:
        logger.info("[Phase 1] No amendments found")

    return results


def run_phase_2(storage: Storage, scan_results: list, logger):
    """Run Phase 2: Scrape content for amendments."""
    logger.info("=" * 70)
    logger.info("PHASE 2: Scraping Amendment Content")
    logger.info("=" * 70)

    if not scan_results:
        logger.info("[Phase 2] No scan results from this run. Loading from file...")
        scan_results = storage.load_scan_results()
        if not scan_results:
            logger.info("[Phase 2] No scan results found.")
            return

    scraper = ContentScraper(storage)
    scraper.scrape_amendments_batch(scan_results)


def run_phase_3(storage: Storage, logger):
    """Run Phase 3: Scrape motions from start page."""
    logger.info("=" * 70)
    logger.info("PHASE 3: Scraping Motions")
    logger.info("=" * 70)

    motion_scraper = MotionScraper(storage)
    motion_scraper.scrape_all_motions()


def run_scrape_cycle(storage: Storage, logger):
    """Run one complete scrape cycle."""
    scan_results = []

    try:
        scan_results = run_phase_1(storage, logger)
    except Exception as e:
        logger.error(f"[Phase 1] Error: {e}", exc_info=True)

    try:
        run_phase_2(storage, scan_results, logger)
    except Exception as e:
        logger.error(f"[Phase 2] Error: {e}", exc_info=True)

    try:
        run_phase_3(storage, logger)
    except Exception as e:
        logger.error(f"[Phase 3] Error: {e}", exc_info=True)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Antragsgruen Amendment Scraper for 52bdk - Periodic Mode"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=config.SCRAPE_INTERVAL_MINUTES,
        help=f"Scrape interval in minutes (default: {config.SCRAPE_INTERVAL_MINUTES})",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=config.MAX_WORKERS,
        help=f"Number of worker threads (default: {config.MAX_WORKERS})",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=0,
        help="Number of runs before exiting (default: 0 = infinite)",
    )

    args = parser.parse_args()

    config.MAX_WORKERS = args.workers
    interval_seconds = args.interval * 60

    logger = setup_logging()
    print_banner(logger)

    logger.info(f"[Main] Interval: {args.interval} minutes")
    logger.info(f"[Main] Using {config.MAX_WORKERS} worker threads")
    logger.info("[Main] Using direct HTTP requests (no Tor)")

    http_client = get_client()
    storage = Storage()

    try:
        run_count = 0
        max_runs = args.runs

        while True:
            run_count += 1
            logger.info("=" * 70)
            logger.info(f"STARTING RUN #{run_count}")
            logger.info("=" * 70)

            try:
                run_scrape_cycle(storage, logger)
            except Exception as e:
                logger.error(f"[Run #{run_count}] Fatal error in cycle: {e}", exc_info=True)

            # Export to Google Sheets
            try:
                exporter = GoogleSheetsExporter()
                exporter.export_to_sheet(config.OUTPUT_DIR)
            except Exception as e:
                logger.error(f"[Run #{run_count}] Failed to export to Google Sheets: {e}", exc_info=True)

            # Archive results
            try:
                archive_dir = config.OUTPUT_DIR / "archive"
                zip_and_clear_output(config.OUTPUT_DIR, archive_dir, logger)
            except Exception as e:
                logger.error(f"[Run #{run_count}] Failed to archive: {e}", exc_info=True)

            # Mark successful run for health endpoint
            try:
                mark_success()
            except Exception as e:
                logger.warning(f"[Run #{run_count}] Failed to mark success: {e}")

            logger.info(f"[Run #{run_count}] Complete. Waiting {args.interval} minutes...")

            if max_runs > 0 and run_count >= max_runs:
                logger.info(f"[Main] Completed {run_count} runs, exiting.")
                break

            try:
                time.sleep(interval_seconds)
            except KeyboardInterrupt:
                raise

    except KeyboardInterrupt:
        logger.info("\n[Main] Stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"[Main] Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        close_client()


if __name__ == "__main__":
    main()
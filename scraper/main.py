"""
Antragsgruen Amendment Scraper for 52bdk - Periodic Mode

This script runs all scrape phases in a loop, archives results
after each successful run, and logs everything to a file.
"""

import argparse
import logging
import shutil
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
from cloudflare_sync import CloudflareSyncClient, build_sync_payload


def setup_logging():
    """Setup logging to file and console.

    The handlers are attached to the root logger, so that the messages of the
    modules (scanner, content scraper, motion scraper, ...) end up in the log
    file as well and not only on the console.
    """
    log_file = config.OUTPUT_DIR / "scraper.log"
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    root = logging.getLogger()
    if not root.handlers:
        root.setLevel(logging.INFO)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(formatter)
        root.addHandler(fh)
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(formatter)
        root.addHandler(ch)

    # The HTTP client logs every request on its own - that would drown the
    # log file.
    for noisy in ("httpx", "httpcore", "hpack"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return logging.getLogger("scraper")


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

    # The results of the previous run are needed to continue the scan where it
    # stopped and to keep the result complete.
    previous_results = storage.load_scan_results() or []

    scanner = AmendmentScanner(storage)
    results = scanner.scan_range(
        config.START_AMENDMENT_ID,
        config.MAX_CONSECUTIVE_NOT_FOUND,
        previous_results=previous_results,
    )

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
    storage.flush_structured_records()


def run_phase_3(storage: Storage, scan_results: list, logger):
    """Run Phase 3: Scrape motions from start page.

    Returns:
        Tuple of (scraped motions, expected motions)
    """
    logger.info("=" * 70)
    logger.info("PHASE 3: Scraping Motions")
    logger.info("=" * 70)

    # Motions that are not linked on the start page (e.g. the parent of a
    # statute that is not publicly listed) are derived from the scan results.
    extra_slugs = []
    for result in scan_results or []:
        slug = result.get("motion_slug")
        if slug and slug not in extra_slugs:
            extra_slugs.append(slug)

    motion_scraper = MotionScraper(storage)
    expected = motion_scraper.count_motion_pages(extra_slugs=extra_slugs)
    scraped = motion_scraper.scrape_all_motions(extra_slugs=extra_slugs)
    storage.flush_structured_records()

    return scraped, expected


def run_phase_4(storage: Storage, logger, scraped_motions=0, expected_motions=0):
    """Run Phase 4: Push the collected data to the Cloudflare Worker."""
    logger.info("=" * 70)
    logger.info("PHASE 4: Syncing to Cloudflare Worker")
    logger.info("=" * 70)

    if not config.CLOUDFLARE_SYNC_ENABLED:
        logger.info("[Phase 4] Cloudflare sync is disabled")
        return False

    if not config.cloudflare_configured:
        sources = config.describe_sources()
        logger.warning(
            "[Phase 4] Cloudflare sync is not configured - the data stays local. "
            f"Searched the secrets file: {sources['secrets_file']} "
            f"(exists: {sources['secrets_file_exists']}). "
            "Set the environment variables CLOUDFLARE_SYNC_URL and "
            "CLOUDFLARE_SYNC_TOKEN or provide WORKER_URL and SYNC_SECRET in that file."
        )
        return False

    sources = config.describe_sources()
    logger.info(
        f"[Phase 4] Cloudflare sync configured "
        f"(url from {'environment' if sources['url_from_env'] else 'secrets file'}, "
        f"token from {'environment' if sources['token_from_env'] else 'secrets file'})"
    )

    motions = storage.get_motion_records()
    amendments = storage.get_amendment_records()

    if not motions:
        logger.warning("[Phase 4] No motion records collected - nothing to sync")
        return False

    # If not every motion could be scraped, the data is merged into the
    # existing data on the server instead of replacing it, so that nothing is
    # lost because of a single failed request.
    complete = scraped_motions >= expected_motions if expected_motions else True
    if not complete:
        logger.warning(
            f"[Phase 4] Only {scraped_motions}/{expected_motions} motions were scraped - "
            "the data is merged into the existing data on the server"
        )

    payload = build_sync_payload(
        convention=config.CONVENTION,
        base_url=config.BASE_URL,
        title=config.CONVENTION_TITLE,
        motions=motions,
        amendments=amendments,
        complete=complete,
    )

    hidden = [entry for entry in payload["amendments"] if not entry["visible"]]
    logger.info(
        f"[Phase 4] Prepared {len(payload['motions'])} motions, "
        f"{len(payload['amendments'])} amendments "
        f"({len(hidden)} not publicly listed)"
    )

    try:
        with CloudflareSyncClient(
            config.CLOUDFLARE_SYNC_URL,
            config.CLOUDFLARE_SYNC_TOKEN,
            timeout=config.CLOUDFLARE_SYNC_TIMEOUT,
        ) as client:
            result = client.push(payload)
        logger.info(f"[Phase 4] Sync successful: {result}")
        return True
    except Exception as e:
        logger.error(f"[Phase 4] Sync failed: {e}")
        return False


def run_scrape_cycle(storage: Storage, logger):
    """Run one complete scrape cycle."""
    # Clean output directory before starting new cycle (keep log and archive)
    archive_dir = config.OUTPUT_DIR / "archive"
    for p in config.OUTPUT_DIR.iterdir():
        if p.resolve() == archive_dir.resolve():
            continue
        if p.name == "scraper.log":
            continue
        if p.is_file():
            try:
                p.unlink(missing_ok=True)
            except Exception as e:
                logger.warning(f"[Cleanup] Failed to delete file {p}: {e}")
        elif p.is_dir():
            try:
                shutil.rmtree(p, ignore_errors=True)
            except Exception as e:
                logger.warning(f"[Cleanup] Failed to delete directory {p}: {e}")

    scan_results = []
    storage.clear_structured_records()
    scraped_motions = 0
    expected_motions = 0

    try:
        scan_results = run_phase_1(storage, logger)
    except Exception as e:
        logger.error(f"[Phase 1] Error: {e}", exc_info=True)

    try:
        run_phase_2(storage, scan_results, logger)
    except Exception as e:
        logger.error(f"[Phase 2] Error: {e}", exc_info=True)

    try:
        scraped_motions, expected_motions = run_phase_3(storage, scan_results, logger)
    except Exception as e:
        logger.error(f"[Phase 3] Error: {e}", exc_info=True)

    try:
        run_phase_4(storage, logger, scraped_motions, expected_motions)
    except Exception as e:
        logger.error(f"[Phase 4] Error: {e}", exc_info=True)


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
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Disable the Cloudflare Worker sync (Phase 4)",
    )
    parser.add_argument(
        "--worker-url",
        default=None,
        help="Override the Cloudflare Worker URL (default: from env or secrets file)",
    )
    parser.add_argument(
        "--sync-token",
        default=None,
        help="Override the Cloudflare sync bearer token",
    )
    parser.add_argument(
        "--sync-only",
        action="store_true",
        help="Only run the Cloudflare sync using the data of the last run",
    )

    args = parser.parse_args()

    config.MAX_WORKERS = args.workers
    interval_seconds = args.interval * 60

    if args.worker_url:
        config.CLOUDFLARE_SYNC_URL = args.worker_url
    if args.sync_token:
        config.CLOUDFLARE_SYNC_TOKEN = args.sync_token
    if args.no_sync:
        config.CLOUDFLARE_SYNC_ENABLED = False

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

            if args.sync_only:
                storage.load_structured_records()
                try:
                    run_phase_4(storage, logger)
                except Exception as e:
                    logger.error(f"[Run #{run_count}] Sync error: {e}", exc_info=True)
                logger.info(f"[Run #{run_count}] Completed (sync only). Exiting.")
                break

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
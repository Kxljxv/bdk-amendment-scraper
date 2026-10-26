"""
Cloudflare sync exporter for the Antragsgrün amendment scraper.

This module contains two responsibilities:

1. Parsing of Antragsgrün pages into structured records (motions, amendments).
2. Shipping those records to the Cloudflare Worker that backs the browser
   extension.

The parsers deliberately mirror the markup that Antragsgrün renders, so that
the extension can re-create list entries that are indistinguishable from the
natively rendered ones:

* Motion detail page (``section.amendments > ul.amendments``)::

      <li><a class="amendment25529" href="...">O-05-069</a> (Initiator, Status)</li>

* Motion list page (``ul.amendments``)::

      <li class="amendmentRow25529 amendment">
        <span class="privateCommentHolder"></span>
        <a href="..." class="amendmentTitle amendment25529">O-05-069</a>
        <p class="date"><span class="sr-only">Angelegt am</span> ...</p>
        <span class="info">Initiator</span>
        <span class='clearfix'></span>
      </li>
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import httpx
from bs4 import BeautifulSoup
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

#: Label Antragsgrün uses for amendments without a title prefix.
AMENDMENT_LABEL = "Änderungsantrag"

#: Label of the "created on" column in the motion list.
CREATED_ON_LABEL = "Angelegt am"

#: Marker of the Antragsgrün error page for amendments that are not readable.
ERROR_INDICATOR = "Dieser Änderungsantrag kann nicht angezeigt werden"

#: Timezone of the Antragsgrün server, used for the relative date labels.
SERVER_TIMEZONE = "Europe/Berlin"

_GERMAN_MONTHS = [
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
]

_AMENDMENT_ID_CLASS_RE = re.compile(r"^amendment(\d+)$")
_MOTION_ROW_ID_CLASS_RE = re.compile(r"^motionRow(\d+)$")
_PREFIX_RE = re.compile(r"^([^\s:]{1,40}):\s*(.+)$", re.DOTALL)
_LINE_NUMBER_RE = re.compile(r"\d+")


def parse_ajax_diff(html: str) -> List[Dict[str, Any]]:
    """
    Parses the "ajax-diff" response of an amendment.

    Antragsgrün renders the diff of an amendment in blocks, one per affected
    paragraph. Every block starts with a summary that names the affected lines
    of the motion ("Von Zeile 185 bis 188:").

    The returned blocks contain the line range of the motion and the diff
    itself, so that the extension can place a marker next to the paragraph
    that the amendment changes.

    Args:
        html: The response of the "ajax-diff" endpoint

    Returns:
        List of blocks: {"from": int, "to": int, "marker": int, "html": str}
    """
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    blocks: List[Dict[str, Any]] = []

    for header in soup.find_all(class_="lineSummary"):
        summary = _node_text(header)
        numbers = [int(value) for value in _LINE_NUMBER_RE.findall(summary)]
        if not numbers:
            continue

        if summary.startswith("Vor Zeile"):
            # "Vor Zeile X einfügen": the number already is the 1-based line
            # number of the affected line.
            first = numbers[0]
            last = numbers[0]
            marker = numbers[0]
        elif summary.startswith("Von Zeile") and len(numbers) >= 2:
            first = numbers[0]
            last = numbers[1]
            marker = first + 1
        else:
            # "In Zeile X" / "Nach Zeile X"
            first = numbers[0]
            last = numbers[0]
            marker = first + 1

        content = header.find_next_sibling("div")
        blocks.append(
            {
                "from": first,
                "to": last,
                "marker": marker,
                "summary": summary,
                "html": str(content) if content is not None else "",
            }
        )

    return blocks


def fetch_ajax_diff(client, url: str) -> List[Dict[str, Any]]:
    """
    Fetches and parses the "ajax-diff" endpoint of an amendment.

    The endpoint is publicly accessible and also answers for amendments that
    are not listed (e.g. drafts), which is exactly what is needed here.

    Args:
        client: An httpx client
        url: The URL of the amendment

    Returns:
        List of diff blocks, empty if the amendment has no visible diff
    """
    if not url:
        return []
    try:
        response = client.get(f"{url.rstrip('/')}/ajax-diff")
    except Exception as error:
        logger.warning("[CloudflareSync] Could not load the diff of %s: %s", url, error)
        return []

    if response.status_code >= 400:
        return []
    return parse_ajax_diff(response.text)


def _server_now() -> datetime:
    """Returns the current time in the Antragsgrün server timezone."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(SERVER_TIMEZONE))
    except Exception:  # pragma: no cover - zoneinfo without tzdata
        return datetime.now(timezone.utc)


def _clean_text(value: Optional[str]) -> str:
    """Collapses whitespace and strips the given string."""
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _node_text(node: Any) -> str:
    """Returns the collapsed text content of a BeautifulSoup node."""
    if node is None:
        return ""
    return _clean_text(node.get_text(" ", strip=True))


def _table_rows(table: Any) -> List[tuple]:
    """Returns the ``(label, cell)`` pairs of a ``motionDataTable``."""
    rows: List[tuple] = []
    if table is None:
        return rows
    for tr in table.find_all("tr"):
        th = tr.find("th")
        td = tr.find("td")
        if th is None or td is None:
            continue
        rows.append((_node_text(th).rstrip(":"), td))
    return rows


def _find_data_table(soup: BeautifulSoup) -> Any:
    """Returns the ``motionDataTable`` element of a motion/amendment page."""
    return soup.find("table", class_="motionDataTable")


def _row_value(rows: Sequence[tuple], *labels: str) -> str:
    """Returns the text of the first row matching one of the given labels."""
    wanted = {label.lower() for label in labels}
    for label, cell in rows:
        if label.lower() in wanted:
            return _node_text(cell)
    return ""


def _split_prefixed_title(title: str) -> tuple:
    """Splits ``"O-05: Some title"`` into ``("O-05", "Some title")``."""
    title = _clean_text(title)
    if not title:
        return "", ""
    match = _PREFIX_RE.match(title)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return "", title


def _extract_women_quota(cell: Any) -> str:
    """Returns the ``womenQuota`` span of an initiator cell, if present."""
    if cell is None:
        return ""
    quota = cell.find("span", class_="womenQuota")
    if quota is None:
        return ""
    return str(quota)


def _extract_initiator_text(cell: Any) -> str:
    """
    Returns the initiator string of an initiator cell.

    Antragsgrün renders the initiators followed by a ``moreSupporters`` block
    that lists the additional supporters. Only the initiators belong to the
    ``getInitiatorsStr()`` value that the native list markup uses.
    """
    if cell is None:
        return ""
    clone = BeautifulSoup(str(cell), "html.parser")
    for more in clone.find_all("div", class_="moreSupporters"):
        more.decompose()
    for quota in clone.find_all("span", class_="womenQuota"):
        quota.decompose()
    for button in clone.find_all("button"):
        button.decompose()
    for details in clone.find_all("div", class_="contactDetails"):
        details.decompose()
    return _node_text(clone)


def _relative_date_to_iso(display: str) -> str:
    """
    Converts the German date labels used by Antragsgrün into an ISO date.

    ``"Gestern, 07:05"`` becomes the ISO date of the previous day, ``"Heute,
    12:00"`` the current one and ``"12.10.2026, 07:11"`` becomes
    ``"2026-10-12"``.
    """
    value = _clean_text(display)
    if not value:
        return ""
    now = _server_now()

    lowered = value.lower()
    if lowered.startswith("heute"):
        return now.date().isoformat()
    if lowered.startswith("gestern"):
        return (now - timedelta(days=1)).date().isoformat()

    match = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})", value)
    if match:
        day, month, year = (int(part) for part in match.groups())
        try:
            return datetime(year, month, day).date().isoformat()
        except ValueError:
            return ""
    return ""


def _extract_visible_amendments(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    """
    Extracts the amendments that are officially listed on a motion page.

    Mirrors ``StdHooks::getMotionFormattedAmendmentList()``.
    """
    section = soup.find("section", class_="amendments")
    if section is None:
        return []

    amendments: List[Dict[str, Any]] = []
    for li in section.find_all("ul", class_="amendments"):
        for item in li.find_all("li", recursive=False):
            link = item.find("a")
            if link is None:
                continue
            amendment_id = None
            for class_name in link.get("class") or []:
                match = _AMENDMENT_ID_CLASS_RE.match(class_name)
                if match:
                    amendment_id = int(match.group(1))
                    break
            if amendment_id is None:
                continue

            tail = _node_text(item)
            title = _clean_text(link.get_text())
            if tail.startswith(title):
                tail = tail[len(title):].strip()
            if tail.startswith("(") and tail.endswith(")"):
                tail = tail[1:-1]
            initiator, _, status = tail.rpartition(", ")
            if not status:
                initiator, status = "", tail

            amendments.append(
                {
                    "id": amendment_id,
                    "title": title,
                    "initiator": _clean_text(initiator),
                    "status": _clean_text(status),
                    "url": link.get("href", ""),
                }
            )
    return amendments


def parse_motion_page(html: str, slug: str = "", url: str = "") -> Dict[str, Any]:
    """
    Parses a motion detail page.

    Returns the motion metadata and the list of officially visible amendments.
    """
    soup = BeautifulSoup(html, "html.parser")

    header = soup.find("div", class_="primaryHeader")
    h1 = header.find("h1") if header else soup.find("h1")
    heading = _node_text(h1)
    title_prefix, title = _split_prefixed_title(heading)

    rows = _table_rows(_find_data_table(soup))
    initiator = _row_value(rows, "Antragsteller*in", "Antragsteller/in", "Antragsteller")

    motion_id = None
    for element in soup.find_all(class_=True):
        for class_name in element.get("class") or []:
            match = _MOTION_ROW_ID_CLASS_RE.match(class_name)
            if match:
                motion_id = int(match.group(1))
                break
        if motion_id is not None:
            break

    visible = _extract_visible_amendments(soup)

    return {
        "slug": slug,
        "url": url,
        "title": title,
        "title_prefix": title_prefix,
        "initiator": initiator,
        "motion_id": motion_id,
        "visible_amendments": visible,
    }


def parse_amendment_page(
    html: str,
    amendment_id: int,
    motion_slug: str = "",
    url: str = "",
) -> Dict[str, Any]:
    """
    Parses an amendment detail page.

    Returns the structured metadata of a single amendment. When the page is not
    readable (Antragsgrün answers with a 404 error page), the returned record
    only contains the id and the motion it belongs to.
    """
    soup = BeautifulSoup(html, "html.parser")

    if ERROR_INDICATOR in html or "<title>Error" in html:
        # The amendment exists but Antragsgrün does not render it for the
        # public. Only the id and the motion it belongs to are known.
        return {
            "id": amendment_id,
            "motion_slug": motion_slug,
            "url": url,
            "title": "",
            "title_prefix": "",
            "list_title": AMENDMENT_LABEL,
            "detail_title": str(amendment_id),
            "initiator": "",
            "women_quota": "",
            "status": "",
            "created_date": "",
            "created_display": "",
            "readable": False,
            "content_html": "",
        }

    header = soup.find("div", class_="primaryHeader")
    h1 = header.find("h1") if header else soup.find("h1")
    title = _node_text(h1)

    rows = _table_rows(_find_data_table(soup))

    resolved_motion_slug = motion_slug
    title_prefix = ""

    # The breadcrumb always links back to the motion the amendment belongs to.
    breadcrumb = soup.find("ol", class_="breadcrumb")
    if breadcrumb is not None:
        for link in breadcrumb.find_all("a", href=True):
            href = link.get("href", "")
            match = re.match(r"^/[^/]+/([^/]+)/?$", href)
            if match:
                resolved_motion_slug = match.group(1)
                break

        items = breadcrumb.find_all("li")
        if items:
            candidate = _node_text(items[-1])
            if candidate and candidate != AMENDMENT_LABEL:
                title_prefix = candidate

    if not title_prefix:
        for label, cell in rows:
            if label.lower() == "antrag":
                motion_link = cell.find("a", href=True)
                if motion_link is not None:
                    href = motion_link.get("href", "")
                    match = re.match(r"^/[^/]+/([^/]+)/?$", href)
                    if match:
                        resolved_motion_slug = match.group(1)
                break

    initiator_cell = None
    status_cell = None
    created_display = ""
    for label, cell in rows:
        lowered = label.lower()
        if lowered in ("antragsteller*in", "antragsteller/in", "antragsteller"):
            initiator_cell = cell
        elif lowered == "status":
            status_cell = cell
        elif lowered in ("angelegt", "eingereicht"):
            created_display = _node_text(cell)

    initiator = _extract_initiator_text(initiator_cell)
    women_quota = _extract_women_quota(initiator_cell)

    status = ""
    if status_cell is not None:
        clone = BeautifulSoup(str(status_cell), "html.parser")
        for small in clone.find_all("small"):
            small.decompose()
        status = _node_text(clone)

    if title_prefix:
        list_title = title_prefix
        detail_title = title_prefix
    else:
        list_title = AMENDMENT_LABEL
        detail_title = str(amendment_id)

    content = soup.find(class_="antragsgruen-width-main")
    content_html = str(content) if content else ""

    return {
        "id": amendment_id,
        "motion_slug": resolved_motion_slug,
        "url": url,
        "title": title,
        "title_prefix": title_prefix,
        "list_title": list_title,
        "detail_title": detail_title,
        "initiator": initiator,
        "women_quota": women_quota,
        "status": status,
        "created_date": _relative_date_to_iso(created_display),
        "created_display": created_display,
        "readable": bool(title),
        "content_html": content_html,
    }


def _first_diff_value(blocks: Any, key: str) -> Optional[int]:
    """Returns the value of the first diff block, if there is one."""
    if not blocks:
        return None
    try:
        value = blocks[0].get(key)
    except (AttributeError, IndexError, TypeError):
        return None
    return value if isinstance(value, int) else None


def build_sync_payload(
    convention: str,
    base_url: str,
    motions: Iterable[Dict[str, Any]],
    amendments: Iterable[Dict[str, Any]],
    title: str = "",
    complete: bool = True,
    scraped_at: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Builds the JSON body that is sent to ``POST /api/sync``.

    ``motions`` and ``amendments`` are the structured records collected during
    the run. Amendments that are not part of the officially visible list of
    their motion are flagged as hidden.
    """
    visible_ids: Dict[str, set] = {}
    motion_payload: List[Dict[str, Any]] = []

    for motion in motions:
        slug = motion.get("slug", "")
        ids = {int(entry["id"]) for entry in motion.get("visible_amendments", []) if "id" in entry}
        visible_ids[slug] = ids
        motion_payload.append(
            {
                "slug": slug,
                "url": motion.get("url", ""),
                "title": motion.get("title", ""),
                "title_prefix": motion.get("title_prefix", ""),
                "initiator": motion.get("initiator", ""),
                "motion_id": motion.get("motion_id"),
                "visible_amendment_ids": sorted(ids),
            }
        )

    amendment_payload: List[Dict[str, Any]] = []
    skipped: List[int] = []
    for amendment in amendments:
        slug = amendment.get("motion_slug", "")
        if slug not in visible_ids:
            # The motion was not scraped, so we cannot tell whether the
            # amendment is listed publicly. Skipping it is the safe choice:
            # injecting an amendment that is already visible would duplicate it.
            skipped.append(int(amendment.get("id", -1)))
            continue
        is_visible = int(amendment.get("id", -1)) in visible_ids.get(slug, set())
        amendment_payload.append(
            {
                "id": amendment.get("id"),
                "motion_slug": slug,
                "url": amendment.get("url", ""),
                "title": amendment.get("title", ""),
                "title_prefix": amendment.get("title_prefix", ""),
                "list_title": amendment.get("list_title", ""),
                "detail_title": amendment.get("detail_title", ""),
                "initiator": amendment.get("initiator", ""),
                "women_quota": amendment.get("women_quota", ""),
                "status": amendment.get("status", ""),
                "created_date": amendment.get("created_date", ""),
                "created_display": amendment.get("created_display", ""),
                "visible": is_visible,
                "readable": bool(amendment.get("readable", False)),
                "diff_from": _first_diff_value(amendment.get("diff_blocks"), "marker"),
                "diff_to": _first_diff_value(amendment.get("diff_blocks"), "to"),
                "diff_html": json.dumps(
                    amendment.get("diff_blocks") or [], ensure_ascii=False
                ),
            }
        )

    if skipped:
        logger.warning(
            "[CloudflareSync] Skipped %d amendments of unscraped motions: %s",
            len(skipped),
            ", ".join(str(entry) for entry in skipped[:20]),
        )

    return {
        "convention": convention,
        "base_url": base_url,
        "title": title,
        "scraped_at": scraped_at or datetime.now(timezone.utc).isoformat(),
        "complete": complete,
        "motions": motion_payload,
        "amendments": amendment_payload,
    }


class CloudflareSyncClient:
    """Sends sync payloads to the Cloudflare Worker."""

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float = 30.0,
        max_attempts: int = 4,
    ) -> None:
        if not base_url:
            raise ValueError("Cloudflare sync URL is not configured")
        if not token:
            raise ValueError("Cloudflare sync token is not configured")

        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.max_attempts = max_attempts
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "bdk-amendment-scraper/1.0",
            },
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "CloudflareSyncClient":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def _post(self, path: str, payload: Dict[str, Any]) -> httpx.Response:
        return self._client.post(f"{self.base_url}{path}", json=payload)

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=30),
        stop=stop_after_attempt(4),
        retry=retry_if_exception_type(
            (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.RemoteProtocolError,
                httpx.ProtocolError,
                ConnectionError,
                OSError,
            )
        ),
        reraise=True,
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _post_with_retry(self, path: str, payload: Dict[str, Any]) -> httpx.Response:
        return self._post(path, payload)

    def push(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sends the payload to ``POST /api/sync`` and returns the parsed response.

        Raises ``RuntimeError`` when the worker rejects the payload.
        """
        response = self._post_with_retry("/api/sync", payload)
        if response.status_code >= 400:
            raise RuntimeError(
                f"Sync failed with HTTP {response.status_code}: {response.text[:500]}"
            )
        try:
            return response.json()
        except ValueError:
            return {"ok": True, "raw": response.text[:500]}


def load_secret_file(path: Path) -> Dict[str, str]:
    """Loads a JSON file with secret configuration, ignoring missing files."""
    try:
        if not path.is_file():
            return {}
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return {}
        return {str(key): str(value) for key, value in data.items() if value is not None}
    except Exception as error:  # pragma: no cover - defensive
        logger.warning("[CloudflareSync] Could not read secret file %s: %s", path, error)
        return {}

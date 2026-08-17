"""Downloads Menorah's public policy archive for health and life.

Data flow (confirmed against the live site on 2026-07-22):
1. Load the search page fresh for each category, select the "תחום" (line
   of business) dropdown option, click the search button, and capture the
   `/api/v1/search/` XHR response. It returns every matching document in
   one JSON payload (no pagination) - same pattern as Clal, simpler than
   Phoenix's page-by-page archive.
2. Each result's `documentURL` is a plain URL on a separate `cdn.` host - a
   plain HTTP GET (no browser, no cookies) downloads it directly.

The appendix number isn't a separate structured field here (unlike
Phoenix/Clal) - it's embedded as "נספח <number>" inside `policyHeader`
and/or `tags`, so it's parsed with the same shared regex Migdal already
uses on raw PDF text (`core.extraction.appendix_number.find_appendix_numbers`)
rather than a bespoke one.

The search page sits behind real bot-management that issued an actual
CAPTCHA challenge after a burst of requests in quick succession (confirmed
live) - much stronger than Phoenix's WAF or Clal's cookie-based gate. A
single slow request cleared it again within a couple of minutes. This
means listing must go one category at a time with a real delay between
each (see MenorahConfig.listing_delay_seconds), never in parallel or back
to back. PDF downloads from the separate `cdn.menoramivt.co.il` host are
NOT behind this protection - plain HTTP with a normal User-Agent works
fine (confirmed live).

Each result also carries `policyIssueDate`/`policyEndDate` (ISO datetimes)
- confirmed live (2026-08-11) as a genuine validity window, same role as
Clal's StartValidity/EndValidity: the same policyHeader appears repeatedly
across results with different, real past end dates for superseded
versions. Currently-active documents use a far-future sentinel end date
rather than a null/unset marker - `"2100-12-01T13:00:00.000Z"` observed
consistently (51/333 documents in a live sample, always that exact
value) - treated as "no real end date" the same way Clal treats its
`"0001-01-01"` unset sentinel, just at the other end of time.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
from playwright.sync_api import Page, sync_playwright

from companies.menorah.config import DOMAIN_TO_LINES_OF_BUSINESS, MenorahConfig
from core.exceptions import StorageError
from core.extraction.appendix_number import find_appendix_numbers
from core.plugins.base import BaseDownloader
from core.storage.local import LocalFileStorage
from core.utils.hashing import sha256_of_bytes, sha256_of_file
from core.utils.logging import get_logger

logger = get_logger(__name__)

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_MAX_FILENAME_LENGTH = 150


@dataclass(frozen=True)
class MenorahDocumentRef:
    """One document listed in Menorah's policy archive."""

    domain: str  # "health" | "life"
    title: str
    appendix_numbers: list[str]  # a document can legitimately cover more than one
    download_url: str
    marketing_start_date: date | None = None  # "policyIssueDate"
    marketing_end_date: date | None = None  # "policyEndDate" - far-future sentinel == active

    @property
    def is_active(self) -> bool:
        """Mirrors Document.is_active - True whenever there's no end date at
        all, or it hasn't passed yet."""
        return self.marketing_end_date is None or self.marketing_end_date >= date.today()

    @property
    def local_filename(self) -> str:
        """The saved file's name: decoded, and length-capped for Windows.

        Same rationale as Phoenix's/Clal's identically-named property.
        """
        name = unquote(self.download_url.rsplit("/", 1)[-1])
        if len(name) <= _MAX_FILENAME_LENGTH:
            return name
        stem, _, ext = name.rpartition(".")
        ext = f".{ext}" if ext else ""
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
        keep = _MAX_FILENAME_LENGTH - len(ext) - len(digest) - 1
        return f"{stem[:keep]}_{digest}{ext}"


_FAR_FUTURE_SENTINEL_YEAR = 2090


def _parse_date(text: str | None) -> date | None:
    """Parse the API's ISO datetime strings; missing/far-future-sentinel/
    unparseable -> None (None is the expected, meaningful case for
    policyEndDate: far-future == active)."""
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        logger.warning("Menorah: unparseable date %r", text)
        return None
    if parsed.year >= _FAR_FUTURE_SENTINEL_YEAR:
        return None
    return parsed


def refs_from_search_response(domain: str, body: dict[str, Any]) -> list[MenorahDocumentRef]:
    """Turn one `/api/v1/search/` JSON response into document refs.

    Pure function so the (occasionally messy) response shape is testable
    directly, without any Playwright involved.
    """
    if body.get("err"):
        logger.warning("Menorah %s search returned an error: %s", domain, body["err"])
        return []

    refs = []
    for policy in body.get("data", []):
        url = policy.get("documentURL")
        if not url:
            continue
        title = (policy.get("policyHeaderForDisplay") or policy.get("policyHeader") or "").strip()
        haystack = " ".join([title, *policy.get("tags", [])])
        refs.append(
            MenorahDocumentRef(
                domain=domain,
                title=title,
                appendix_numbers=find_appendix_numbers(haystack),
                download_url=url,
                marketing_start_date=_parse_date(policy.get("policyIssueDate")),
                marketing_end_date=_parse_date(policy.get("policyEndDate")),
            )
        )
    return refs


class MenorahDownloader(BaseDownloader):
    """Fetches and downloads Menorah's health/life policy-archive documents."""

    def __init__(self, config: MenorahConfig, http_client: httpx.Client | None = None) -> None:
        super().__init__(config)
        self._config: MenorahConfig = config
        self._client = http_client or httpx.Client(
            timeout=config.request_timeout_seconds,
            headers={"User-Agent": _BROWSER_USER_AGENT},
            follow_redirects=True,
        )

    def list_documents(self) -> list[MenorahDocumentRef]:
        """Fetch every health/life document ref for Menorah, across every
        line-of-business category (see DOMAIN_TO_LINES_OF_BUSINESS)."""
        refs: list[MenorahDocumentRef] = []
        seen_urls: set[str] = set()
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True, args=["--disable-blink-features=AutomationControlled"]
            )
            context = browser.new_context(
                user_agent=_BROWSER_USER_AGENT,
                locale="he-IL",
                viewport={"width": 1366, "height": 900},
            )
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            page = context.new_page()

            for domain, lines in DOMAIN_TO_LINES_OF_BUSINESS.items():
                for lob_id, option_label in lines:
                    for ref in self._list_line_of_business(page, domain, lob_id, option_label):
                        if ref.download_url not in seen_urls:
                            seen_urls.add(ref.download_url)
                            refs.append(ref)
                    if self._config.listing_delay_seconds:
                        time.sleep(self._config.listing_delay_seconds)

            browser.close()

        logger.info("Menorah archive: %d health/life documents found", len(refs))
        return refs

    def _list_line_of_business(
        self, page: Page, domain: str, lob_id: int, option_label: str
    ) -> list[MenorahDocumentRef]:
        """Always starts from a fresh page load: the dropdown's own visible
        text becomes the selected option's label after a click, so reusing
        the same loaded page across categories would break the next
        category's "click the placeholder" step. A fresh load always starts
        back at the unselected "תחום" placeholder state."""
        page.goto(self._config.search_page_url, wait_until="load", timeout=60000)
        page.wait_for_timeout(4000)

        domain_field = page.locator("text=תחום").first
        box = domain_field.bounding_box()
        if box is None:
            logger.warning("Menorah %s (id=%d): domain field not found", domain, lob_id)
            return []
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.wait_for_timeout(800)
        page.get_by_role("option", name=option_label).click()
        page.wait_for_timeout(800)

        api_fragment = self._config.search_api_url_fragment
        try:
            with page.expect_response(
                lambda r: api_fragment in r.url, timeout=20000
            ) as response_info:
                page.click("#searchBtn")
            body = response_info.value.json()
        except Exception as exc:  # noqa: BLE001 - nav/JSON/timeout all mean "try again later"
            logger.warning("Menorah %s (id=%d) search failed: %s", domain, lob_id, exc)
            return []

        refs = refs_from_search_response(domain, body)
        logger.info(
            "Menorah %s (id=%d, %s): %d documents", domain, lob_id, option_label, len(refs)
        )
        return refs

    def download_all(
        self,
        destination_dir: Path,
        limit: int | None = None,
        refs: list[MenorahDocumentRef] | None = None,
    ) -> list[Path]:
        """Download every listed document, deduplicated by content hash."""
        if refs is None:
            refs = self.list_documents()
        if limit is not None:
            refs = refs[:limit]

        storage = LocalFileStorage(destination_dir)
        seen_hashes: set[str] = self._hash_existing_files(destination_dir)
        saved_paths: list[Path] = []

        for ref in refs:
            relative_path = f"{ref.domain}/{ref.local_filename}"
            try:
                if storage.exists(relative_path):
                    saved_paths.append(destination_dir / relative_path)
                    continue

                content = self._fetch_with_retry(ref.download_url)

                content_hash = sha256_of_bytes(content)
                if content_hash in seen_hashes:
                    logger.debug("Skipping duplicate content for %s", ref.local_filename)
                    continue
                seen_hashes.add(content_hash)

                saved_path = storage.save(relative_path, content)
                saved_paths.append(saved_path)
                logger.info("Downloaded %s (%s)", relative_path, ref.title)
            except (httpx.HTTPError, StorageError) as exc:
                logger.warning("Failed to download %s: %s", ref.download_url, exc)

            if self._config.download_delay_seconds:
                time.sleep(self._config.download_delay_seconds)

        return saved_paths

    def _fetch_with_retry(self, url: str) -> bytes:
        """GET url, retrying transient failures but not permanent ones."""
        max_attempts = self._config.download_retry_max_attempts
        base_delay = self._config.download_retry_base_seconds
        last_exc: httpx.HTTPError | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                resp = self._client.get(url)
                resp.raise_for_status()
                return resp.content
            except httpx.HTTPStatusError as exc:
                if 400 <= exc.response.status_code < 500:
                    raise
                last_exc = exc
            except httpx.HTTPError as exc:
                last_exc = exc

            if attempt < max_attempts:
                logger.warning(
                    "Retrying download (attempt %d/%d): %s: %s",
                    attempt,
                    max_attempts,
                    url,
                    last_exc,
                )
                time.sleep(base_delay * attempt)

        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _hash_existing_files(destination_dir: Path) -> set[str]:
        if not destination_dir.is_dir():
            return set()
        return {sha256_of_file(path) for path in destination_dir.rglob("*") if path.is_file()}

    def close(self) -> None:
        self._client.close()

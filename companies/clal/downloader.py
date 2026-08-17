"""Downloads Clal's public policy-terms archive for health and life.

Data flow (confirmed against the live site on 2026-07-21):
1. Load the search page once (establishes the session's bot-management
   cookies), then for each domain, drive the real UI - select the "Family"
   and "Company" dropdowns and click the search button - and capture the
   `SearchPolicies` XHR response. It returns every matching document in one
   JSON payload (no pagination), each with a clean `AttachmentNumber`
   field. No OCR or text extraction is needed to get the appendix number,
   unlike Migdal.
2. Each result's `FilePath` is a plain relative URL under /media/ - a plain
   HTTP GET (no browser, no cookies) downloads it directly.

The listing request needs a real, already-interacted-with browser session:
calling the API directly (even with matching query params) returns a bare
404 "No HTTP resource" response - confirmed live, this is bot-management
rejecting the request, not the application. PDF downloads themselves are
plain HTTP, no browser required - also confirmed live.

Each policy also carries `StartValidity`/`EndValidity` (ISO datetimes,
`"0001-01-01T00:00:00"` as the sentinel for "unset") - confirmed live
(2026-08-10) as a genuine validity window, same role as Harel's
marketing_start_date/marketing_end_date: the same AttachmentNumber appears
repeatedly with different, sequential, non-overlapping windows as it's
superseded over time. `SoldData` (a separate bool on the same payload)
looked promising but does NOT track this - confirmed live it's a
product-level "still sold to new customers" flag, not document validity
(a currently-active-dated entry was seen with SoldData=false, and
long-expired entries with SoldData=true) - deliberately not used here.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import unquote

import httpx
from playwright.sync_api import sync_playwright

from companies.clal.config import DOMAIN_TO_FAMILY, ClalConfig
from core.exceptions import StorageError
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
class ClalDocumentRef:
    """One document listed in Clal's archive."""

    domain: str  # "health" | "life"
    title: str
    appendix_number: str  # "" when the site didn't provide one for this document
    policy_type: str  # e.g. "פרטי" / "קולקטיב" - informational only
    download_url: str
    marketing_start_date: date | None = None  # "StartValidity"
    marketing_end_date: date | None = None  # "EndValidity" - unset on-site == still active

    @property
    def is_active(self) -> bool:
        """Mirrors Document.is_active - True whenever there's no end date at
        all, or it hasn't passed yet."""
        return self.marketing_end_date is None or self.marketing_end_date >= date.today()

    @property
    def local_filename(self) -> str:
        """The saved file's name: decoded, and length-capped for Windows.

        Same rationale as Phoenix's identically-named property - some
        titles are long enough that a raw filename segment can exceed
        Windows' ~260-char path limit.
        """
        name = unquote(self.download_url.rsplit("/", 1)[-1])
        if len(name) <= _MAX_FILENAME_LENGTH:
            return name
        stem, _, ext = name.rpartition(".")
        ext = f".{ext}" if ext else ""
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
        keep = _MAX_FILENAME_LENGTH - len(ext) - len(digest) - 1
        return f"{stem[:keep]}_{digest}{ext}"


_UNSET_VALIDITY_SENTINEL = "0001-01-01T00:00:00"


def _parse_date(text: str | None) -> date | None:
    """Parse the API's ISO datetime strings; missing/sentinel/unparseable ->
    None (None is the expected, meaningful case for EndValidity: unset ==
    active)."""
    if not text or text == _UNSET_VALIDITY_SENTINEL:
        return None
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        logger.warning("Clal: unparseable date %r", text)
        return None


def refs_from_search_response(
    domain: str, body: dict, media_base_url: str
) -> list[ClalDocumentRef]:
    """Turn one `SearchPolicies` JSON response into document refs.

    Pulled out of `ClalDownloader._list_domain` as a pure function so the
    (occasionally messy - null AttachmentNumber, missing FilePath, a failed
    IsSuccess) response shape can be tested directly, without any
    Playwright involved.
    """
    if not body.get("IsSuccess"):
        logger.warning(
            "Clal %s search returned IsSuccess=false: %s", domain, body.get("ErrorMessage")
        )
        return []

    policies = [
        policy for family in body.get("FamilyPoliciesDetails", []) for policy in family["Policies"]
    ]
    return [
        ClalDocumentRef(
            domain=domain,
            title=(policy.get("Title") or "").strip(),
            appendix_number=(policy.get("AttachmentNumber") or "").strip(),
            policy_type=(policy.get("PolicyTypeDesc") or "").strip(),
            download_url=f"{media_base_url}{policy['FilePath']}",
            marketing_start_date=_parse_date(policy.get("StartValidity")),
            marketing_end_date=_parse_date(policy.get("EndValidity")),
        )
        for policy in policies
        if policy.get("FilePath")
    ]


class ClalDownloader(BaseDownloader):
    """Fetches and downloads Clal's health/life policy-terms documents."""

    def __init__(self, config: ClalConfig, http_client: httpx.Client | None = None) -> None:
        super().__init__(config)
        self._config: ClalConfig = config
        self._client = http_client or httpx.Client(
            timeout=config.request_timeout_seconds,
            headers={"User-Agent": _BROWSER_USER_AGENT},
            follow_redirects=True,
        )

    def list_documents(self) -> list[ClalDocumentRef]:
        """Fetch every health/life document ref for Clal, across both
        Company entities (see ClalConfig.company_filter_ids - "כלל ביטוח"
        and "כלל בריאות" have completely non-overlapping document sets)."""
        refs: list[ClalDocumentRef] = []
        seen_urls: set[str] = set()
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=_BROWSER_USER_AGENT, locale="he-IL")
            page = context.new_page()
            page.goto(self._config.search_page_url, wait_until="networkidle", timeout=60000)

            for domain, family_id in DOMAIN_TO_FAMILY.items():
                for company_id in self._config.company_filter_ids:
                    for ref in self._list_domain(page, domain, family_id, company_id):
                        if ref.download_url not in seen_urls:
                            seen_urls.add(ref.download_url)
                            refs.append(ref)
                    if self._config.listing_delay_seconds:
                        time.sleep(self._config.listing_delay_seconds)

            browser.close()

        logger.info("Clal archive: %d health/life documents found", len(refs))
        return refs

    def _list_domain(
        self, page, domain: str, family_id: str, company_id: str
    ) -> list[ClalDocumentRef]:
        page.select_option("#Family", family_id)
        page.wait_for_timeout(300)
        page.select_option("#Company", company_id)
        page.wait_for_timeout(300)

        api_fragment = self._config.search_api_url_fragment
        with page.expect_response(
            lambda r: api_fragment in r.url, timeout=30000
        ) as response_info:
            page.click("#BtnSearchPolicies")
        response = response_info.value

        try:
            body = response.json()
        except Exception as exc:  # noqa: BLE001 - response may not be valid JSON on failure
            logger.warning("Clal %s search: failed to parse response: %s", domain, exc)
            return []

        refs = refs_from_search_response(domain, body, self._config.media_base_url)
        logger.info(
            "Clal %s (company=%s): %d documents (TotalResultCount=%s)",
            domain,
            company_id,
            len(refs),
            body.get("TotalResultCount"),
        )
        return refs

    def download_all(
        self,
        destination_dir: Path,
        limit: int | None = None,
        refs: list[ClalDocumentRef] | None = None,
    ) -> list[Path]:
        """Download every listed document, deduplicated by content hash.

        `refs`, if given, skips re-fetching the listing - useful when the
        caller already fetched it (e.g. to also populate the DB from the
        same listing afterwards without hitting the site's search endpoint
        a second time).
        """
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

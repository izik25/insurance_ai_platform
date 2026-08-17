"""Downloads Ayalon's public policy archive for health and life.

Data flow (confirmed against the live site on 2026-08-12):
1. Load the homepage once, then load the archive page - this establishes
   the session's bot-management cookies (see below) - and capture the
   archive page's own `POST /api/policyarchive/search` XHR response (empty
   JSON body `{}`). It returns the ENTIRE archive - every category, every
   subject - in one response (`{"totalCount": 1139, "items": [...]}`),
   confirmed live: `totalCount` exactly matched `len(items)`. No dropdown
   selection, no per-category looping, no pagination needed at all -
   simpler than every other company plugin so far.
2. Each item's `fileUrl` is a relative `/media/...` path on the same host -
   a plain HTTP GET (no browser, no cookies) downloads it directly,
   confirmed live.

The archive page sits behind ShieldSquare/Radware bot-management (an
hCaptcha challenge page, confirmed live for a bare headless-Chromium
request straight to /archive with no prior navigation). Loading the
homepage first, with basic stealth (hiding navigator.webdriver, a normal
desktop UA/locale, `--disable-blink-features=AutomationControlled` -
same technique already used by Phoenix/Menorah), clears it reliably - no
CAPTCHA-solving needed. The search endpoint itself also 302-redirects to
the same challenge when called directly via plain httpx with no session
(confirmed live) - same "needs a real, already-warmed-up browser session"
pattern as Clal. PDF downloads are NOT behind this protection - confirmed
live, a plain unauthenticated httpx GET returns the PDF directly.

Each item carries `categoryName`/`subjectName` (see config.py for how
these map to this platform's domain) and `fromDate`/`endDate` (ISO
datetimes) - confirmed live as a genuine validity window, same role as
every other company's marketing_start_date/marketing_end_date: `endDate`
null means still current, a real date means superseded (the response also
carries a redundant `isActive` bool that is not used here, trusting the
raw date the same way every other company plugin trusts its own raw
validity field).

No appendix-number field exists in this API's response - `policyName`
values often embed "נספח <number>" directly (e.g. "נספח 1326 - גילוי
נאות..."), parsed with the same shared regex Menorah/Migdal already use
(`core.extraction.appendix_number.find_appendix_numbers`) rather than a
bespoke one; expected to return [] for the (administrative/general) rows
that don't mention one, same "trust the source, else backfill via LLM"
rule as every other company.
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
from playwright.sync_api import sync_playwright

from companies.ayalon.config import (
    CATEGORY_TO_DOMAIN,
    COLLECTIVE_CATEGORY_NAME,
    COLLECTIVE_SUBJECT_TO_DOMAIN,
    AyalonConfig,
)
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
_STEALTH_INIT_SCRIPT = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
_MAX_FILENAME_LENGTH = 150


@dataclass(frozen=True)
class AyalonDocumentRef:
    """One document listed in Ayalon's policy archive."""

    domain: str  # "health" | "life"
    title: str  # policyName
    appendix_numbers: list[str]
    category_name: str
    subject_name: str
    download_url: str
    marketing_start_date: date | None = None  # "fromDate"
    marketing_end_date: date | None = None  # "endDate" - null on-site == still active

    @property
    def is_active(self) -> bool:
        """Mirrors Document.is_active - True whenever there's no end date at
        all, or it hasn't passed yet."""
        return self.marketing_end_date is None or self.marketing_end_date >= date.today()

    @property
    def department_name(self) -> str:
        return f"{self.category_name} / {self.subject_name}"

    @property
    def local_filename(self) -> str:
        """The saved file's name: decoded, and length-capped for Windows.

        Same rationale as Clal's/Menorah's identically-named property.
        """
        name = unquote(self.download_url.rsplit("/", 1)[-1])
        if len(name) <= _MAX_FILENAME_LENGTH:
            return name
        stem, _, ext = name.rpartition(".")
        ext = f".{ext}" if ext else ""
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
        keep = _MAX_FILENAME_LENGTH - len(ext) - len(digest) - 1
        return f"{stem[:keep]}_{digest}{ext}"


def _parse_date(text: str | None) -> date | None:
    """Parse the API's ISO datetime strings; missing/unparseable -> None
    (None is the expected, meaningful case for endDate: unset == active)."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        logger.warning("Ayalon: unparseable date %r", text)
        return None


def _domain_for_item(category_name: str, subject_name: str) -> str | None:
    """Returns this item's platform domain, or None when it's out of scope
    (see config.py for exactly which categories/subjects are tracked)."""
    if category_name == COLLECTIVE_CATEGORY_NAME:
        return COLLECTIVE_SUBJECT_TO_DOMAIN.get(subject_name)
    return CATEGORY_TO_DOMAIN.get(category_name)


def refs_from_search_response(body: dict[str, Any]) -> list[AyalonDocumentRef]:
    """Turn one `/api/policyarchive/search` JSON response into document
    refs, restricted to the categories/subjects this platform tracks.

    Pure function so the (whole-archive) response shape is testable
    directly, without any Playwright involved.
    """
    refs = []
    for item in body.get("items", []):
        file_url = item.get("fileUrl")
        if not file_url:
            continue
        category_name = item.get("categoryName") or ""
        subject_name = item.get("subjectName") or ""
        domain = _domain_for_item(category_name, subject_name)
        if domain is None:
            continue

        title = (item.get("policyName") or "").strip()
        refs.append(
            AyalonDocumentRef(
                domain=domain,
                title=title,
                appendix_numbers=find_appendix_numbers(title),
                category_name=category_name,
                subject_name=subject_name,
                download_url=f"https://www.ayalon-ins.co.il{file_url}",
                marketing_start_date=_parse_date(item.get("fromDate")),
                marketing_end_date=_parse_date(item.get("endDate")),
            )
        )
    return refs


class AyalonDownloader(BaseDownloader):
    """Fetches and downloads Ayalon's health/life policy-archive documents."""

    def __init__(self, config: AyalonConfig, http_client: httpx.Client | None = None) -> None:
        super().__init__(config)
        self._config: AyalonConfig = config
        self._client = http_client or httpx.Client(
            timeout=config.request_timeout_seconds,
            headers={"User-Agent": _BROWSER_USER_AGENT},
            follow_redirects=True,
        )

    def list_documents(self) -> list[AyalonDocumentRef]:
        """Fetch every in-scope document ref in one shot (see module
        docstring: the archive page's own search call already returns
        everything, no per-category requests needed)."""
        api_fragment = self._config.search_api_url_fragment
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True, args=["--disable-blink-features=AutomationControlled"]
            )
            context = browser.new_context(user_agent=_BROWSER_USER_AGENT, locale="he-IL")
            context.add_init_script(_STEALTH_INIT_SCRIPT)
            page = context.new_page()

            page.goto(self._config.warmup_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(3000)

            try:
                with page.expect_response(
                    lambda r: api_fragment in r.url, timeout=45000
                ) as response_info:
                    page.goto(
                        self._config.archive_page_url, wait_until="domcontentloaded", timeout=45000
                    )
                body = response_info.value.json()
            except Exception as exc:  # noqa: BLE001 - nav/JSON/timeout all mean "try again later"
                logger.warning("Ayalon archive listing failed: %s", exc)
                browser.close()
                return []

            browser.close()

        refs = refs_from_search_response(body)
        logger.info(
            "Ayalon archive: %d in-scope documents found (of %s total in archive)",
            len(refs),
            body.get("totalCount"),
        )
        return refs

    def download_all(
        self,
        destination_dir: Path,
        limit: int | None = None,
        refs: list[AyalonDocumentRef] | None = None,
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

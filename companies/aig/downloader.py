"""Downloads AIG's public health/life policy-terms archive.

Data flow (confirmed against the live site on 2026-07-27):
1. `GET` each domain's product page (see `config.DOMAIN_TO_PAGE_URL`) returns
   fully server-rendered HTML (a plain `httpx` GET gets every document link
   already present - no browser/JS needed, unlike Phoenix/Clal/Menorah).
2. Every `<a href="...pdf">` on the page is one document; its link text is
   the title (e.g. "פוליסת בריאות בסיסית בתוקף החל מ 02.2024" - product name
   plus effective-date range, not a "נספח <number>" appendix number the way
   Migdal/Phoenix/Clal/Menorah documents are labeled).
3. Each PDF is served directly from `aig.co.il/media/<id>/<slug>.pdf` via a
   plain unauthenticated GET (confirmed live: `content-type: application/pdf`,
   status 200, no cookies/session needed) - same as the listing page, no bot
   protection observed anywhere in this flow.

Each product page lists the same document more than once (a "current
documents" grouping plus a full historical-editions list) - deduplicated
here by href, keeping the first title seen (the two copies were confirmed
identical whenever both were present).

Unlike Harel/Clal/Direct Insurance/Migdal, no structured validity-date
field exists anywhere on this site - the closest signal is embedded in the
title text itself, e.g. "... בתוקף החל מ 02.2024" (start only) or "... בתוקף
החל מ- 09.2023 ועד ל- 31.01.2024" (start+end) - parsed by
marketing_dates_from_title below. This is best-effort, NOT a reliable
active/historical split, confirmed live (2026-08-10):
- Only ~74% of real titles are cleanly regex-parseable as digit dates at
  all (DD.MM.YYYY / MM.YYYY / D.M.YY / D.M.YYYY, mixed within the same
  page); a further ~11% use Hebrew month names ("בתוקף מאוקטובר 17") with
  no digits at all - not handled here, left with no marketing dates.
- The page has a genuine "מהדורות קודמות" (previous editions) section
  distinct from the current-documents section (confirmed live via a
  `hide`-classed wrapper div), and at least one confirmed-superseded
  document in that section has NO end date in its title at all - so
  "title has no עד" must NOT be read as proof of "currently active", only
  as "no evidence either way" (same accepted-gap posture as everywhere
  else "no signal" means "active by default"). `refs_from_page_html`
  does not currently distinguish that section from the current one -
  fixing that would need a genuinely different HTML-traversal strategy,
  deliberately not attempted here.

Critical illness ("Extra Care"/מחלות קשות) has no page of its own: its
documents already appear on both the health and life pages (confirmed live,
different media IDs even for the same effective date - likely a separate
accessibility-rendered copy per page rather than a shared file). Domain
here is simply "whichever page this ref came from"; health is listed first
in `DOMAIN_TO_PAGE_URL` and `download_all`'s existing content-hash dedup
(shared across every ref processed in one call) means an identical-bytes
duplicate from the life page is silently skipped, so Extra Care lands as
domain="health" - consistent with how every other company on this platform
classifies critical illness. A byte-different copy (e.g. a differently
rendered accessible PDF) instead saves as its own document under domain
="life"; left as-is rather than special-cased further, same tolerance the
project already extends to Clal's two overlapping company entities.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from companies.aig.config import DOMAIN_TO_PAGE_URL, AigConfig
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
class AigDocumentRef:
    """One document listed on an AIG product page."""

    domain: str  # "health" | "life"
    title: str
    appendix_numbers: list[str]
    download_url: str
    marketing_start_date: date | None = None  # best-effort, from title text - see module docstring
    marketing_end_date: date | None = None  # ditto - absence does NOT mean "active", just "unknown"

    @property
    def is_active(self) -> bool:
        """Mirrors Document.is_active - True whenever there's no end date at
        all, or it hasn't passed yet."""
        return self.marketing_end_date is None or self.marketing_end_date >= date.today()

    @property
    def local_filename(self) -> str:
        """`<media-id>_<decoded-slug>.pdf` - the media id (already unique
        per Umbraco file) guards against two different documents ever
        producing the same decoded slug; the length cap is a hard backstop
        so no title, however long, can crash a save (same approach as
        Phoenix's `local_filename`)."""
        parts = urlparse(self.download_url).path.strip("/").split("/")
        media_id = parts[-2] if len(parts) >= 2 else "aig"
        name = unquote(parts[-1])
        stem, _, ext = name.rpartition(".")
        ext = f".{ext}" if ext else ""
        prefix = f"{media_id}_"
        keep = _MAX_FILENAME_LENGTH - len(prefix) - len(ext)
        return f"{prefix}{stem[:keep]}{ext}"


_DATE_TOKEN = r"\d{1,2}\.\d{1,2}\.\d{2,4}|\d{1,2}\.\d{4}"
_START_DATE_PATTERN = re.compile(rf"(?:בתוקף\s+)?(?:החל\s+)?מ-?\s*({_DATE_TOKEN})")
_END_DATE_PATTERN = re.compile(rf"עד\s*ל?-?\s*({_DATE_TOKEN})")


def _parse_title_date(text: str) -> date | None:
    """Parse one "D.M.YYYY" / "D.M.YY" / "M.YYYY" token from title text."""
    parts = text.split(".")
    try:
        if len(parts) == 2:
            month, year = int(parts[0]), int(parts[1])
            return date(year, month, 1)
        if len(parts) == 3:
            day, month, year_str = int(parts[0]), int(parts[1]), parts[2]
            year = int(year_str) if len(year_str) == 4 else 2000 + int(year_str)
            return date(year, month, day)
    except ValueError:
        pass
    logger.warning("AIG: unparseable title date %r", text)
    return None


def marketing_dates_from_title(title: str) -> tuple[date | None, date | None]:
    """Best-effort (start, end) extraction from title text - see module
    docstring for the real-world coverage/reliability caveats."""
    start_match = _START_DATE_PATTERN.search(title)
    end_match = _END_DATE_PATTERN.search(title)
    start = _parse_title_date(start_match.group(1)) if start_match else None
    end = _parse_title_date(end_match.group(1)) if end_match else None
    return start, end


def refs_from_page_html(domain: str, html: str) -> list[AigDocumentRef]:
    """Turn one product page's HTML into document refs.

    Pure function so the page structure is testable directly, without any
    network involved.
    """
    soup = BeautifulSoup(html, "html.parser")
    refs: list[AigDocumentRef] = []
    seen_urls: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if not href.lower().endswith(".pdf"):
            continue
        url = href.replace("aig.co.il//media", "aig.co.il/media")
        if url in seen_urls:
            continue
        seen_urls.add(url)

        title = anchor.get_text(" ", strip=True)
        start_date, end_date = marketing_dates_from_title(title)
        refs.append(
            AigDocumentRef(
                domain=domain,
                title=title,
                appendix_numbers=find_appendix_numbers(title),
                download_url=url,
                marketing_start_date=start_date,
                marketing_end_date=end_date,
            )
        )

    return refs


class AigDownloader(BaseDownloader):
    """Fetches and downloads AIG's health/life policy-terms documents."""

    def __init__(self, config: AigConfig, http_client: httpx.Client | None = None) -> None:
        super().__init__(config)
        self._config: AigConfig = config
        self._client = http_client or httpx.Client(
            timeout=config.request_timeout_seconds,
            headers={"User-Agent": _BROWSER_USER_AGENT},
            follow_redirects=True,
        )

    def list_documents(self) -> list[AigDocumentRef]:
        """Fetch every document ref across every tracked domain's page."""
        refs: list[AigDocumentRef] = []
        for domain, page_url in DOMAIN_TO_PAGE_URL.items():
            try:
                resp = self._client.get(page_url)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("AIG %s page fetch failed: %s", domain, exc)
                continue

            domain_refs = refs_from_page_html(domain, resp.text)
            logger.info("AIG %s: %d documents found", domain, len(domain_refs))
            refs.extend(domain_refs)

        logger.info("AIG archive: %d documents found", len(refs))
        return refs

    def download_all(
        self,
        destination_dir: Path,
        limit: int | None = None,
        refs: list[AigDocumentRef] | None = None,
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

"""Downloads Harel's public policy archive for health and life.

Data flow (confirmed against the live site on 2026-08-05 - see
config.py's module docstring for the query-string contract, reverse
engineered from `Harel.PoliciesArchive.Filter()` in harel_common.js):

1. `GET archive.aspx?cn=הראל&t2=<area GUID>&p=<page>` returns a fully
   server-rendered results table for that area/company/page - a plain
   `httpx` GET (no Playwright, no session/cookies beyond defaults) gets the
   real table, confirmed live. Simplest company plugin so far, on par with
   Direct Insurance/AIG.
2. Each row's first `<a href="....pdf">` is the document (title = link
   text, minus trailing whitespace); the "מספר נספח" column (2nd `<td>`) is
   the appendix number when present - the site's own metadata, no OCR/LLM
   guessing needed, same as Phoenix/Clal/Menorah.
2b. The table has 6 columns, not 2 (confirmed live 2026-08-10): columns 3
   and 4 are "תאריך תחילת שיווק"/"תאריך סיום שיווק" (marketing start/end
   date, DD.MM.YYYY). A blank end-date cell means the row is the version
   currently on sale; a past end-date means it's a superseded historical
   version kept in the archive for disclosure - confirmed live by paging to
   the end of the health listing (page 34/35 of 35), where every row has a
   blank end date, versus early pages where every row's end date has
   already passed. This end date is what `Document.is_active` is built
   from - it is NOT reliably present in the PDF body text itself (checked
   a sample of downloaded PDFs: the word "תוקף" does appear, but as generic
   policy-wording boilerplate, never paired with these same dates), so it
   has to come from this listing, not from parsing/OCR of the document.
3. Total page count for the current filter comes from
   `#HiddFieldNumOfLinks`'s value on page 1's response - read once, then
   pages 2..N are fetched directly rather than re-deriving the count or
   parsing the pagination widget's link list.
4. PDF hrefs are already-decoded absolute URLs (e.g.
   ".../Policies/ביקור רופא בבית.pdf" - literal Hebrew + spaces, not
   percent-encoded) - `httpx` encodes them correctly on request without any
   extra handling; used verbatim as `download_url` and, for the local
   filename, as the source of the human-readable name.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import unquote

import httpx
from bs4 import BeautifulSoup

from companies.harel.config import (
    AREA_LABELS,
    AREA_TO_DOMAIN,
    LIVE_AREA_TO_DOMAIN,
    LIVE_PAGE_APPENDIX_NUMBER_DENYLIST,
    LIVE_PAGE_EXCLUDED_TITLE_SUBSTRINGS,
    LIVE_PRODUCT_PAGES,
    UNFILTERED_AREAS,
    HarelConfig,
)
from core.exceptions import PdfProcessingError, StorageError
from core.pdf_processing.document import PdfDocument
from core.pdf_processing.reading_order import reconstruct_rtl_line_order
from core.plugins.base import BaseDownloader
from core.storage.local import LocalFileStorage
from core.utils.hashing import sha256_of_bytes, sha256_of_file
from core.utils.logging import get_logger

logger = get_logger(__name__)

# Live-page appendix/edition extraction - see config.py's module docstring
# for why this exists and its confirmed-live accuracy caveats. Tried in
# order: an exact "נספח/תכנית (מספר/מס')? NNN" match after reconstructing
# true reading order (highest confidence) -> a loose proximity match to the
# same keywords (handles PDFs where reading order is still off, or a
# character run within a single word got reversed - a proximity search
# doesn't care what's between the keyword and the number) -> a loose
# proximity match to the edition date mention (some live pages print the
# appendix number as a bare number next to "מהדורה" with no נספח/תכנית
# keyword at all - confirmed live on appendix 191's own cover page).
_CLEAN_APPENDIX_RE = re.compile(r"(?:נספח|תכנית)\s*(?:מספר|מס['׳]?)\s*[:\-]?\s*(\d{2,4})")
_KEYWORD_RE = re.compile(r"(נספח|תכנית)")
_NUM_RE = re.compile(r"(?<![\d/.])(\d{2,4})(?![\d/.])")
_EDITION_RE = re.compile(
    r"מהדורה?\s{0,3}[:\-]?\s{0,3}(\d{2}[./]\d{4})|(\d{2}[./]\d{4})\s{0,3}מהדורה"
)
_EDITION_WORD_RE = re.compile(r"מהדורה")
_TITLE_WINDOW = 1500
_PROXIMITY = 40


def _nearest_number(window: str, keyword_re: re.Pattern[str], proximity: int) -> str | None:
    best: str | None = None
    best_dist: int | None = None
    for kw in keyword_re.finditer(window):
        lo, hi = max(0, kw.start() - proximity), min(len(window), kw.end() + proximity)
        for num in _NUM_RE.finditer(window[lo:hi]):
            num_pos = lo + num.start()
            dist = min(abs(num_pos - kw.start()), abs(num_pos - kw.end()))
            if best_dist is None or dist < best_dist:
                best_dist, best = dist, num.group(1)
    return best


def extract_appendix_and_edition(text: str) -> tuple[str | None, date | None]:
    """Best-effort (number, marketing_start_date) from a live-page PDF's
    page-1 text. See module-level regex comment for the fallback chain."""
    window = text[:_TITLE_WINDOW]

    number = None
    clean_match = _CLEAN_APPENDIX_RE.search(window)
    if clean_match:
        number = clean_match.group(1)
    if number is None:
        number = _nearest_number(window, _KEYWORD_RE, _PROXIMITY)
    if number is None:
        number = _nearest_number(window, _EDITION_WORD_RE, 80)

    if number is not None:
        normalized = number.lstrip("0") or "0"
        if normalized in LIVE_PAGE_APPENDIX_NUMBER_DENYLIST or normalized == "0":
            number = None

    edition_date = None
    edition_match = _EDITION_RE.search(window)
    if edition_match:
        edition_text = edition_match.group(1) or edition_match.group(2)
        month, year = re.split(r"[./]", edition_text)
        try:
            edition_date = date(int(year), int(month), 1)
        except ValueError:
            edition_date = None

    return number, edition_date

_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_MAX_FILENAME_LENGTH = 150


@dataclass(frozen=True)
class HarelDocumentRef:
    """One document listed in Harel's policy archive."""

    domain: str  # "health" | "life"
    title: str
    appendix_numbers: list[str]  # 0 or 1 entries - one "מספר נספח" column per row
    download_url: str
    marketing_start_date: date | None = None  # "תאריך תחילת שיווק"
    marketing_end_date: date | None = None  # "תאריך סיום שיווק" - blank on-site == still active

    @property
    def is_active(self) -> bool:
        """Mirrors Document.is_active - True whenever there's no end date at
        all, or it hasn't passed yet."""
        return self.marketing_end_date is None or self.marketing_end_date >= date.today()

    @property
    def local_filename(self) -> str:
        """Decoded, length-capped for Windows - same rationale as every
        other title-derived `local_filename` on this platform (Phoenix/
        Menorah/AIG)."""
        name = unquote(self.download_url.rsplit("/", 1)[-1])
        if len(name) <= _MAX_FILENAME_LENGTH:
            return name
        stem, _, ext = name.rpartition(".")
        ext = f".{ext}" if ext else ""
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
        keep = _MAX_FILENAME_LENGTH - len(ext) - len(digest) - 1
        return f"{stem[:keep]}_{digest}{ext}"


def _parse_date(text: str) -> date | None:
    """Parse the archive's "DD.MM.YYYY" date cells; blank/unparseable -> None
    (blank is the expected, meaningful case: no end date == still active)."""
    text = text.strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%d.%m.%Y").date()
    except ValueError:
        logger.warning("Harel archive: unparseable date %r", text)
        return None


def _parse_total_pages(html: str) -> int:
    soup = BeautifulSoup(html, "html.parser")
    field = soup.find(id="HiddFieldNumOfLinks")
    if field is None:
        return 1
    try:
        return max(1, int(field.get("value", "1")))
    except ValueError:
        return 1


def refs_from_archive_html(domain: str, html: str) -> list[HarelDocumentRef]:
    """Turn one archive results page's HTML into document refs.

    Pure function so the page structure is testable directly, without any
    network involved.
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find(id="policies")
    if table is None:
        return []

    refs: list[HarelDocumentRef] = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 2:
            continue
        anchor = cells[0].find("a", href=True)
        if anchor is None or not anchor["href"].lower().endswith(".pdf"):
            continue

        title = anchor.get_text(" ", strip=True)
        appendix_text = cells[1].get_text(strip=True)
        start_date = _parse_date(cells[3].get_text(strip=True)) if len(cells) > 3 else None
        end_date = _parse_date(cells[4].get_text(strip=True)) if len(cells) > 4 else None
        refs.append(
            HarelDocumentRef(
                domain=domain,
                title=title,
                appendix_numbers=[appendix_text] if appendix_text else [],
                download_url=anchor["href"],
                marketing_start_date=start_date,
                marketing_end_date=end_date,
            )
        )
    return refs


class HarelDownloader(BaseDownloader):
    """Fetches and downloads Harel's health/life policy-archive documents."""

    def __init__(self, config: HarelConfig, http_client: httpx.Client | None = None) -> None:
        super().__init__(config)
        self._config: HarelConfig = config
        self._client = http_client or httpx.Client(
            timeout=config.request_timeout_seconds,
            headers={"User-Agent": _BROWSER_USER_AGENT},
            follow_redirects=True,
        )

    def list_documents(self, include_live: bool = True) -> list[HarelDocumentRef]:
        """Fetch every health/life document ref: archive.aspx (every tracked
        area, see config.AREA_TO_DOMAIN, paginated) plus - unless
        `include_live` is False - each live product page's current edition
        (see config.py's module docstring for why archive.aspx alone isn't
        enough)."""
        refs: list[HarelDocumentRef] = []
        seen_urls: set[str] = set()

        for area_guid, domain in AREA_TO_DOMAIN.items():
            for ref in self._list_area(area_guid, domain):
                if ref.download_url not in seen_urls:
                    seen_urls.add(ref.download_url)
                    refs.append(ref)

        logger.info("Harel archive: %d health/life documents found", len(refs))

        if include_live:
            live_refs = self.list_live_current_documents()
            new_live = 0
            for ref in live_refs:
                if ref.download_url not in seen_urls:
                    seen_urls.add(ref.download_url)
                    refs.append(ref)
                    new_live += 1
            logger.info(
                "Harel live pages: %d documents found (%d not already in archive listing)",
                len(live_refs),
                new_live,
            )

        return refs

    def list_live_current_documents(self) -> list[HarelDocumentRef]:
        """Crawl every page in config.LIVE_PRODUCT_PAGES, download each PDF
        once to read its appendix number/edition off page 1 (see
        extract_appendix_and_edition), and return one ref per PDF that both
        (a) isn't collateral (config.LIVE_PAGE_EXCLUDED_TITLE_SUBSTRINGS)
        and (b) yielded a usable appendix number. Marketing end date is
        always None - these are, by construction, each product's page as
        currently published, i.e. the currently-marketed edition."""
        refs: list[HarelDocumentRef] = []
        for area, slugs in LIVE_PRODUCT_PAGES.items():
            domain = LIVE_AREA_TO_DOMAIN[area]
            for slug in slugs:
                refs.extend(self._list_live_product_page(area, domain, slug))
        return refs

    def _list_live_product_page(self, area: str, domain: str, slug: str) -> list[HarelDocumentRef]:
        url = f"{self._config.live_product_base_url}/{area}/policies/{slug}"
        try:
            resp = self._client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Harel live page %s fetch failed: %s", url, exc)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        seen_hrefs: set[str] = set()
        refs: list[HarelDocumentRef] = []
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            if not href.lower().split("?")[0].endswith(".pdf") or href in seen_hrefs:
                continue
            seen_hrefs.add(href)
            title = anchor.get_text(" ", strip=True)
            if any(bad in title for bad in LIVE_PAGE_EXCLUDED_TITLE_SUBSTRINGS):
                continue

            number, edition_date = self._probe_live_pdf(href)
            if number is None:
                continue
            refs.append(
                HarelDocumentRef(
                    domain=domain,
                    title=title,
                    appendix_numbers=[number],
                    download_url=href,
                    marketing_start_date=edition_date,
                    marketing_end_date=None,
                )
            )
            if self._config.listing_delay_seconds:
                time.sleep(self._config.listing_delay_seconds)

        return refs

    def _probe_live_pdf(self, url: str) -> tuple[str | None, date | None]:
        try:
            content = self._fetch_with_retry(url)
        except httpx.HTTPError as exc:
            logger.warning("Harel live PDF probe failed for %s: %s", url, exc)
            return None, None

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        try:
            with PdfDocument(tmp_path) as doc:
                if doc.page_count == 0:
                    return None, None
                words = doc.extract_words(0)
                text = reconstruct_rtl_line_order(words)
        except PdfProcessingError as exc:
            logger.warning("Harel live PDF unreadable %s: %s", url, exc)
            return None, None
        finally:
            tmp_path.unlink(missing_ok=True)

        return extract_appendix_and_edition(text)

    def _list_area(self, area_guid: str, domain: str) -> list[HarelDocumentRef]:
        label = AREA_LABELS.get(area_guid, area_guid)
        area_refs: list[HarelDocumentRef] = []

        params: dict[str, str] = {"t2": area_guid}
        if area_guid not in UNFILTERED_AREAS:
            params["cn"] = self._config.company_filter_text

        page = 1
        total_pages = 1
        while page <= total_pages:
            try:
                resp = self._client.get(
                    self._config.archive_page_url,
                    params={**params, "p": str(page)},
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("Harel %s (%s) page %d fetch failed: %s", domain, label, page, exc)
                break

            if page == 1:
                total_pages = _parse_total_pages(resp.text)

            page_refs = refs_from_archive_html(domain, resp.text)
            area_refs.extend(page_refs)

            if self._config.listing_delay_seconds:
                time.sleep(self._config.listing_delay_seconds)
            page += 1

        logger.info("Harel %s (%s): %d documents across %d page(s)", domain, label, len(area_refs), total_pages)
        return area_refs

    def download_all(
        self,
        destination_dir: Path,
        limit: int | None = None,
        refs: list[HarelDocumentRef] | None = None,
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

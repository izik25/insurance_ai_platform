"""Downloads Phoenix's public policy-terms archive for health and life.

Data flow (confirmed against the live site on 2026-07-16):
1. GET `search_url?world=<World>&cover=&company=<company>&attache=&q=&qType=&page=<N>`
   returns one page of results as server-rendered HTML — a table where each
   row already carries the appendix number as a plain column. No OCR or
   text extraction is needed to get it, unlike Migdal.
2. Each row's document-title cell links directly to the PDF (a plain
   fnx.co.il URL, not behind the WAF). Iterate `page` until a page comes
   back with zero result rows.

The listing request needs a real browser: fnx.co.il sits behind AWS WAF
Bot Control, which returns a bare 403 for default headless Chromium.
Basic stealth (hiding navigator.webdriver, a normal desktop UA/locale) is
enough to pass it — no CAPTCHA-solving or deeper fingerprint spoofing was
needed. The PDF downloads themselves are plain HTTP, no browser required.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import httpx
from playwright.sync_api import Browser, Page, sync_playwright

from companies.phoenix.config import DOMAIN_COVERS, DOMAIN_TO_WORLD, PhoenixConfig
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
_STEALTH_INIT_SCRIPT = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
_NO_RESULTS_MARKER = "לא אותרו טפסים"
_ROW_EXTRACTION_JS = """els => els.map(tr => {
    const cells = tr.querySelectorAll('td');
    if (cells.length < 4) return null;
    const link = cells[1] ? cells[1].querySelector('a.fileLink') : null;
    if (!link) return null;
    const appendixNumber = cells[2].innerText.trim();
    if (!appendixNumber) return null;  // seen duplicated rows with a blank appendix cell
    return {
        title: link.innerText.trim(),
        href: link.href,
        appendix_number: appendixNumber,
        edition: cells[3].innerText.trim(),
    };
}).filter(Boolean)"""


_MAX_FILENAME_LENGTH = 150


@dataclass(frozen=True)
class PhoenixDocumentRef:
    """One document listed in Phoenix's archive."""

    domain: str  # "health" | "life"
    title: str
    appendix_number: str
    edition: str
    download_url: str

    @property
    def local_filename(self) -> str:
        """The saved file's name: URL-decoded, and length-capped for Windows.

        Some archive titles are long enough that the raw percent-encoded
        URL segment exceeds Windows' ~260-char path limit (confirmed live:
        a "גילוי נאות 1628 - ..." title crashed a real download with
        FileNotFoundError). Decoding first keeps names readable and usually
        short enough on its own; the length cap is a hard backstop so no
        title, however long, can crash a save.
        """
        name = unquote(self.download_url.rsplit("/", 1)[-1])
        if len(name) <= _MAX_FILENAME_LENGTH:
            return name
        stem, _, ext = name.rpartition(".")
        ext = f".{ext}" if ext else ""
        digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
        keep = _MAX_FILENAME_LENGTH - len(ext) - len(digest) - 1
        return f"{stem[:keep]}_{digest}{ext}"


class PhoenixDownloader(BaseDownloader):
    """Fetches and downloads Phoenix's health/life policy-terms documents."""

    def __init__(self, config: PhoenixConfig, http_client: httpx.Client | None = None) -> None:
        super().__init__(config)
        self._config: PhoenixConfig = config
        self._client = http_client or httpx.Client(
            timeout=config.request_timeout_seconds,
            headers={"User-Agent": _BROWSER_USER_AGENT},
            follow_redirects=True,  # download links are http:// and 301 to https://
        )

    def list_documents(self, max_pages: int | None = None) -> list[PhoenixDocumentRef]:
        """Fetch every health/life document ref for Phoenix, across all pages.

        `max_pages`, if given, caps how many result pages are fetched per
        domain — useful for a quick smoke test against the live site
        without paging through the full archive (~100+ pages per domain).
        """
        refs: list[PhoenixDocumentRef] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True, args=["--disable-blink-features=AutomationControlled"]
            )

            for domain, world in DOMAIN_TO_WORLD.items():
                refs.extend(self._list_domain(browser, domain, world, max_pages))

            browser.close()

        logger.info("Phoenix archive: %d health/life documents found", len(refs))
        return refs

    @staticmethod
    def _new_page(browser: Browser) -> Page:
        context = browser.new_context(
            user_agent=_BROWSER_USER_AGENT,
            viewport={"width": 1366, "height": 900},
            locale="he-IL",
        )
        context.add_init_script(_STEALTH_INIT_SCRIPT)
        return context.new_page()

    def _list_domain(
        self, browser: Browser, domain: str, world: str, max_pages: int | None = None
    ) -> list[PhoenixDocumentRef]:
        """Fetch every document ref for one domain, deduplicated across covers.

        An unfiltered query (cover="") gets stuck on an unreliable page 10
        for Phoenix's health archive no matter how it's retried (confirmed
        live), while the site's own per-category "cover" filter — driven
        directly as a query param, since the dropdown itself is broken
        markup on the site — reaches the same documents through queries
        that never get anywhere near that page count. `DOMAIN_COVERS` maps
        a domain to the sub-category values worth iterating; domains not
        listed there (life) just run a single unfiltered query, since that
        already paginates reliably end-to-end.
        """
        covers = DOMAIN_COVERS.get(domain, [""])
        seen_urls: set[str] = set()
        refs: list[PhoenixDocumentRef] = []
        for cover in covers:
            page = self._new_page(browser)
            for ref in self._list_domain_cover(browser, page, domain, world, cover, max_pages):
                if ref.download_url not in seen_urls:
                    seen_urls.add(ref.download_url)
                    refs.append(ref)

        logger.info(
            "Phoenix %s: %d distinct documents across %d cover-queries",
            domain,
            len(refs),
            len(covers),
        )
        return refs

    def _list_domain_cover(
        self,
        browser: Browser,
        page: Page,
        domain: str,
        world: str,
        cover: str,
        max_pages: int | None = None,
    ) -> list[PhoenixDocumentRef]:
        refs: list[PhoenixDocumentRef] = []
        page_number = 1
        known_last_page: int | None = None
        context_resets = 0
        while max_pages is None or page_number <= max_pages:
            rows, last_page_hint, needs_reset = self._fetch_page_rows(
                page, self._page_url(world, page_number, cover), page_number, known_last_page
            )
            if last_page_hint is not None:
                known_last_page = last_page_hint
            if rows is None:
                if needs_reset and context_resets < self._config.listing_context_reset_max:
                    context_resets += 1
                    logger.warning(
                        "Resetting browser session for %s page %d (reset %d/%d)",
                        domain,
                        page_number,
                        context_resets,
                        self._config.listing_context_reset_max,
                    )
                    page.context.close()
                    page = self._new_page(browser)
                    continue  # retry the same page_number on a fresh session
                break
            refs.extend(
                PhoenixDocumentRef(
                    domain=domain,
                    title=row["title"],
                    appendix_number=row["appendix_number"],
                    edition=row["edition"],
                    download_url=row["href"],
                )
                for row in rows
            )
            page_number += 1
            if self._config.listing_page_delay_seconds:
                time.sleep(self._config.listing_page_delay_seconds)

        page.context.close()
        logger.info(
            "Phoenix %s (cover=%r): %d documents across %d pages (pager last-page hint: %s)",
            domain,
            cover,
            len(refs),
            page_number - 1,
            known_last_page,
        )
        return refs

    def _fetch_page_rows(
        self, page: Page, url: str, page_number: int, known_last_page: int | None
    ) -> tuple[list[dict[str, str]] | None, int | None, bool]:
        """Return this page's rows (and the pager's reported last-page number).

        An empty row list is ambiguous on its own: the site occasionally
        renders a clean "no results" page even mid-archive (observed live:
        Phoenix health page 10 returned it — plain HTTP 200 — repeatedly,
        while the pager's own "last page" link on page 1 pointed to page
        114). So an empty page is only trusted as the real end when we have
        no reason to expect otherwise; if `known_last_page` says more pages
        should exist, retry with a larger budget before giving up. The
        third return value signals the caller that even that larger budget
        was exhausted while the page still looked premature — worth a fresh
        browser session (confirmed live: a brand-new context fetching the
        exact same "stuck" URL succeeds immediately), rather than a genuine
        end of pagination.
        """
        base_delay = self._config.listing_retry_base_seconds
        normal_attempts = self._config.listing_retry_max_attempts
        premature_attempts = self._config.listing_premature_end_max_attempts
        looks_premature = known_last_page is not None and page_number <= known_last_page
        max_attempts = premature_attempts if looks_premature else normal_attempts

        for attempt in range(1, max_attempts + 1):
            try:
                response = page.goto(url, wait_until="load", timeout=30000)
            except Exception as exc:  # noqa: BLE001 - Playwright raises various timeout/nav errors
                logger.warning(
                    "Navigation error (attempt %d/%d): %s: %s",
                    attempt,
                    max_attempts,
                    url,
                    exc,
                )
                if attempt < max_attempts:
                    time.sleep(base_delay * attempt)
                continue

            rows = page.eval_on_selector_all("tr", _ROW_EXTRACTION_JS)
            if rows:
                return rows, self._extract_last_page(page), False

            has_marker = _NO_RESULTS_MARKER in page.content()
            if not looks_premature and has_marker:
                return None, None, False  # genuine, expected end of pagination

            logger.warning(
                "Empty page (marker=%s, status=%s, premature=%s), attempt %d/%d: %s",
                has_marker,
                response.status if response else None,
                looks_premature,
                attempt,
                max_attempts,
                url,
            )
            if attempt < max_attempts:
                time.sleep(base_delay * attempt)

        logger.warning("Giving up on %s after %d attempts", url, max_attempts)
        return None, None, looks_premature

    @staticmethod
    def _extract_last_page(page: Page) -> int | None:
        """Read the pager's "<<" (last page) link to learn the true page count."""
        href = page.eval_on_selector(
            'a[id*="lnkLast"]', "el => el ? el.getAttribute('href') : null"
        )
        if not href:
            return None
        values = parse_qs(urlparse(href).query).get("page")
        if not values:
            return None
        try:
            return int(values[0])
        except ValueError:
            return None

    def _page_url(self, world: str, page_number: int, cover: str = "") -> str:
        params = {
            "world": world,
            "cover": cover,
            "company": self._config.company_filter,
            "attache": "",
            "q": "",
            "qType": "",
            "page": str(page_number),
        }
        return f"{self._config.search_url}?{urlencode(params)}"

    def download_all(
        self,
        destination_dir: Path,
        limit: int | None = None,
        refs: list[PhoenixDocumentRef] | None = None,
    ) -> list[Path]:
        """Download every listed document, deduplicated by content hash.

        `refs`, if given, skips re-fetching the listing — useful when the
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
        """GET url, retrying transient failures but not permanent ones.

        A 5xx (or network-level error) is worth retrying - confirmed live,
        this is what a 502 from fnx.co.il's gateway under sustained load
        looks like, and it clears up on retry. A 4xx means the link is
        genuinely broken upstream (real Phoenix-side dead links exist, seen
        throughout this archive) - retrying won't fix that, so it's raised
        immediately instead of wasting attempts.
        """
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

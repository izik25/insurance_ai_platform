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

import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

import httpx
from playwright.sync_api import Page, sync_playwright

from companies.phoenix.config import DOMAIN_TO_WORLD, PhoenixConfig
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
        return self.download_url.rsplit("/", 1)[-1]


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
            context = browser.new_context(
                user_agent=_BROWSER_USER_AGENT,
                viewport={"width": 1366, "height": 900},
                locale="he-IL",
            )
            context.add_init_script(_STEALTH_INIT_SCRIPT)
            page = context.new_page()

            for domain, world in DOMAIN_TO_WORLD.items():
                refs.extend(self._list_domain(page, domain, world, max_pages))

            browser.close()

        logger.info("Phoenix archive: %d health/life documents found", len(refs))
        return refs

    def _list_domain(
        self, page: Page, domain: str, world: str, max_pages: int | None = None
    ) -> list[PhoenixDocumentRef]:
        refs: list[PhoenixDocumentRef] = []
        page_number = 1
        while max_pages is None or page_number <= max_pages:
            rows = self._fetch_page_rows(page, self._page_url(world, page_number))
            if rows is None:
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

        logger.info(
            "Phoenix %s: %d documents across %d pages", domain, len(refs), page_number - 1
        )
        return refs

    def _fetch_page_rows(
        self, page: Page, url: str, max_attempts: int = 3
    ) -> list[dict[str, str]] | None:
        """Return this page's rows, or None once genuinely past the last page.

        An empty row list is ambiguous on its own: the WAF occasionally
        returns a page with no results even mid-archive (observed live),
        indistinguishable from a real "no more pages" without checking for
        the site's own no-results message. Retry a few times before
        accepting an empty page as the real end of pagination.
        """
        for attempt in range(1, max_attempts + 1):
            response = page.goto(url, wait_until="load", timeout=30000)
            rows = page.eval_on_selector_all("tr", _ROW_EXTRACTION_JS)
            if rows:
                return rows
            if _NO_RESULTS_MARKER in page.content():
                return None
            logger.warning(
                "Empty page with no results-marker (status=%s), attempt %d/%d: %s",
                response.status if response else None,
                attempt,
                max_attempts,
                url,
            )
            time.sleep(1.0)

        logger.warning("Giving up on %s after %d attempts", url, max_attempts)
        return None

    def _page_url(self, world: str, page_number: int) -> str:
        params = {
            "world": world,
            "cover": "",
            "company": self._config.company_filter,
            "attache": "",
            "q": "",
            "qType": "",
            "page": str(page_number),
        }
        return f"{self._config.search_url}?{urlencode(params)}"

    def download_all(self, destination_dir: Path, limit: int | None = None) -> list[Path]:
        """Download every listed document, deduplicated by content hash."""
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

                resp = self._client.get(ref.download_url)
                resp.raise_for_status()
                content = resp.content

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

    @staticmethod
    def _hash_existing_files(destination_dir: Path) -> set[str]:
        if not destination_dir.is_dir():
            return set()
        return {sha256_of_file(path) for path in destination_dir.rglob("*") if path.is_file()}

    def close(self) -> None:
        self._client.close()

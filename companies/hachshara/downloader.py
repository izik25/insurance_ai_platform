"""Downloads Hachshara's public file-finder archive (health, so far).

Data flow (confirmed live on 2026-07-22): a plain `GET` of a domain's
file-finder listing page (e.g. https://www.hcsra.co.il/file-finder/health-insurance/)
returns static Next.js SSR HTML with every document card already rendered -
no browser/JS execution needed, no bot-management observed. Each card looks
like:

    <a class="...StyledLink..." href="https://umbraco-api.hcsra.co.il/media/<id>/<filename>.pdf"
       target="_blank"><p class="...">TITLE</p></a>

and the same PDF URL is repeated in a separate "download" button anchor
right after it (no title text there) - deduplicated by URL, first title
text wins. `<id>` is an opaque 8-char CMS media id, unique and stable per
asset - used as the collision-proof prefix of the saved filename.

No dependency on an HTML parser (bs4/lxml/etc. aren't used anywhere else in
this repo) - the card markup is narrow and stable enough that plain `re`
avoids adding one for a single company, same spirit as every other plugin
avoiding unnecessary dependencies.

Unlike Phoenix/Menorah/Clal, listing needs no Playwright at all - same
minimal-tooling situation as Direct Insurance's plain-`httpx` archive.

There's no structured appendix-number field in this listing (titles read
like "הצעה לביטוח בריאות", "גילוי נאות מגן לניתוחים... נספח 526" -
sometimes containing "נספח <n>", often not at all). `find_appendix_numbers`
is applied to the title defensively, but the real source of truth for this
company is the PDF's own page-1 content - see extractor.py.
"""

from __future__ import annotations

import hashlib
import html
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

import httpx

from companies.hachshara.config import DOMAIN_TO_LISTING_PATH, HachsharaConfig
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

_TITLE_ANCHOR = re.compile(
    r'<a[^>]*class="[^"]*StyledLink[^"]*"[^>]*'
    r'href="(?P<url>https://umbraco-api\.hcsra\.co\.il/media/[^"]+?\.pdf)"[^>]*>'
    r"\s*<p[^>]*>(?P<title>[^<]*)</p>",
)
_ANY_MEDIA_HREF = re.compile(r'href="(https://umbraco-api\.hcsra\.co\.il/media/[^"]+?\.pdf)"')
_MEDIA_ID = re.compile(r"/media/([^/]+)/")


@dataclass(frozen=True)
class HachsharaDocumentRef:
    """One document listed on a Hachshara file-finder page."""

    domain: str  # "health" (only domain wired up so far)
    media_id: str  # opaque 8-char CMS id from the URL, unique per asset
    title: str
    appendix_numbers: list[str]  # defensive parse of the title; usually []
    download_url: str

    @property
    def local_filename(self) -> str:
        """media_id-prefixed origin filename, length-capped for Windows.

        Keeping the (capped) origin filename rather than dropping it - as
        Direct Insurance's simpler `{form_id}.pdf` scheme does - is
        deliberate: extractor.py's filename-hint fallback depends on tokens
        like "נספח_531" surviving in the saved name. media_id alone already
        makes the result collision-proof, so truncation of the origin stem
        is always safe.
        """
        original = unquote(self.download_url.rsplit("/", 1)[-1])
        stem, _, ext = original.rpartition(".")
        ext = f".{ext}" if ext else ".pdf"
        prefixed_stem = f"{self.media_id}_{stem}"
        if len(prefixed_stem) + len(ext) <= _MAX_FILENAME_LENGTH:
            return f"{prefixed_stem}{ext}"
        digest = hashlib.sha1(original.encode("utf-8")).hexdigest()[:8]
        keep = _MAX_FILENAME_LENGTH - len(self.media_id) - len(digest) - len(ext) - 2
        return f"{self.media_id}_{stem[:keep]}_{digest}{ext}"


def refs_from_listing_html(domain: str, html_text: str) -> list[HachsharaDocumentRef]:
    """Parse one file-finder listing page's raw HTML into document refs.

    Pure function (no I/O), testable against a saved sample page. Every PDF
    href under umbraco-api's /media/ path is ingested - no attempt is made
    to associate a card with one of the page's category tabs (see module
    docstring).
    """
    urls_with_titles: dict[str, str] = {}
    for match in _TITLE_ANCHOR.finditer(html_text):
        urls_with_titles.setdefault(match.group("url"), html.unescape(match.group("title")).strip())
    for match in _ANY_MEDIA_HREF.finditer(html_text):
        urls_with_titles.setdefault(match.group(1), "")

    refs: list[HachsharaDocumentRef] = []
    for url, title in urls_with_titles.items():
        id_match = _MEDIA_ID.search(url)
        if id_match is None:
            logger.warning("Could not parse media id from %s; skipping", url)
            continue
        refs.append(
            HachsharaDocumentRef(
                domain=domain,
                media_id=id_match.group(1),
                title=title,
                appendix_numbers=find_appendix_numbers(title),
                download_url=url,
            )
        )
    return refs


class HachsharaDownloader(BaseDownloader):
    """Fetches and downloads Hachshara's file-finder archive documents."""

    def __init__(self, config: HachsharaConfig, http_client: httpx.Client | None = None) -> None:
        super().__init__(config)
        self._config: HachsharaConfig = config
        self._client = http_client or httpx.Client(
            timeout=config.request_timeout_seconds,
            headers={"User-Agent": _BROWSER_USER_AGENT},
            follow_redirects=True,
        )

    def list_documents(self) -> list[HachsharaDocumentRef]:
        """Fetch every document ref across every tracked domain's listing page."""
        refs: list[HachsharaDocumentRef] = []
        seen_urls: set[str] = set()

        for domain, path in DOMAIN_TO_LISTING_PATH.items():
            for ref in self._list_domain(domain, path):
                if ref.download_url not in seen_urls:
                    seen_urls.add(ref.download_url)
                    refs.append(ref)
            if self._config.listing_delay_seconds:
                time.sleep(self._config.listing_delay_seconds)

        logger.info("Hachshara archive: %d documents found", len(refs))
        return refs

    def _list_domain(self, domain: str, path: str) -> list[HachsharaDocumentRef]:
        try:
            resp = self._client.get(f"{self._config.base_url}{path}")
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("Hachshara %s listing failed: %s", domain, exc)
            return []

        refs = refs_from_listing_html(domain, resp.text)
        logger.info("Hachshara %s: %d documents", domain, len(refs))
        return refs

    def download_all(
        self,
        destination_dir: Path,
        limit: int | None = None,
        refs: list[HachsharaDocumentRef] | None = None,
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

"""Downloads Direct Insurance's (ביטוח ישיר, 555.co.il) public forms/policy archive.

Data flow (confirmed live on 2026-07-22):
1. `GET /webapp/api/siteapi/form/formdata` returns the entire taxonomy in one
   call: products, each product's `salesGroup` sub-categories, and which
   form types (1="פוליסה וכתבי שירות", 2="טפסי שירות", 3="טפסי תביעות")
   are valid per salesGroup (`formTypesActive`). No UI/dropdown-driving
   needed at all - simpler than every other company plugin so far.
2. `POST /webapp/api/siteapi/form/sendformdata` with a JSON body
   `{"product": "<id>", "saleGroup": "<id>", "formType": [...], "active":
   "false"}` returns every matching document in one response
   (`{"status": 0, "collection": [...]}`) - `formType` accepts an array,
   so one request per salesGroup covers every valid form type for it.
   `active: "false"` ("כל הטפסים") is a strict superset of `"true"`
   ("נמכרים כעת" - currently-sold only), so querying it alone gives
   maximum coverage (confirmed live).
3. Each result's `formId` maps directly to a download URL:
   `/webapp/api/siteapi/form/openform/{formId}` - confirmed live to serve
   the PDF directly (`content-type: application/pdf`, status 200) via a
   plain unauthenticated GET. No browser/session/cookies needed anywhere
   in this flow - unlike every other company plugin so far, Direct
   Insurance has NO bot protection observed on either the search or
   download endpoints.

Only "פוליסה וכתבי שירות" (typeKey=1) results carry real policy/coverage
documents with product names and dates; "טפסי שירות"/"טפסי תביעות"
(typeKey=2/3) are administrative forms (cancellation requests, beneficiary
updates, claim-filing forms) with no coverage content - confirmed live.
All three are still fetched (per explicit user decision, after live
testing showed excluding policy would leave nothing but forms with no
download link) since a salesGroup's `formTypesActive` entry is trusted
as-is rather than filtered by this plugin; downstream extraction/matching
naturally cares only about real policy content.

Real policy docs (typeKey=1) are served with `content-type: application/pdf`
and real `%PDF-` bytes (confirmed live). Some administrative forms
(typeKey=2/3) are instead scanned images served with a misleading
`content-type: image/tiff` header even though the actual bytes are JPEG
(`\xff\xd8\xff` - confirmed live) - saved as-is with a `.pdf` extension
regardless. This isn't a bug to work around: PyMuPDF's `fitz.open()`
content-sniffs past the extension and wraps a single image as a 1-page
pseudo-PDF, so `PdfDocument.has_text_layer()` correctly reports False and
the existing OCR fallback (Stage 3) handles these pages exactly like any
other scanned document - confirmed live, no downloader changes needed.

No appendix-number field exists in this API's response (unlike
Phoenix/Clal/Menorah) - `formName` values look like "<product name>
<code>/<rev> - מהדורה <date>" (e.g. "195/01") rather than the "נספח
<number>" wording `find_appendix_numbers` looks for, so it's applied
defensively but is expected to return [] for most/all documents here;
that's fine, per the project's "trust the source, else backfill via LLM"
rule.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from companies.directinsurance.config import DOMAIN_TO_PRODUCT, DirectInsuranceConfig
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


@dataclass(frozen=True)
class DirectInsuranceDocumentRef:
    """One document listed in Direct Insurance's forms/policy archive."""

    domain: str  # "life" | "health"
    form_id: int
    title: str
    form_type: str  # typeDsc, e.g. "פוליסה וכתבי שירות"
    sale_group: str  # saleDsc, e.g. "מקרה מוות"
    appendix_numbers: list[str]
    download_url: str

    @property
    def local_filename(self) -> str:
        """formId is unique per document, so it alone is a safe filename -
        no collisions, no length concerns (unlike title-derived filenames)."""
        return f"{self.form_id}.pdf"


def refs_from_search_response(
    domain: str, body: dict[str, Any], config: DirectInsuranceConfig
) -> list[DirectInsuranceDocumentRef]:
    """Turn one `/sendformdata` JSON response into document refs.

    Pure function so the response shape is testable directly, without any
    network involved.
    """
    if body.get("status") != 0:
        logger.warning(
            "Direct Insurance %s search returned status=%s", domain, body.get("status")
        )
        return []

    refs = []
    for item in body.get("collection", []):
        form_id = item.get("formId")
        if form_id is None:
            continue
        title = (item.get("formName") or "").strip()
        refs.append(
            DirectInsuranceDocumentRef(
                domain=domain,
                form_id=form_id,
                title=title,
                form_type=item.get("typeDsc") or "",
                sale_group=item.get("saleDsc") or "",
                appendix_numbers=find_appendix_numbers(title),
                download_url=config.download_url_template.format(form_id=form_id),
            )
        )
    return refs


class DirectInsuranceDownloader(BaseDownloader):
    """Fetches and downloads Direct Insurance's health/life forms/policy archive."""

    def __init__(
        self, config: DirectInsuranceConfig, http_client: httpx.Client | None = None
    ) -> None:
        super().__init__(config)
        self._config: DirectInsuranceConfig = config
        self._client = http_client or httpx.Client(
            timeout=config.request_timeout_seconds,
            headers={"User-Agent": _BROWSER_USER_AGENT},
            follow_redirects=True,
        )

    def list_documents(self) -> list[DirectInsuranceDocumentRef]:
        """Fetch every document ref across every salesGroup sub-category of
        every tracked product (see DOMAIN_TO_PRODUCT), using the live
        taxonomy (salesGroup ids + valid form types) from formdata."""
        taxonomy = self._fetch_taxonomy()
        sale_groups_by_product = taxonomy.get("salesGroup", {})
        form_types_active = taxonomy.get("formTypesActive", {})

        refs: list[DirectInsuranceDocumentRef] = []
        seen_form_ids: set[int] = set()

        for domain, product_id in DOMAIN_TO_PRODUCT.items():
            for sale_group in sale_groups_by_product.get(product_id, []):
                sale_group_id = sale_group["key"]
                form_types = [t["key"] for t in form_types_active.get(sale_group_id, [])]
                if not form_types:
                    logger.warning(
                        "Direct Insurance %s saleGroup=%s (%s): no valid form types, skipping",
                        domain,
                        sale_group_id,
                        sale_group.get("dsc"),
                    )
                    continue

                for ref in self._search(domain, product_id, sale_group_id, form_types):
                    if ref.form_id not in seen_form_ids:
                        seen_form_ids.add(ref.form_id)
                        refs.append(ref)

                if self._config.listing_delay_seconds:
                    time.sleep(self._config.listing_delay_seconds)

        logger.info("Direct Insurance archive: %d documents found", len(refs))
        return refs

    def _fetch_taxonomy(self) -> dict[str, Any]:
        resp = self._client.get(self._config.formdata_url)
        resp.raise_for_status()
        body = resp.json()
        collection: dict[str, Any] = body.get("collection", {})
        return collection

    def _search(
        self, domain: str, product_id: str, sale_group_id: str, form_types: list[str]
    ) -> list[DirectInsuranceDocumentRef]:
        try:
            resp = self._client.post(
                self._config.search_url,
                json={
                    "product": product_id,
                    "saleGroup": sale_group_id,
                    "formType": form_types,
                    "active": "false",
                },
            )
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPError as exc:
            logger.warning(
                "Direct Insurance %s saleGroup=%s search failed: %s", domain, sale_group_id, exc
            )
            return []

        refs = refs_from_search_response(domain, body, self._config)
        logger.info(
            "Direct Insurance %s saleGroup=%s: %d documents", domain, sale_group_id, len(refs)
        )
        return refs

    def download_all(
        self,
        destination_dir: Path,
        limit: int | None = None,
        refs: list[DirectInsuranceDocumentRef] | None = None,
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

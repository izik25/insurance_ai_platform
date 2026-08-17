"""Downloads Migdal's public policy-terms archive, filtered to target domains.

Data flow (confirmed against the live site on 2026-07-15):
1. GET `list_endpoint?ListType=PolicyTermsFilesFolder&Source=media` returns a
   flat JSON array of every historical policy document Migdal has published,
   each tagged with a `Department` taxonomy term.
2. Each entry's `umbracoFile` (e.g. "/media/7875/130040900.pdf") is resolved
   to an actual PDF by prefixing it with `blob_base_url`.

Note: `front.migdal.co.il` (a different subdomain) sits behind an Incapsula
WAF that rejects plain HTTP calls. This endpoint, on `my.migdal.co.il`, does
not — it is reachable with a normal HTTP client, no browser needed.

Each item also carries `fromDate`/`ToDate` (ISO8601 UTC timestamps,
`ToDate` nullable) - confirmed live (2026-08-10) as a genuine per-file
validity window: `ToDate: null` means this specific PDF is the currently
active version, a real timestamp means it was superseded (verified against
real fromDate/ToDate chains where one entry's ToDate lines up with the
next's fromDate). Since every JSON item is already one specific historical
PDF file (not a policy-level record), no grouping/chain logic is needed
here - each `Document` row just reads its own item's ToDate directly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import httpx

from companies.migdal.config import MigdalConfig, classify_department
from core.exceptions import StorageError
from core.plugins.base import BaseDownloader
from core.storage.local import LocalFileStorage
from core.utils.hashing import sha256_of_bytes, sha256_of_file
from core.utils.logging import get_logger

logger = get_logger(__name__)

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
}


@dataclass(frozen=True)
class MigdalDocumentRef:
    """One document listed in Migdal's archive, after domain filtering."""

    media_folder_id: str
    original_filename: str
    policy_name: str
    department_name: str
    domain: str  # "health" | "life" | "mixed"
    download_url: str
    marketing_start_date: date | None = None  # "fromDate"
    marketing_end_date: date | None = None  # "ToDate" - null on-site == still active

    @property
    def is_active(self) -> bool:
        """Mirrors Document.is_active - True whenever there's no end date at
        all, or it hasn't passed yet."""
        return self.marketing_end_date is None or self.marketing_end_date >= date.today()

    @property
    def local_filename(self) -> str:
        return f"{self.media_folder_id}_{self.original_filename}"


def _parse_date(text: str | None) -> date | None:
    """Parse the API's ISO8601 UTC timestamps; missing/unparseable -> None
    (missing is the expected, meaningful case for ToDate: null == active)."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        logger.warning("Migdal: unparseable date %r", text)
        return None


class MigdalDownloader(BaseDownloader):
    """Fetches and downloads Migdal's health/life policy-terms documents."""

    def __init__(self, config: MigdalConfig, http_client: httpx.Client | None = None) -> None:
        super().__init__(config)
        self._config: MigdalConfig = config
        self._client = http_client or httpx.Client(
            timeout=config.request_timeout_seconds, headers=_BROWSER_HEADERS
        )

    def list_documents(self) -> list[MigdalDocumentRef]:
        """Fetch the full archive and return only health/life/mixed entries."""
        response = self._client.get(
            self._config.list_endpoint,
            params={"ListType": "PolicyTermsFilesFolder", "Source": "media"},
        )
        response.raise_for_status()
        payload = response.json()
        raw_items = payload.get("Data", [])

        refs: list[MigdalDocumentRef] = []
        for item in raw_items:
            ref = self._to_ref(item)
            if ref is not None:
                refs.append(ref)

        logger.info(
            "Migdal archive: %d total documents, %d match target domains",
            len(raw_items),
            len(refs),
        )
        return refs

    def _to_ref(self, item: dict) -> MigdalDocumentRef | None:
        departments = item.get("Department") or []
        domain: str | None = None
        department_name = ""
        for dept in departments:
            name = dept.get("_name", "")
            classified = classify_department(name)
            if classified is not None:
                domain = classified
                department_name = name
                break

        if domain is None:
            return None

        umbraco_file = item.get("umbracoFile", "")
        stripped = umbraco_file.replace("/media/", "", 1)
        if "/" in stripped:
            media_folder_id, filename = stripped.split("/", 1)
        else:
            media_folder_id, filename = "", stripped

        if not filename:
            logger.warning("Skipping item with no resolvable file path: %r", item)
            return None

        return MigdalDocumentRef(
            media_folder_id=media_folder_id,
            original_filename=filename,
            policy_name=item.get("policyName", ""),
            department_name=department_name,
            domain=domain,
            download_url=self._config.blob_base_url + stripped,
            marketing_start_date=_parse_date(item.get("fromDate")),
            marketing_end_date=_parse_date(item.get("ToDate")),
        )

    def download_all(self, destination_dir: Path, limit: int | None = None) -> list[Path]:
        """Download every target document, deduplicated by content hash.

        Dedup is seeded from files already on disk, so re-running against a
        partially populated destination_dir does not re-save content that
        was previously downloaded under a different original filename.
        """
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
                logger.info("Downloaded %s (%s)", relative_path, ref.policy_name)
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

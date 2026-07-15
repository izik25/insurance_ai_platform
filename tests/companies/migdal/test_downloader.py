"""Downloader tests run entirely against a mocked HTTP transport — no
requests ever reach the real Migdal servers during the test suite."""

from __future__ import annotations

from pathlib import Path

import httpx

from companies.migdal.config import MigdalConfig
from companies.migdal.downloader import MigdalDownloader

LIST_ENDPOINT = "https://fake-migdal.test/list"
BLOB_BASE_URL = "https://fake-migdal.test/blob/"

_LIST_PAYLOAD = {
    "Data": [
        {
            "umbracoFile": "/media/1001/health.pdf",
            "policyName": "Health Policy",
            "Department": [{"_name": "ביטוח בריאות וסיעוד"}],
        },
        {
            "umbracoFile": "/media/1002/life.pdf",
            "policyName": "Life Policy",
            "Department": [{"_name": "ביטוח למקרה מוות"}],
        },
        {
            "umbracoFile": "/media/1003/mixed.pdf",
            "policyName": "Group Policy",
            "Department": [{"_name": "קולקטיבים"}],
        },
        {
            "umbracoFile": "/media/1004/car.pdf",
            "policyName": "Car Policy",
            "Department": [{"_name": "ביטוח רכב"}],
        },
        {
            "umbracoFile": "/media/1005/dup.pdf",
            "policyName": "Duplicate of Health",
            "Department": [{"_name": "ביטוח בריאות וסיעוד"}],
        },
    ]
}

_BLOB_CONTENT = {
    "1001/health.pdf": b"HEALTH-CONTENT",
    "1002/life.pdf": b"LIFE-CONTENT",
    "1003/mixed.pdf": b"MIXED-CONTENT",
    "1005/dup.pdf": b"HEALTH-CONTENT",  # identical bytes to health.pdf -> deduped
}


def _make_config() -> MigdalConfig:
    return MigdalConfig(
        list_endpoint=LIST_ENDPOINT,
        blob_base_url=BLOB_BASE_URL,
        download_delay_seconds=0.0,
    )


def _make_downloader(blob_calls: list[str]) -> MigdalDownloader:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(LIST_ENDPOINT):
            return httpx.Response(200, json=_LIST_PAYLOAD)
        if url.startswith(BLOB_BASE_URL):
            key = url.removeprefix(BLOB_BASE_URL)
            blob_calls.append(key)
            if key == "1004/car.pdf":
                raise AssertionError("Non-target department must never be downloaded")
            if key in _BLOB_CONTENT:
                return httpx.Response(200, content=_BLOB_CONTENT[key])
            return httpx.Response(404)
        raise AssertionError(f"Unexpected request: {url}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return MigdalDownloader(_make_config(), http_client=client)


def test_list_documents_filters_to_target_departments() -> None:
    downloader = _make_downloader(blob_calls=[])
    refs = downloader.list_documents()

    domains = {ref.original_filename: ref.domain for ref in refs}
    assert domains == {
        "health.pdf": "health",
        "life.pdf": "life",
        "mixed.pdf": "mixed",
        "dup.pdf": "health",
    }
    assert "car.pdf" not in domains


def test_download_all_saves_and_deduplicates(tmp_path: Path) -> None:
    blob_calls: list[str] = []
    downloader = _make_downloader(blob_calls)

    saved = downloader.download_all(tmp_path)

    # dup.pdf has identical content to health.pdf -> not saved twice
    assert len(saved) == 3
    assert (tmp_path / "health" / "1001_health.pdf").read_bytes() == b"HEALTH-CONTENT"
    assert (tmp_path / "life" / "1002_life.pdf").read_bytes() == b"LIFE-CONTENT"
    assert (tmp_path / "mixed" / "1003_mixed.pdf").read_bytes() == b"MIXED-CONTENT"
    assert not (tmp_path / "health" / "1005_dup.pdf").exists()


def test_download_all_skips_already_downloaded_files(tmp_path: Path) -> None:
    blob_calls: list[str] = []
    downloader = _make_downloader(blob_calls)
    downloader.download_all(tmp_path)
    first_run_calls = list(blob_calls)
    blob_calls.clear()

    downloader.download_all(tmp_path)

    assert first_run_calls  # sanity: the first run did hit the network
    # health/life/mixed are skipped by path; only dup.pdf (never saved to
    # disk, since its content duplicated health.pdf) needs re-fetching to
    # confirm — by content hash — that it's still a duplicate.
    assert blob_calls == ["1005/dup.pdf"]
    assert not (tmp_path / "health" / "1005_dup.pdf").exists()


def test_download_all_respects_limit(tmp_path: Path) -> None:
    downloader = _make_downloader(blob_calls=[])
    saved = downloader.download_all(tmp_path, limit=1)
    assert len(saved) == 1


def test_download_all_continues_after_a_failed_download(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(LIST_ENDPOINT):
            return httpx.Response(200, json=_LIST_PAYLOAD)
        if url.endswith("1001/health.pdf"):
            return httpx.Response(500)
        if url.startswith(BLOB_BASE_URL):
            key = url.removeprefix(BLOB_BASE_URL)
            return httpx.Response(200, content=_BLOB_CONTENT.get(key, b"X"))
        raise AssertionError(f"Unexpected request: {url}")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = MigdalDownloader(_make_config(), http_client=client)

    saved = downloader.download_all(tmp_path)

    # health.pdf failed (500); dup.pdf has the same content but was never
    # deduped against it (the failed download never got hashed), so it
    # gets saved under its own name instead.
    assert len(saved) == 3
    assert not (tmp_path / "health" / "1001_health.pdf").exists()
    assert (tmp_path / "health" / "1005_dup.pdf").read_bytes() == b"HEALTH-CONTENT"

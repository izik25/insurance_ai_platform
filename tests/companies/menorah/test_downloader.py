"""Tests run without any real browser or network access: JSON-response
parsing is exercised as a pure function, and `download_all` against a
mocked HTTP transport with `list_documents` monkeypatched."""

from __future__ import annotations

from pathlib import Path

import httpx

from companies.menorah.config import MenorahConfig
from companies.menorah.downloader import (
    MenorahDocumentRef,
    MenorahDownloader,
    refs_from_search_response,
)


def _make_config(**overrides: object) -> MenorahConfig:
    defaults: dict[str, object] = {
        "download_delay_seconds": 0.0,
        "download_retry_base_seconds": 0.0,
        "listing_delay_seconds": 0.0,
    }
    defaults.update(overrides)
    return MenorahConfig(**defaults)  # type: ignore[arg-type]


def test_refs_from_search_response_parses_appendix_number_from_header() -> None:
    # Shaped like a real response captured live: appendix number embedded
    # in policyHeader text, not a separate structured field.
    body = {
        "err": None,
        "data": [
            {
                "_id": "6005a18ab9dabe000b22f7a4",
                "documentURL": "https://cdn.menoramivt.co.il/public/docs/20210303/923-1-1103.pdf",
                "lineOfBusiness": 5,
                "policyHeader": "THE FAMILY LIFE PROTECTOR, נספח 923",
                "policyHeaderForDisplay": "THE FAMILY LIFE PROTECTOR, נספח 923",
                "tags": ["נספח 923"],
            }
        ],
    }

    refs = refs_from_search_response("health", body)

    assert len(refs) == 1
    assert refs[0].appendix_numbers == ["923"]
    assert refs[0].title == "THE FAMILY LIFE PROTECTOR, נספח 923"
    assert refs[0].domain == "health"
    assert refs[0].download_url.endswith("923-1-1103.pdf")


def test_refs_from_search_response_falls_back_to_header_when_tags_empty() -> None:
    # Confirmed live: some real rows have an empty tags list even though
    # the appendix number is still embedded in policyHeader.
    body = {
        "err": None,
        "data": [
            {
                "documentURL": "https://cdn.menoramivt.co.il/public/docs/x/997.pdf",
                "policyHeader": "אופק רחב - שינויים בהגדרות ובכיסויים הביטוחיים, נספח 997",
                "policyHeaderForDisplay": "אופק רחב - שינויים בהגדרות ובכיסויים הביטוחיים, נספח 997",
                "tags": [],
            }
        ],
    }

    refs = refs_from_search_response("health", body)

    assert refs[0].appendix_numbers == ["997"]


def test_refs_from_search_response_dedupes_repeated_appendix_mentions() -> None:
    # Confirmed live: some rows tag the same number twice ("נספח 851" and
    # "גילוי נאות 851") - must not produce ["851", "851"].
    body = {
        "err": None,
        "data": [
            {
                "documentURL": "https://cdn.menoramivt.co.il/public/docs/x/851.pdf",
                "policyHeader": "אופק רחב, נספח 851",
                "tags": ["נספח 851", "גילוי נאות 851"],
            }
        ],
    }

    refs = refs_from_search_response("health", body)

    assert refs[0].appendix_numbers == ["851"]


def test_refs_from_search_response_skips_entries_without_document_url() -> None:
    body = {"err": None, "data": [{"policyHeader": "no url here"}]}

    refs = refs_from_search_response("health", body)

    assert refs == []


def test_refs_from_search_response_returns_empty_on_error() -> None:
    body = {"err": "something broke", "data": []}

    refs = refs_from_search_response("health", body)

    assert refs == []


def test_local_filename_derived_from_url() -> None:
    ref = MenorahDocumentRef(
        domain="health",
        title="נספח 100",
        appendix_numbers=["100"],
        download_url="https://cdn.menoramivt.co.il/public/docs/x/nispach-100.pdf",
    )
    assert ref.local_filename == "nispach-100.pdf"


def test_local_filename_caps_length_for_very_long_titles() -> None:
    long_name = "א" * 300 + ".pdf"
    ref = MenorahDocumentRef(
        domain="health",
        title="נספח ארוך",
        appendix_numbers=["100"],
        download_url=f"https://x/{long_name}",
    )
    assert len(ref.local_filename) <= 150
    assert ref.local_filename.endswith(".pdf")


def _make_downloader_with_refs(
    refs: list[MenorahDocumentRef], blob_calls: list[str], blob_content: dict[str, bytes]
) -> MenorahDownloader:
    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path.rsplit("/", 1)[-1]
        blob_calls.append(key)
        if key in blob_content:
            return httpx.Response(200, content=blob_content[key])
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = MenorahDownloader(_make_config(), http_client=client)
    downloader.list_documents = lambda: refs  # type: ignore[method-assign]
    return downloader


def test_download_all_saves_and_deduplicates(tmp_path: Path) -> None:
    refs = [
        MenorahDocumentRef("health", "A", ["100"], "http://x/a.pdf"),
        MenorahDocumentRef("health", "B", ["101"], "http://x/b.pdf"),
        MenorahDocumentRef("life", "C", ["200"], "http://x/c.pdf"),
    ]
    blob_content = {"a.pdf": b"CONTENT-A", "b.pdf": b"CONTENT-A", "c.pdf": b"CONTENT-C"}
    downloader = _make_downloader_with_refs(refs, [], blob_content)

    saved = downloader.download_all(tmp_path)

    assert len(saved) == 2
    assert (tmp_path / "health" / "a.pdf").read_bytes() == b"CONTENT-A"
    assert (tmp_path / "life" / "c.pdf").read_bytes() == b"CONTENT-C"
    assert not (tmp_path / "health" / "b.pdf").exists()


def test_download_all_skips_already_downloaded_files(tmp_path: Path) -> None:
    refs = [MenorahDocumentRef("health", "A", ["100"], "http://x/a.pdf")]
    blob_calls: list[str] = []
    downloader = _make_downloader_with_refs(refs, blob_calls, {"a.pdf": b"CONTENT-A"})

    downloader.download_all(tmp_path)
    blob_calls.clear()
    downloader.download_all(tmp_path)

    assert blob_calls == []


def test_download_all_respects_limit(tmp_path: Path) -> None:
    refs = [
        MenorahDocumentRef("health", "A", ["100"], "http://x/a.pdf"),
        MenorahDocumentRef("health", "B", ["101"], "http://x/b.pdf"),
    ]
    downloader = _make_downloader_with_refs(refs, [], {"a.pdf": b"A", "b.pdf": b"B"})

    saved = downloader.download_all(tmp_path, limit=1)

    assert len(saved) == 1


def test_download_all_continues_after_a_failed_download(tmp_path: Path) -> None:
    refs = [
        MenorahDocumentRef("health", "A", ["100"], "http://x/missing.pdf"),
        MenorahDocumentRef("health", "B", ["101"], "http://x/b.pdf"),
    ]
    downloader = _make_downloader_with_refs(refs, [], {"b.pdf": b"CONTENT-B"})

    saved = downloader.download_all(tmp_path)

    assert len(saved) == 1
    assert not (tmp_path / "health" / "missing.pdf").exists()
    assert (tmp_path / "health" / "b.pdf").read_bytes() == b"CONTENT-B"


def test_download_all_retries_transient_502_then_succeeds(tmp_path: Path) -> None:
    refs = [MenorahDocumentRef("health", "A", ["100"], "http://x/a.pdf")]
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            return httpx.Response(502)
        return httpx.Response(200, content=b"CONTENT-A")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = MenorahDownloader(_make_config(), http_client=client)
    downloader.list_documents = lambda: refs  # type: ignore[method-assign]

    saved = downloader.download_all(tmp_path)

    assert attempts["count"] == 3
    assert len(saved) == 1
    assert (tmp_path / "health" / "a.pdf").read_bytes() == b"CONTENT-A"


def test_download_all_does_not_retry_permanent_404(tmp_path: Path) -> None:
    refs = [MenorahDocumentRef("health", "A", ["100"], "http://x/a.pdf")]
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = MenorahDownloader(_make_config(), http_client=client)
    downloader.list_documents = lambda: refs  # type: ignore[method-assign]

    saved = downloader.download_all(tmp_path)

    assert attempts["count"] == 1
    assert saved == []

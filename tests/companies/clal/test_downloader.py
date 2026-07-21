"""Tests run without any real browser or network access: JSON-response
parsing is exercised as a pure function, and `download_all` against a
mocked HTTP transport with `list_documents` monkeypatched."""

from __future__ import annotations

from pathlib import Path

import httpx

from companies.clal.config import ClalConfig
from companies.clal.downloader import ClalDocumentRef, ClalDownloader, refs_from_search_response


def _make_config(**overrides: object) -> ClalConfig:
    defaults: dict[str, object] = {
        "download_delay_seconds": 0.0,
        "download_retry_base_seconds": 0.0,
        "listing_delay_seconds": 0.0,
    }
    defaults.update(overrides)
    return ClalConfig(**defaults)  # type: ignore[arg-type]


def test_refs_from_search_response_maps_policies() -> None:
    body = {
        "IsSuccess": True,
        "TotalResultCount": 2,
        "FamilyPoliciesDetails": [
            {
                "Family": "1520",
                "Policies": [
                    {
                        "CompanyDesc": "כלל ביטוח",
                        "AttachmentNumber": "2118",
                        "Title": "נספח בדיקה",
                        "PolicyTypeDesc": "פרטי",
                        "FilePath": "/media/1/a.pdf",
                    },
                    {
                        "CompanyDesc": "כלל ביטוח",
                        "AttachmentNumber": None,  # confirmed live: some real rows have this
                        "Title": "נספח ללא מספר",
                        "PolicyTypeDesc": "",
                        "FilePath": "/media/2/b.pdf",
                    },
                ],
            }
        ],
    }

    refs = refs_from_search_response("health", body, "https://example.com")

    assert len(refs) == 2
    assert refs[0] == ClalDocumentRef(
        domain="health",
        title="נספח בדיקה",
        appendix_number="2118",
        policy_type="פרטי",
        download_url="https://example.com/media/1/a.pdf",
    )
    # Null AttachmentNumber becomes "" (empty, not missing) - matches how the
    # DB-write step already treats an empty appendix_number list.
    assert refs[1].appendix_number == ""


def test_refs_from_search_response_skips_policies_without_file_path() -> None:
    body = {
        "IsSuccess": True,
        "FamilyPoliciesDetails": [
            {"Family": "1520", "Policies": [{"Title": "no file", "AttachmentNumber": "1"}]}
        ],
    }

    refs = refs_from_search_response("health", body, "https://example.com")

    assert refs == []


def test_refs_from_search_response_returns_empty_on_is_success_false() -> None:
    body = {"IsSuccess": False, "ErrorMessage": "boom"}

    refs = refs_from_search_response("health", body, "https://example.com")

    assert refs == []


def test_local_filename_derived_from_url() -> None:
    ref = ClalDocumentRef(
        domain="health",
        title="נספח 100",
        appendix_number="100",
        policy_type="פרטי",
        download_url="https://www.clalbit.co.il/media/1/nispach-100.pdf",
    )
    assert ref.local_filename == "nispach-100.pdf"


def test_local_filename_url_decodes_percent_encoded_names() -> None:
    ref = ClalDocumentRef(
        domain="health",
        title="נספח 100",
        appendix_number="100",
        policy_type="",
        download_url="https://x/%D7%92%D7%99%D7%9C%D7%95%D7%99%20100.pdf",
    )
    assert ref.local_filename == "גילוי 100.pdf"


def test_local_filename_caps_length_for_very_long_titles() -> None:
    long_name = "א" * 300 + ".pdf"
    ref = ClalDocumentRef(
        domain="health",
        title="נספח ארוך",
        appendix_number="100",
        policy_type="",
        download_url=f"https://x/{long_name}",
    )
    assert len(ref.local_filename) <= 150
    assert ref.local_filename.endswith(".pdf")


def _make_downloader_with_refs(
    refs: list[ClalDocumentRef], blob_calls: list[str], blob_content: dict[str, bytes]
) -> ClalDownloader:
    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path.rsplit("/", 1)[-1]
        blob_calls.append(key)
        if key in blob_content:
            return httpx.Response(200, content=blob_content[key])
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = ClalDownloader(_make_config(), http_client=client)
    downloader.list_documents = lambda: refs  # type: ignore[method-assign]
    return downloader


def test_download_all_saves_and_deduplicates(tmp_path: Path) -> None:
    refs = [
        ClalDocumentRef("health", "A", "100", "פרטי", "http://x/a.pdf"),
        ClalDocumentRef("health", "B", "101", "פרטי", "http://x/b.pdf"),
        ClalDocumentRef("life", "C", "200", "פרטי", "http://x/c.pdf"),
    ]
    blob_content = {"a.pdf": b"CONTENT-A", "b.pdf": b"CONTENT-A", "c.pdf": b"CONTENT-C"}
    downloader = _make_downloader_with_refs(refs, [], blob_content)

    saved = downloader.download_all(tmp_path)

    # b.pdf has identical content to a.pdf -> deduped
    assert len(saved) == 2
    assert (tmp_path / "health" / "a.pdf").read_bytes() == b"CONTENT-A"
    assert (tmp_path / "life" / "c.pdf").read_bytes() == b"CONTENT-C"
    assert not (tmp_path / "health" / "b.pdf").exists()


def test_download_all_skips_already_downloaded_files(tmp_path: Path) -> None:
    refs = [ClalDocumentRef("health", "A", "100", "פרטי", "http://x/a.pdf")]
    blob_calls: list[str] = []
    downloader = _make_downloader_with_refs(refs, blob_calls, {"a.pdf": b"CONTENT-A"})

    downloader.download_all(tmp_path)
    blob_calls.clear()
    downloader.download_all(tmp_path)

    assert blob_calls == []


def test_download_all_respects_limit(tmp_path: Path) -> None:
    refs = [
        ClalDocumentRef("health", "A", "100", "פרטי", "http://x/a.pdf"),
        ClalDocumentRef("health", "B", "101", "פרטי", "http://x/b.pdf"),
    ]
    downloader = _make_downloader_with_refs(refs, [], {"a.pdf": b"A", "b.pdf": b"B"})

    saved = downloader.download_all(tmp_path, limit=1)

    assert len(saved) == 1


def test_download_all_continues_after_a_failed_download(tmp_path: Path) -> None:
    refs = [
        ClalDocumentRef("health", "A", "100", "פרטי", "http://x/missing.pdf"),
        ClalDocumentRef("health", "B", "101", "פרטי", "http://x/b.pdf"),
    ]
    downloader = _make_downloader_with_refs(refs, [], {"b.pdf": b"CONTENT-B"})

    saved = downloader.download_all(tmp_path)

    assert len(saved) == 1
    assert not (tmp_path / "health" / "missing.pdf").exists()
    assert (tmp_path / "health" / "b.pdf").read_bytes() == b"CONTENT-B"


def test_download_all_retries_transient_502_then_succeeds(tmp_path: Path) -> None:
    refs = [ClalDocumentRef("health", "A", "100", "פרטי", "http://x/a.pdf")]
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            return httpx.Response(502)
        return httpx.Response(200, content=b"CONTENT-A")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = ClalDownloader(_make_config(), http_client=client)
    downloader.list_documents = lambda: refs  # type: ignore[method-assign]

    saved = downloader.download_all(tmp_path)

    assert attempts["count"] == 3
    assert len(saved) == 1
    assert (tmp_path / "health" / "a.pdf").read_bytes() == b"CONTENT-A"


def test_download_all_does_not_retry_permanent_404(tmp_path: Path) -> None:
    refs = [ClalDocumentRef("health", "A", "100", "פרטי", "http://x/a.pdf")]
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = ClalDownloader(_make_config(), http_client=client)
    downloader.list_documents = lambda: refs  # type: ignore[method-assign]

    saved = downloader.download_all(tmp_path)

    assert attempts["count"] == 1
    assert saved == []

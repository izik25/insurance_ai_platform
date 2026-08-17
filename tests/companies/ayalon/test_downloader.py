"""Tests run without any real browser or network access: JSON-response
parsing is exercised as a pure function, and `download_all` against a
mocked HTTP transport with `list_documents` monkeypatched."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx

from companies.ayalon.config import AyalonConfig
from companies.ayalon.downloader import (
    AyalonDocumentRef,
    AyalonDownloader,
    refs_from_search_response,
)


def _make_config(**overrides: object) -> AyalonConfig:
    defaults: dict[str, object] = {
        "download_delay_seconds": 0.0,
        "download_retry_base_seconds": 0.0,
    }
    defaults.update(overrides)
    return AyalonConfig(**defaults)  # type: ignore[arg-type]


def _item(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "policyName": "נספח 1326 - גילוי נאות",
        "subjectId": "59a0fa91-841d-49c7-9198-77ebff76bfbc",
        "subjectName": "פוליסה",
        "categoryId": "9862b72f-59dd-4d28-acbc-b81b6220b76f",
        "categoryName": "ביטוח בריאות",
        "fromDate": "2026-05-01T00:00:00",
        "endDate": None,
        "isActive": True,
        "fileUrl": "/media/w4jjlvtf/br_gn_1326.pdf",
    }
    base.update(overrides)
    return base


def test_refs_from_search_response_parses_appendix_number_from_title() -> None:
    body = {"totalCount": 1, "items": [_item()]}

    refs = refs_from_search_response(body)

    assert len(refs) == 1
    assert refs[0].appendix_numbers == ["1326"]
    assert refs[0].domain == "health"
    assert refs[0].download_url == "https://www.ayalon-ins.co.il/media/w4jjlvtf/br_gn_1326.pdf"


def test_refs_from_search_response_maps_life_category() -> None:
    body = {"totalCount": 1, "items": [_item(categoryName="ביטוח חיים")]}

    refs = refs_from_search_response(body)

    assert refs[0].domain == "life"


def test_refs_from_search_response_skips_out_of_scope_category() -> None:
    body = {"totalCount": 1, "items": [_item(categoryName="ביטוח רכב", subjectName="פוליסה")]}

    refs = refs_from_search_response(body)

    assert refs == []


def test_refs_from_search_response_keeps_collective_relevant_subjects() -> None:
    body = {
        "totalCount": 2,
        "items": [
            _item(categoryName="ביטוח קולקטיב", subjectName="חיים"),
            _item(categoryName="ביטוח קולקטיב", subjectName="בריאות"),
        ],
    }

    refs = refs_from_search_response(body)

    assert len(refs) == 2
    assert {r.domain for r in refs} == {"life", "health"}


def test_refs_from_search_response_drops_collective_home_and_car() -> None:
    body = {
        "totalCount": 2,
        "items": [
            _item(categoryName="ביטוח קולקטיב", subjectName="דירה"),
            _item(categoryName="ביטוח קולקטיב", subjectName="רכב"),
        ],
    }

    refs = refs_from_search_response(body)

    assert refs == []


def test_refs_from_search_response_skips_entries_without_file_url() -> None:
    body = {"totalCount": 1, "items": [_item(fileUrl=None)]}

    refs = refs_from_search_response(body)

    assert refs == []


def test_refs_from_search_response_parses_marketing_dates() -> None:
    body = {
        "totalCount": 1,
        "items": [_item(fromDate="2020-01-01T00:00:00", endDate="2022-06-01T00:00:00")],
    }

    refs = refs_from_search_response(body)

    assert refs[0].marketing_start_date == date(2020, 1, 1)
    assert refs[0].marketing_end_date == date(2022, 6, 1)
    assert refs[0].is_active is False


def test_refs_from_search_response_null_end_date_is_active() -> None:
    body = {"totalCount": 1, "items": [_item(fromDate="2026-05-01T00:00:00", endDate=None)]}

    refs = refs_from_search_response(body)

    assert refs[0].marketing_end_date is None
    assert refs[0].is_active is True


def test_department_name_combines_category_and_subject() -> None:
    ref = AyalonDocumentRef(
        domain="health",
        title="נספח 1",
        appendix_numbers=["1"],
        category_name="ביטוח בריאות",
        subject_name="פוליסה",
        download_url="https://x/a.pdf",
    )
    assert ref.department_name == "ביטוח בריאות / פוליסה"


def test_local_filename_derived_from_url() -> None:
    ref = AyalonDocumentRef(
        domain="health",
        title="נספח 100",
        appendix_numbers=["100"],
        category_name="ביטוח בריאות",
        subject_name="נספח",
        download_url="https://www.ayalon-ins.co.il/media/xyz/nispach-100.pdf",
    )
    assert ref.local_filename == "nispach-100.pdf"


def test_local_filename_caps_length_for_very_long_titles() -> None:
    long_name = "א" * 300 + ".pdf"
    ref = AyalonDocumentRef(
        domain="health",
        title="נספח ארוך",
        appendix_numbers=["100"],
        category_name="ביטוח בריאות",
        subject_name="נספח",
        download_url=f"https://x/{long_name}",
    )
    assert len(ref.local_filename) <= 150
    assert ref.local_filename.endswith(".pdf")


def _make_downloader_with_refs(
    refs: list[AyalonDocumentRef], blob_content: dict[str, bytes]
) -> AyalonDownloader:
    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path.rsplit("/", 1)[-1]
        if key in blob_content:
            return httpx.Response(200, content=blob_content[key])
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = AyalonDownloader(_make_config(), http_client=client)
    downloader.list_documents = lambda: refs  # type: ignore[method-assign]
    return downloader


def _ref(domain: str, title: str, url: str) -> AyalonDocumentRef:
    return AyalonDocumentRef(
        domain=domain,
        title=title,
        appendix_numbers=[],
        category_name="ביטוח בריאות",
        subject_name="פוליסה",
        download_url=url,
    )


def test_download_all_saves_and_deduplicates(tmp_path: Path) -> None:
    refs = [
        _ref("health", "A", "http://x/a.pdf"),
        _ref("health", "B", "http://x/b.pdf"),
        _ref("life", "C", "http://x/c.pdf"),
    ]
    blob_content = {"a.pdf": b"CONTENT-A", "b.pdf": b"CONTENT-A", "c.pdf": b"CONTENT-C"}
    downloader = _make_downloader_with_refs(refs, blob_content)

    saved = downloader.download_all(tmp_path)

    assert len(saved) == 2
    assert (tmp_path / "health" / "a.pdf").read_bytes() == b"CONTENT-A"
    assert (tmp_path / "life" / "c.pdf").read_bytes() == b"CONTENT-C"
    assert not (tmp_path / "health" / "b.pdf").exists()


def test_download_all_respects_limit(tmp_path: Path) -> None:
    refs = [_ref("health", "A", "http://x/a.pdf"), _ref("health", "B", "http://x/b.pdf")]
    downloader = _make_downloader_with_refs(refs, {"a.pdf": b"A", "b.pdf": b"B"})

    saved = downloader.download_all(tmp_path, limit=1)

    assert len(saved) == 1


def test_download_all_does_not_retry_permanent_404(tmp_path: Path) -> None:
    refs = [_ref("health", "A", "http://x/missing.pdf")]
    downloader = _make_downloader_with_refs(refs, {})

    saved = downloader.download_all(tmp_path)

    assert saved == []

"""Tests run without any real network access: JSON-response parsing is
exercised as a pure function, and both `list_documents`/`download_all`
against a mocked HTTP transport."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from companies.directinsurance.config import DirectInsuranceConfig
from companies.directinsurance.downloader import (
    DirectInsuranceDocumentRef,
    DirectInsuranceDownloader,
    refs_from_search_response,
)


def _make_config(**overrides: object) -> DirectInsuranceConfig:
    defaults: dict[str, object] = {
        "download_delay_seconds": 0.0,
        "download_retry_base_seconds": 0.0,
        "listing_delay_seconds": 0.0,
    }
    defaults.update(overrides)
    return DirectInsuranceConfig(**defaults)  # type: ignore[arg-type]


def test_refs_from_search_response_parses_real_shaped_entry() -> None:
    # Shaped like a real response captured live.
    body = {
        "status": 0,
        "collection": [
            {
                "formId": 9214,
                "formName": "כסוי בטוח למקרה מוות או גילוי מחלה ממארת 195/01 - מהדורה 08/2021",
                "typeDsc": "פוליסה וכתבי שירות",
                "typeKey": "1",
                "productDsc": "ביטוח חיים",
                "productKey": "7",
                "saleDsc": "מקרה מוות",
                "saleKey": "14",
                "active": False,
                "fromDate": "26/08/2021",
                "toDate": "",
            }
        ],
    }
    config = _make_config()

    refs = refs_from_search_response("life", body, config)

    assert len(refs) == 1
    ref = refs[0]
    assert ref.form_id == 9214
    assert ref.domain == "life"
    assert ref.form_type == "פוליסה וכתבי שירות"
    assert ref.sale_group == "מקרה מוות"
    assert ref.download_url == "https://www.555.co.il/webapp/api/siteapi/form/openform/9214"
    # This site's formName wording doesn't use "נספח <n>" - defensively
    # parsed but expected empty here.
    assert ref.appendix_numbers == []


def test_refs_from_search_response_parses_appendix_number_when_present() -> None:
    body = {
        "status": 0,
        "collection": [
            {
                "formId": 1,
                "formName": "תנאים כלליים - נספח 923",
                "typeDsc": "פוליסה וכתבי שירות",
                "saleDsc": "מקרה מוות",
            }
        ],
    }

    refs = refs_from_search_response("life", body, _make_config())

    assert refs[0].appendix_numbers == ["923"]


def test_refs_from_search_response_skips_entries_without_form_id() -> None:
    body = {"status": 0, "collection": [{"formName": "no id here"}]}

    refs = refs_from_search_response("life", body, _make_config())

    assert refs == []


def test_refs_from_search_response_returns_empty_on_nonzero_status() -> None:
    body = {"status": 1, "collection": []}

    refs = refs_from_search_response("life", body, _make_config())

    assert refs == []


def test_local_filename_derived_from_form_id() -> None:
    ref = DirectInsuranceDocumentRef(
        domain="life",
        form_id=9214,
        title="x",
        form_type="פוליסה וכתבי שירות",
        sale_group="מקרה מוות",
        appendix_numbers=[],
        download_url="https://www.555.co.il/webapp/api/siteapi/form/openform/9214",
    )
    assert ref.local_filename == "9214.pdf"


_TAXONOMY = {
    "status": 0,
    "collection": {
        "salesGroup": {
            "7": [
                {"key": "14", "dsc": "מקרה מוות"},
                {"key": "24", "dsc": "אובדן כושר עבודה"},
            ],
            "8": [{"key": "23", "dsc": "מחלות קשות"}],
        },
        "formTypesActive": {
            "14": [
                {"key": "3", "dsc": "טפסי תביעות"},
                {"key": "2", "dsc": "טפסי שירות"},
                {"key": "1", "dsc": "פוליסה וכתבי שירות"},
            ],
            "24": [{"key": "2", "dsc": "טפסי שירות"}],
            "23": [{"key": "1", "dsc": "פוליסה וכתבי שירות"}],
        },
    },
}


def _make_downloader_with_taxonomy(
    search_responses: dict[str, dict[str, object]],
    calls: list[tuple[str, str]],
) -> DirectInsuranceDownloader:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "formdata" in request.url.path:
            return httpx.Response(200, json=_TAXONOMY)
        if request.method == "POST" and "sendformdata" in request.url.path:
            payload = json.loads(request.content)
            sale_group = payload["saleGroup"]
            calls.append((sale_group, ",".join(payload["formType"])))
            return httpx.Response(200, json=search_responses.get(sale_group, {"status": 0, "collection": []}))
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return DirectInsuranceDownloader(_make_config(), http_client=client)


def test_list_documents_queries_every_sale_group_with_all_valid_form_types() -> None:
    calls: list[tuple[str, str]] = []
    search_responses = {
        "14": {
            "status": 0,
            "collection": [
                {"formId": 1, "formName": "A", "typeDsc": "פוליסה", "saleDsc": "מקרה מוות"}
            ],
        },
        "24": {
            "status": 0,
            "collection": [
                {"formId": 2, "formName": "B", "typeDsc": "טפסי שירות", "saleDsc": "אובדן כושר עבודה"}
            ],
        },
    }
    downloader = _make_downloader_with_taxonomy(search_responses, calls)

    refs = downloader.list_documents()

    assert len(refs) == 2
    assert {ref.form_id for ref in refs} == {1, 2}
    calls_by_group = dict(calls)
    assert set(calls_by_group["14"].split(",")) == {"1", "2", "3"}
    assert calls_by_group["24"] == "2"


def test_list_documents_queries_health_product_too() -> None:
    calls: list[tuple[str, str]] = []
    search_responses = {
        "23": {
            "status": 0,
            "collection": [
                {"formId": 3, "formName": "C", "typeDsc": "פוליסה", "saleDsc": "מחלות קשות"}
            ],
        },
    }
    downloader = _make_downloader_with_taxonomy(search_responses, calls)

    refs = downloader.list_documents()

    refs_by_id = {ref.form_id: ref for ref in refs}
    assert refs_by_id[3].domain == "health"


def test_list_documents_dedupes_repeated_form_ids() -> None:
    calls: list[tuple[str, str]] = []
    same_doc = {"formId": 1, "formName": "A", "typeDsc": "פוליסה", "saleDsc": "מקרה מוות"}
    search_responses = {
        "14": {"status": 0, "collection": [same_doc]},
        "24": {"status": 0, "collection": [same_doc]},
    }
    downloader = _make_downloader_with_taxonomy(search_responses, calls)

    refs = downloader.list_documents()

    assert len(refs) == 1


def _make_downloader_with_refs(
    refs: list[DirectInsuranceDocumentRef], blob_calls: list[str], blob_content: dict[str, bytes]
) -> DirectInsuranceDownloader:
    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path.rsplit("/", 1)[-1]
        blob_calls.append(key)
        if key in blob_content:
            return httpx.Response(200, content=blob_content[key])
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = DirectInsuranceDownloader(_make_config(), http_client=client)
    downloader.list_documents = lambda: refs  # type: ignore[method-assign]
    return downloader


def _ref(form_id: int, domain: str = "life") -> DirectInsuranceDocumentRef:
    return DirectInsuranceDocumentRef(
        domain=domain,
        form_id=form_id,
        title=f"title {form_id}",
        form_type="פוליסה וכתבי שירות",
        sale_group="מקרה מוות",
        appendix_numbers=[],
        download_url=f"https://www.555.co.il/webapp/api/siteapi/form/openform/{form_id}",
    )


def test_download_all_saves_and_deduplicates(tmp_path: Path) -> None:
    refs = [_ref(1), _ref(2), _ref(3, domain="health")]
    blob_content = {"1": b"CONTENT-A", "2": b"CONTENT-A", "3": b"CONTENT-C"}
    downloader = _make_downloader_with_refs(refs, [], blob_content)

    saved = downloader.download_all(tmp_path)

    assert len(saved) == 2
    assert (tmp_path / "life" / "1.pdf").read_bytes() == b"CONTENT-A"
    assert (tmp_path / "health" / "3.pdf").read_bytes() == b"CONTENT-C"
    assert not (tmp_path / "life" / "2.pdf").exists()


def test_download_all_skips_already_downloaded_files(tmp_path: Path) -> None:
    refs = [_ref(1)]
    blob_calls: list[str] = []
    downloader = _make_downloader_with_refs(refs, blob_calls, {"1": b"CONTENT-A"})

    downloader.download_all(tmp_path)
    blob_calls.clear()
    downloader.download_all(tmp_path)

    assert blob_calls == []


def test_download_all_respects_limit(tmp_path: Path) -> None:
    refs = [_ref(1), _ref(2)]
    downloader = _make_downloader_with_refs(refs, [], {"1": b"A", "2": b"B"})

    saved = downloader.download_all(tmp_path, limit=1)

    assert len(saved) == 1


def test_download_all_continues_after_a_failed_download(tmp_path: Path) -> None:
    refs = [_ref(1), _ref(2)]
    downloader = _make_downloader_with_refs(refs, [], {"2": b"CONTENT-B"})

    saved = downloader.download_all(tmp_path)

    assert len(saved) == 1
    assert not (tmp_path / "life" / "1.pdf").exists()
    assert (tmp_path / "life" / "2.pdf").read_bytes() == b"CONTENT-B"


def test_download_all_retries_transient_502_then_succeeds(tmp_path: Path) -> None:
    refs = [_ref(1)]
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            return httpx.Response(502)
        return httpx.Response(200, content=b"CONTENT-A")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = DirectInsuranceDownloader(_make_config(), http_client=client)
    downloader.list_documents = lambda: refs  # type: ignore[method-assign]

    saved = downloader.download_all(tmp_path)

    assert attempts["count"] == 3
    assert len(saved) == 1
    assert (tmp_path / "life" / "1.pdf").read_bytes() == b"CONTENT-A"


def test_download_all_does_not_retry_permanent_404(tmp_path: Path) -> None:
    refs = [_ref(1)]
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = DirectInsuranceDownloader(_make_config(), http_client=client)
    downloader.list_documents = lambda: refs  # type: ignore[method-assign]

    saved = downloader.download_all(tmp_path)

    assert attempts["count"] == 1
    assert saved == []

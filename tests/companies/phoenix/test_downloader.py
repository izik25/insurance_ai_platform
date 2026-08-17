"""Tests run without any real browser or network access: `_list_domain` is
exercised against a fake Playwright Page stub, and `download_all` against
a mocked HTTP transport with `list_documents` monkeypatched."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest

from companies.phoenix.config import PhoenixConfig
from companies.phoenix.downloader import (
    _NO_RESULTS_MARKER,
    PhoenixDocumentRef,
    PhoenixDownloader,
    with_marketing_dates,
)


class _FakeContext:
    def close(self) -> None:
        pass


class _FakePage:
    """Returns canned rows per call, simulating pagination.

    Once `pages` is exhausted, `content()` reports the site's real
    "no results" marker so `_fetch_page_rows` treats it as a genuine end
    of pagination instead of retrying (which would sleep in real time).
    """

    def __init__(self, pages: list[list[dict[str, str]]]) -> None:
        self._pages = pages
        self.goto_calls: list[str] = []
        self._call_index = 0
        self.context = _FakeContext()

    def goto(self, url: str, wait_until: str = "load", timeout: int = 30000) -> None:
        self.goto_calls.append(url)

    def eval_on_selector_all(self, selector: str, script: str) -> list[dict[str, str]]:
        if self._call_index >= len(self._pages):
            return []
        rows = self._pages[self._call_index]
        self._call_index += 1
        return rows

    def eval_on_selector(self, selector: str, script: str) -> str | None:
        return None  # no pager "last page" hint in these fakes

    def content(self) -> str:
        return _NO_RESULTS_MARKER


def _make_config(**overrides: object) -> PhoenixConfig:
    defaults: dict[str, object] = {
        "download_delay_seconds": 0.0,
        "listing_page_delay_seconds": 0.0,
        "listing_retry_base_seconds": 0.0,
        "download_retry_base_seconds": 0.0,
    }
    defaults.update(overrides)
    return PhoenixConfig(**defaults)  # type: ignore[arg-type]


def test_page_url_contains_expected_query_params() -> None:
    downloader = PhoenixDownloader(_make_config())
    url = downloader._page_url("HealthInsCovers", 3)
    assert url.startswith("https://www.fnx.co.il/spf/Iframe_FormsConditions.aspx?")
    assert "world=HealthInsCovers" in url
    assert "page=3" in url
    assert "company=" in url


def _row(number: str) -> dict[str, str]:
    return {
        "title": f"נספח {number}",
        "href": f"http://x/{number}.pdf",
        "appendix_number": number,
        "edition": "01/26",
    }


def test_list_domain_paginates_until_empty_page() -> None:
    page_one = [_row("100"), _row("101")]
    page_two = [_row("102")]
    fake_page = _FakePage([page_one, page_two])
    downloader = PhoenixDownloader(_make_config())

    refs = downloader._list_domain_cover(
        None, fake_page, "health", "HealthInsCovers", ""  # type: ignore[arg-type]
    )

    assert [r.appendix_number for r in refs] == ["100", "101", "102"]
    assert all(r.domain == "health" for r in refs)
    # 3 goto calls: page 1, page 2, page 3 (which comes back empty and stops the loop)
    assert len(fake_page.goto_calls) == 3


def test_list_domain_returns_empty_when_first_page_empty() -> None:
    fake_page = _FakePage([[]])
    downloader = PhoenixDownloader(_make_config())

    refs = downloader._list_domain_cover(
        None, fake_page, "life", "LifeInsCovers", ""  # type: ignore[arg-type]
    )

    assert refs == []
    assert len(fake_page.goto_calls) == 1


def test_list_domain_dedupes_across_covers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Health queries multiple cover sub-categories and must dedupe by URL.

    (Confirmed live: an unfiltered cover="" query gets stuck on an
    unreliable page 10 for zero new documents - its results are a strict
    subset of the sub-categories - so it's deliberately excluded. Iterating
    sub-categories reaches every document reliably, but the same document
    can show up under more than one cover, so results must be deduplicated
    by download_url.)
    """
    calls: list[str] = []

    def fake_list_domain_cover(
        self: PhoenixDownloader,
        browser: object,
        page: object,
        domain: str,
        world: str,
        cover: str,
        max_pages: int | None = None,
    ) -> list[PhoenixDocumentRef]:
        calls.append(cover)
        by_cover = {
            "אמבלוטורי": [PhoenixDocumentRef("health", "נספח 100", "100", "01/26", "http://x/100.pdf")],
            "סיעוד": [
                PhoenixDocumentRef("health", "נספח 100", "100", "01/26", "http://x/100.pdf"),
                PhoenixDocumentRef("health", "נספח 200", "200", "01/26", "http://x/200.pdf"),
            ],
        }
        return by_cover.get(cover, [])

    monkeypatch.setattr(PhoenixDownloader, "_list_domain_cover", fake_list_domain_cover)
    monkeypatch.setattr(PhoenixDownloader, "_new_page", lambda self, browser: None)

    downloader = PhoenixDownloader(_make_config())
    refs = downloader._list_domain(None, "health", "HealthInsCovers")  # type: ignore[arg-type]

    assert sorted(calls) == sorted(
        [
            "אמבלוטורי",
            "גנטיקס",
            "היתר עסקא",
            "השתלות",
            "כיסויים נוספים",
            "כתבי שירות",
            "מחלות קשות",
            "ניתוחים",
            "ניתוחים משולב",
            "סיעוד",
            "עובדים זרים",
            "רפואה משלימה",
            "שיניים",
            "תאונות אישיות",
            "תרופות",
        ]
    )
    assert sorted(r.download_url for r in refs) == ["http://x/100.pdf", "http://x/200.pdf"]


def test_local_filename_derived_from_url() -> None:
    ref = PhoenixDocumentRef(
        domain="health",
        title="נספח 100",
        appendix_number="100",
        edition="01/26",
        download_url="http://www.fnx.co.il/sites/docs/polarchive/healthinsurance/nispach-100.pdf",
    )
    assert ref.local_filename == "nispach-100.pdf"


def test_local_filename_url_decodes_percent_encoded_names() -> None:
    ref = PhoenixDocumentRef(
        domain="health",
        title="נספח 100",
        appendix_number="100",
        edition="01/26",
        download_url="http://x/%D7%92%D7%99%D7%9C%D7%95%D7%99%20100.pdf",
    )
    assert ref.local_filename == "גילוי 100.pdf"


def test_local_filename_caps_length_for_very_long_titles() -> None:
    long_name = "א" * 300 + ".pdf"
    ref = PhoenixDocumentRef(
        domain="health",
        title="נספח ארוך",
        appendix_number="100",
        edition="01/26",
        download_url=f"http://x/{long_name}",
    )
    assert len(ref.local_filename) <= 150
    assert ref.local_filename.endswith(".pdf")


def _make_downloader_with_refs(
    refs: list[PhoenixDocumentRef], blob_calls: list[str], blob_content: dict[str, bytes]
) -> PhoenixDownloader:
    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path.rsplit("/", 1)[-1]
        blob_calls.append(key)
        if key in blob_content:
            return httpx.Response(200, content=blob_content[key])
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = PhoenixDownloader(_make_config(), http_client=client)
    downloader.list_documents = lambda: refs  # type: ignore[method-assign]
    return downloader


def test_download_all_saves_and_deduplicates(tmp_path: Path) -> None:
    refs = [
        PhoenixDocumentRef("health", "A", "100", "01/26", "http://x/a.pdf"),
        PhoenixDocumentRef("health", "B", "101", "01/26", "http://x/b.pdf"),
        PhoenixDocumentRef("life", "C", "200", "01/26", "http://x/c.pdf"),
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
    refs = [PhoenixDocumentRef("health", "A", "100", "01/26", "http://x/a.pdf")]
    blob_calls: list[str] = []
    downloader = _make_downloader_with_refs(refs, blob_calls, {"a.pdf": b"CONTENT-A"})

    downloader.download_all(tmp_path)
    blob_calls.clear()
    downloader.download_all(tmp_path)

    assert blob_calls == []


def test_download_all_respects_limit(tmp_path: Path) -> None:
    refs = [
        PhoenixDocumentRef("health", "A", "100", "01/26", "http://x/a.pdf"),
        PhoenixDocumentRef("health", "B", "101", "01/26", "http://x/b.pdf"),
    ]
    downloader = _make_downloader_with_refs(refs, [], {"a.pdf": b"A", "b.pdf": b"B"})

    saved = downloader.download_all(tmp_path, limit=1)

    assert len(saved) == 1


def test_download_all_continues_after_a_failed_download(tmp_path: Path) -> None:
    refs = [
        PhoenixDocumentRef("health", "A", "100", "01/26", "http://x/missing.pdf"),
        PhoenixDocumentRef("health", "B", "101", "01/26", "http://x/b.pdf"),
    ]
    downloader = _make_downloader_with_refs(refs, [], {"b.pdf": b"CONTENT-B"})

    saved = downloader.download_all(tmp_path)

    assert len(saved) == 1
    assert not (tmp_path / "health" / "missing.pdf").exists()
    assert (tmp_path / "health" / "b.pdf").read_bytes() == b"CONTENT-B"


def test_download_all_retries_transient_502_then_succeeds(tmp_path: Path) -> None:
    refs = [PhoenixDocumentRef("health", "A", "100", "01/26", "http://x/a.pdf")]
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            return httpx.Response(502)
        return httpx.Response(200, content=b"CONTENT-A")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = PhoenixDownloader(_make_config(), http_client=client)
    downloader.list_documents = lambda: refs  # type: ignore[method-assign]

    saved = downloader.download_all(tmp_path)

    assert attempts["count"] == 3
    assert len(saved) == 1
    assert (tmp_path / "health" / "a.pdf").read_bytes() == b"CONTENT-A"


def test_download_all_does_not_retry_permanent_404(tmp_path: Path) -> None:
    refs = [PhoenixDocumentRef("health", "A", "100", "01/26", "http://x/a.pdf")]
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = PhoenixDownloader(_make_config(), http_client=client)
    downloader.list_documents = lambda: refs  # type: ignore[method-assign]

    saved = downloader.download_all(tmp_path)

    assert attempts["count"] == 1
    assert saved == []


def test_with_marketing_dates_newest_edition_has_no_end_date() -> None:
    refs = [
        PhoenixDocumentRef("health", "old", "6975", "01/24", "http://x/old.pdf"),
        PhoenixDocumentRef("health", "new", "6975", "05/26", "http://x/new.pdf"),
    ]

    result = with_marketing_dates(refs)

    by_title = {r.title: r for r in result}
    assert by_title["new"].marketing_start_date == date(2026, 5, 1)
    assert by_title["new"].marketing_end_date is None


def test_with_marketing_dates_older_edition_ends_before_the_next() -> None:
    refs = [
        PhoenixDocumentRef("health", "old", "6975", "01/24", "http://x/old.pdf"),
        PhoenixDocumentRef("health", "new", "6975", "05/26", "http://x/new.pdf"),
    ]

    result = with_marketing_dates(refs)

    by_title = {r.title: r for r in result}
    assert by_title["old"].marketing_start_date == date(2024, 1, 1)
    assert by_title["old"].marketing_end_date == date(2026, 4, 30)


def test_with_marketing_dates_nispach_gilui_pair_shares_active_edition() -> None:
    """Two files, same appendix + same (highest) edition - both active, not
    one superseding the other."""
    refs = [
        PhoenixDocumentRef("health", "nispach", "6975", "05/26", "http://x/nispach.pdf"),
        PhoenixDocumentRef("health", "gilui", "6975", "05/26", "http://x/gilui.pdf"),
    ]

    result = with_marketing_dates(refs)

    assert all(r.marketing_end_date is None for r in result)
    assert all(r.is_active for r in result)


def test_with_marketing_dates_different_appendix_numbers_are_independent() -> None:
    refs = [
        PhoenixDocumentRef("health", "a-old", "100", "01/20", "http://x/a-old.pdf"),
        PhoenixDocumentRef("health", "b-only", "200", "01/20", "http://x/b-only.pdf"),
    ]

    result = with_marketing_dates(refs)

    by_title = {r.title: r for r in result}
    # Different appendix numbers never supersede each other, even with the
    # same edition - "b-only" is the only (and therefore active) entry in
    # its own group.
    assert by_title["a-old"].marketing_end_date is None
    assert by_title["b-only"].marketing_end_date is None


def test_with_marketing_dates_missing_appendix_number_stays_ungrouped() -> None:
    refs = [PhoenixDocumentRef("health", "x", "", "01/20", "http://x/x.pdf")]

    result = with_marketing_dates(refs)

    assert result[0].marketing_start_date is None
    assert result[0].marketing_end_date is None
    assert result[0].is_active is True


def test_with_marketing_dates_unparseable_edition_stays_ungrouped() -> None:
    refs = [PhoenixDocumentRef("health", "x", "100", "not-an-edition", "http://x/x.pdf")]

    result = with_marketing_dates(refs)

    assert result[0].marketing_start_date is None
    assert result[0].marketing_end_date is None

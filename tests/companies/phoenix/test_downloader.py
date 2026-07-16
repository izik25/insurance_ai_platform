"""Tests run without any real browser or network access: `_list_domain` is
exercised against a fake Playwright Page stub, and `download_all` against
a mocked HTTP transport with `list_documents` monkeypatched."""

from __future__ import annotations

from pathlib import Path

import httpx

from companies.phoenix.config import PhoenixConfig
from companies.phoenix.downloader import (
    _NO_RESULTS_MARKER,
    PhoenixDocumentRef,
    PhoenixDownloader,
)


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

    def goto(self, url: str, wait_until: str = "load", timeout: int = 30000) -> None:
        self.goto_calls.append(url)

    def eval_on_selector_all(self, selector: str, script: str) -> list[dict[str, str]]:
        if self._call_index >= len(self._pages):
            return []
        rows = self._pages[self._call_index]
        self._call_index += 1
        return rows

    def content(self) -> str:
        return _NO_RESULTS_MARKER


def _make_config(**overrides: object) -> PhoenixConfig:
    return PhoenixConfig(download_delay_seconds=0.0, **overrides)  # type: ignore[arg-type]


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

    refs = downloader._list_domain(fake_page, "health", "HealthInsCovers")

    assert [r.appendix_number for r in refs] == ["100", "101", "102"]
    assert all(r.domain == "health" for r in refs)
    # 3 goto calls: page 1, page 2, page 3 (which comes back empty and stops the loop)
    assert len(fake_page.goto_calls) == 3


def test_list_domain_returns_empty_when_first_page_empty() -> None:
    fake_page = _FakePage([[]])
    downloader = PhoenixDownloader(_make_config())

    refs = downloader._list_domain(fake_page, "life", "LifeInsCovers")

    assert refs == []
    assert len(fake_page.goto_calls) == 1


def test_local_filename_derived_from_url() -> None:
    ref = PhoenixDocumentRef(
        domain="health",
        title="נספח 100",
        appendix_number="100",
        edition="01/26",
        download_url="http://www.fnx.co.il/sites/docs/polarchive/healthinsurance/nispach-100.pdf",
    )
    assert ref.local_filename == "nispach-100.pdf"


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

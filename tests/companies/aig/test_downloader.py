"""Tests run without any real network access: HTML parsing is exercised as
a pure function, and both `list_documents`/`download_all` against a mocked
HTTP transport."""

from __future__ import annotations

from pathlib import Path

import httpx

from companies.aig.config import AigConfig
from companies.aig.downloader import AigDocumentRef, AigDownloader, refs_from_page_html


def _make_config(**overrides: object) -> AigConfig:
    defaults: dict[str, object] = {
        "download_delay_seconds": 0.0,
        "download_retry_base_seconds": 0.0,
    }
    defaults.update(overrides)
    return AigConfig(**defaults)  # type: ignore[arg-type]


# Shaped like the real page: the same document appears twice (a grouped
# "current documents" section, then a flat historical list), one link uses
# the double-slash quirk seen live, one is an older edition with only one
# appearance and a date range in its own text.
_REAL_SHAPED_HTML = """
<html><body>
<div class="current">
  <a href="https://www.aig.co.il//media/q0lcfxh3/פוליסת-בריאות-בסיסית.pdf">
    פוליסת בריאות בסיסית בתוקף החל מ 02.2024
  </a>
  <a href="https://www.aig.co.il/media/qcqh0clr/extra-care.pdf">
    Extra Care ביטוח למחלות קשות בתוקף החל מ 01.08.2023
  </a>
</div>
<div class="history">
  <a href="https://www.aig.co.il//media/q0lcfxh3/פוליסת-בריאות-בסיסית.pdf">
    פוליסת בריאות בסיסית בתוקף החל מ 02.2024
  </a>
  <a href="https://www.aig.co.il/media/xyidjpqr/פוליסת-בריאות-ישנה.pdf">
    פוליסת בריאות בסיסית בתוקף החל מ- 09.2023 ועד ל- 31.01.2024
  </a>
</div>
<a href="https://www.aig.co.il/some/page/">לא מסמך</a>
</body></html>
"""


def test_refs_from_page_html_parses_real_shaped_page() -> None:
    refs = refs_from_page_html("health", _REAL_SHAPED_HTML)

    assert len(refs) == 3
    by_url = {ref.download_url: ref for ref in refs}
    assert "https://www.aig.co.il/media/q0lcfxh3/פוליסת-בריאות-בסיסית.pdf" in by_url
    assert by_url["https://www.aig.co.il/media/q0lcfxh3/פוליסת-בריאות-בסיסית.pdf"].title == (
        "פוליסת בריאות בסיסית בתוקף החל מ 02.2024"
    )
    assert all(ref.domain == "health" for ref in refs)


def test_refs_from_page_html_normalizes_double_slash() -> None:
    refs = refs_from_page_html("health", _REAL_SHAPED_HTML)

    urls = {ref.download_url for ref in refs}
    assert not any("aig.co.il//media" in url for url in urls)


def test_refs_from_page_html_dedupes_repeated_href() -> None:
    refs = refs_from_page_html("health", _REAL_SHAPED_HTML)

    urls = [ref.download_url for ref in refs]
    assert len(urls) == len(set(urls))


def test_refs_from_page_html_ignores_non_pdf_links() -> None:
    refs = refs_from_page_html("health", _REAL_SHAPED_HTML)

    assert all(ref.download_url.endswith(".pdf") for ref in refs)


def test_refs_from_page_html_extracts_appendix_number_when_present() -> None:
    html = """
    <a href="https://www.aig.co.il/media/abc12345/x.pdf">תנאים כלליים - נספח 923</a>
    """
    refs = refs_from_page_html("life", html)

    assert refs[0].appendix_numbers == ["923"]


def test_local_filename_prefixes_media_id() -> None:
    ref = AigDocumentRef(
        domain="health",
        title="x",
        appendix_numbers=[],
        download_url="https://www.aig.co.il/media/qcqh0clr/extra-care.pdf",
    )
    assert ref.local_filename == "qcqh0clr_extra-care.pdf"


def test_local_filename_caps_length_for_long_titles() -> None:
    long_slug = "a" * 300
    ref = AigDocumentRef(
        domain="health",
        title="x",
        appendix_numbers=[],
        download_url=f"https://www.aig.co.il/media/qcqh0clr/{long_slug}.pdf",
    )
    assert len(ref.local_filename) <= 150
    assert ref.local_filename.startswith("qcqh0clr_")
    assert ref.local_filename.endswith(".pdf")


def _make_downloader_with_pages(pages: dict[str, str]) -> AigDownloader:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=pages.get(str(request.url), ""))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return AigDownloader(_make_config(), http_client=client)


def test_list_documents_fetches_both_domain_pages() -> None:
    pages = {
        "https://www.aig.co.il/health-insurance/": (
            '<a href="https://www.aig.co.il/media/aaa11111/health-doc.pdf">Health Doc</a>'
        ),
        "https://www.aig.co.il/life-insurance/": (
            '<a href="https://www.aig.co.il/media/bbb22222/life-doc.pdf">Life Doc</a>'
        ),
    }
    downloader = _make_downloader_with_pages(pages)

    refs = downloader.list_documents()

    assert {ref.domain for ref in refs} == {"health", "life"}
    assert len(refs) == 2


def _make_downloader_with_refs(
    refs: list[AigDocumentRef], blob_calls: list[str], blob_content: dict[str, bytes]
) -> AigDownloader:
    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.path.rsplit("/", 1)[-1]
        blob_calls.append(key)
        if key in blob_content:
            return httpx.Response(200, content=blob_content[key])
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = AigDownloader(_make_config(), http_client=client)
    downloader.list_documents = lambda: refs  # type: ignore[method-assign]
    return downloader


def _ref(media_id: str, domain: str = "health") -> AigDocumentRef:
    return AigDocumentRef(
        domain=domain,
        title=f"title {media_id}",
        appendix_numbers=[],
        download_url=f"https://www.aig.co.il/media/{media_id}/doc-{media_id}.pdf",
    )


def test_download_all_saves_and_deduplicates(tmp_path: Path) -> None:
    refs = [_ref("aaa"), _ref("bbb"), _ref("ccc", domain="life")]
    blob_content = {
        f"doc-{m}.pdf": c for m, c in (("aaa", b"CONTENT-A"), ("bbb", b"CONTENT-A"), ("ccc", b"CONTENT-C"))
    }
    downloader = _make_downloader_with_refs(refs, [], blob_content)

    saved = downloader.download_all(tmp_path)

    assert len(saved) == 2
    assert (tmp_path / "health" / "aaa_doc-aaa.pdf").read_bytes() == b"CONTENT-A"
    assert (tmp_path / "life" / "ccc_doc-ccc.pdf").read_bytes() == b"CONTENT-C"
    assert not (tmp_path / "health" / "bbb_doc-bbb.pdf").exists()


def test_download_all_skips_already_downloaded_files(tmp_path: Path) -> None:
    refs = [_ref("aaa")]
    blob_calls: list[str] = []
    downloader = _make_downloader_with_refs(refs, blob_calls, {"doc-aaa.pdf": b"CONTENT-A"})

    downloader.download_all(tmp_path)
    blob_calls.clear()
    downloader.download_all(tmp_path)

    assert blob_calls == []


def test_download_all_respects_limit(tmp_path: Path) -> None:
    refs = [_ref("aaa"), _ref("bbb")]
    downloader = _make_downloader_with_refs(
        refs, [], {"doc-aaa.pdf": b"A", "doc-bbb.pdf": b"B"}
    )

    saved = downloader.download_all(tmp_path, limit=1)

    assert len(saved) == 1


def test_download_all_retries_transient_502_then_succeeds(tmp_path: Path) -> None:
    refs = [_ref("aaa")]
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            return httpx.Response(502)
        return httpx.Response(200, content=b"CONTENT-A")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = AigDownloader(_make_config(), http_client=client)
    downloader.list_documents = lambda: refs  # type: ignore[method-assign]

    saved = downloader.download_all(tmp_path)

    assert attempts["count"] == 3
    assert len(saved) == 1


def test_download_all_does_not_retry_permanent_404(tmp_path: Path) -> None:
    refs = [_ref("aaa")]
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = AigDownloader(_make_config(), http_client=client)
    downloader.list_documents = lambda: refs  # type: ignore[method-assign]

    saved = downloader.download_all(tmp_path)

    assert attempts["count"] == 1
    assert saved == []

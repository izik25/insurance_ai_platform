"""Tests run without any real network access: listing-HTML parsing is
exercised as a pure function, and both `list_documents`/`download_all`
against a mocked HTTP transport."""

from __future__ import annotations

from pathlib import Path

import httpx

from companies.hachshara.config import HachsharaConfig
from companies.hachshara.downloader import (
    HachsharaDocumentRef,
    HachsharaDownloader,
    refs_from_listing_html,
)


def _make_config(**overrides: object) -> HachsharaConfig:
    defaults: dict[str, object] = {
        "download_delay_seconds": 0.0,
        "download_retry_base_seconds": 0.0,
        "listing_delay_seconds": 0.0,
    }
    defaults.update(overrides)
    return HachsharaConfig(**defaults)  # type: ignore[arg-type]


# Shaped like a real file-finder page captured live: each PDF's href
# appears twice per card (once in the titled link, once in a separate
# "download" button anchor with an identical href and no title text).
_STYLED_LINK_CLASS = "MuiTypography-root MuiLink-root euizhxa5 muirtl-185or15-...-StyledLink"
_STYLED_DOWNLOAD_CLASS = "MuiButtonBase-root euizhxa6 muirtl-1ldcmtl-...-StyledDownloadButton"
_SAMPLE_CARD_HTML = f"""
<div class="StyledItemBox">
  <a class="{_STYLED_LINK_CLASS}"
     href="https://umbraco-api.hcsra.co.il/media/3bdgqiqd/1765214414_נספח_531_גילוי_נאות_102023.pdf"
     target="_blank"><p class="MuiTypography-h5">גילוי נאות - נספח 531</p></a>
  <a class="{_STYLED_DOWNLOAD_CLASS}"
     tabindex="0"
     href="https://umbraco-api.hcsra.co.il/media/3bdgqiqd/1765214414_נספח_531_גילוי_נאות_102023.pdf"
     aria-label="הורדת קובץ"></a>
</div>
<div class="StyledItemBox">
  <a class="{_STYLED_LINK_CLASS}"
     href="https://umbraco-api.hcsra.co.il/media/abjfz43r/בריאות-למשפחה.pdf"
     target="_blank"><p class="MuiTypography-h5">בריאות למשפחה</p></a>
  <a class="{_STYLED_DOWNLOAD_CLASS}"
     tabindex="0"
     href="https://umbraco-api.hcsra.co.il/media/abjfz43r/בריאות-למשפחה.pdf"
     aria-label="הורדת קובץ"></a>
</div>
"""


def test_refs_from_listing_html_dedupes_repeated_href_per_card() -> None:
    refs = refs_from_listing_html("health", _SAMPLE_CARD_HTML)

    assert len(refs) == 2
    urls = [ref.download_url for ref in refs]
    assert len(urls) == len(set(urls))


def test_refs_from_listing_html_parses_media_id_and_title() -> None:
    refs = refs_from_listing_html("health", _SAMPLE_CARD_HTML)
    ref = next(r for r in refs if r.media_id == "3bdgqiqd")

    assert ref.domain == "health"
    assert ref.title == "גילוי נאות - נספח 531"
    assert ref.download_url.endswith(
        "1765214414_נספח_531_גילוי_נאות_102023.pdf"
    )


def test_refs_from_listing_html_defensively_parses_appendix_number_from_title() -> None:
    refs = refs_from_listing_html("health", _SAMPLE_CARD_HTML)
    with_appendix = next(r for r in refs if r.media_id == "3bdgqiqd")
    without_appendix = next(r for r in refs if r.media_id == "abjfz43r")

    assert with_appendix.appendix_numbers == ["531"]
    assert without_appendix.appendix_numbers == []


def test_refs_from_listing_html_unescapes_html_entities_in_title() -> None:
    html = """
    <a class="StyledLink" href="https://umbraco-api.hcsra.co.il/media/x1y2z3a4/co.pdf"
       target="_blank"><p>הכשרה &quot;חברה&quot; לביטוח &amp; שירות</p></a>
    """
    refs = refs_from_listing_html("health", html)

    assert refs[0].title == 'הכשרה "חברה" לביטוח & שירות'


def test_refs_from_listing_html_ignores_non_media_links() -> None:
    html = '<a class="StyledLink" href="/contact-us/"><p>יצירת קשר</p></a>'

    refs = refs_from_listing_html("health", html)

    assert refs == []


def test_local_filename_short_name_passthrough() -> None:
    ref = HachsharaDocumentRef(
        domain="health",
        media_id="3bdgqiqd",
        title="x",
        appendix_numbers=["531"],
        download_url="https://umbraco-api.hcsra.co.il/media/3bdgqiqd/nespach_531.pdf",
    )
    assert ref.local_filename == "3bdgqiqd_nespach_531.pdf"


def test_local_filename_caps_length_with_hash_suffix() -> None:
    long_stem = "א" * 200
    ref = HachsharaDocumentRef(
        domain="health",
        media_id="3bdgqiqd",
        title="x",
        appendix_numbers=[],
        download_url=f"https://umbraco-api.hcsra.co.il/media/3bdgqiqd/{long_stem}.pdf",
    )
    name = ref.local_filename
    assert len(name) <= 150
    assert name.startswith("3bdgqiqd_")
    assert name.endswith(".pdf")


_LISTING_HTML = """
<a class="StyledLink" href="https://umbraco-api.hcsra.co.il/media/aaa11111/one.pdf"
   target="_blank"><p>Doc One</p></a>
<a class="StyledLink" href="https://umbraco-api.hcsra.co.il/media/bbb22222/two.pdf"
   target="_blank"><p>Doc Two - נספח 42</p></a>
"""


def _make_downloader_with_listing_pages(
    pages_by_path: dict[str, str],
) -> HachsharaDownloader:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path in pages_by_path:
            return httpx.Response(200, text=pages_by_path[request.url.path])
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HachsharaDownloader(_make_config(), http_client=client)


def test_list_documents_parses_health_listing_page() -> None:
    downloader = _make_downloader_with_listing_pages(
        {"/file-finder/health-insurance/": _LISTING_HTML}
    )

    refs = downloader.list_documents()

    assert len(refs) == 2
    assert {ref.media_id for ref in refs} == {"aaa11111", "bbb22222"}
    assert all(ref.domain == "health" for ref in refs)


def _ref(media_id: str, filename: str = "doc.pdf", domain: str = "health") -> HachsharaDocumentRef:
    return HachsharaDocumentRef(
        domain=domain,
        media_id=media_id,
        title=f"title {media_id}",
        appendix_numbers=[],
        download_url=f"https://umbraco-api.hcsra.co.il/media/{media_id}/{filename}",
    )


def _make_downloader_with_refs(
    refs: list[HachsharaDocumentRef], blob_calls: list[str], blob_content: dict[str, bytes]
) -> HachsharaDownloader:
    def handler(request: httpx.Request) -> httpx.Response:
        media_id = request.url.path.rstrip("/").rsplit("/", 2)[-2]
        blob_calls.append(media_id)
        if media_id in blob_content:
            return httpx.Response(200, content=blob_content[media_id])
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = HachsharaDownloader(_make_config(), http_client=client)
    downloader.list_documents = lambda: refs  # type: ignore[method-assign]
    return downloader


def test_download_all_saves_and_deduplicates(tmp_path: Path) -> None:
    refs = [_ref("aaa11111"), _ref("bbb22222"), _ref("ccc33333", domain="health")]
    blob_content = {"aaa11111": b"CONTENT-A", "bbb22222": b"CONTENT-A", "ccc33333": b"CONTENT-C"}
    downloader = _make_downloader_with_refs(refs, [], blob_content)

    saved = downloader.download_all(tmp_path)

    assert len(saved) == 2
    assert (tmp_path / "health" / "aaa11111_doc.pdf").read_bytes() == b"CONTENT-A"
    assert (tmp_path / "health" / "ccc33333_doc.pdf").read_bytes() == b"CONTENT-C"
    assert not (tmp_path / "health" / "bbb22222_doc.pdf").exists()


def test_download_all_skips_already_downloaded_files(tmp_path: Path) -> None:
    refs = [_ref("aaa11111")]
    blob_calls: list[str] = []
    downloader = _make_downloader_with_refs(refs, blob_calls, {"aaa11111": b"CONTENT-A"})

    downloader.download_all(tmp_path)
    blob_calls.clear()
    downloader.download_all(tmp_path)

    assert blob_calls == []


def test_download_all_respects_limit(tmp_path: Path) -> None:
    refs = [_ref("aaa11111"), _ref("bbb22222")]
    downloader = _make_downloader_with_refs(refs, [], {"aaa11111": b"A", "bbb22222": b"B"})

    saved = downloader.download_all(tmp_path, limit=1)

    assert len(saved) == 1


def test_download_all_continues_after_a_failed_download(tmp_path: Path) -> None:
    refs = [_ref("aaa11111"), _ref("bbb22222")]
    downloader = _make_downloader_with_refs(refs, [], {"bbb22222": b"CONTENT-B"})

    saved = downloader.download_all(tmp_path)

    assert len(saved) == 1
    assert not (tmp_path / "health" / "aaa11111_doc.pdf").exists()
    assert (tmp_path / "health" / "bbb22222_doc.pdf").read_bytes() == b"CONTENT-B"


def test_download_all_retries_transient_502_then_succeeds(tmp_path: Path) -> None:
    refs = [_ref("aaa11111")]
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            return httpx.Response(502)
        return httpx.Response(200, content=b"CONTENT-A")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = HachsharaDownloader(_make_config(), http_client=client)
    downloader.list_documents = lambda: refs  # type: ignore[method-assign]

    saved = downloader.download_all(tmp_path)

    assert attempts["count"] == 3
    assert len(saved) == 1
    assert (tmp_path / "health" / "aaa11111_doc.pdf").read_bytes() == b"CONTENT-A"


def test_download_all_does_not_retry_permanent_404(tmp_path: Path) -> None:
    refs = [_ref("aaa11111")]
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    downloader = HachsharaDownloader(_make_config(), http_client=client)
    downloader.list_documents = lambda: refs  # type: ignore[method-assign]

    saved = downloader.download_all(tmp_path)

    assert attempts["count"] == 1
    assert saved == []

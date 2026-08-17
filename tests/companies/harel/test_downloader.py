"""Tests run without any real network access: the archive table's HTML is
exercised as a pure function (`refs_from_archive_html`), shaped like the
real 6-column table confirmed live on 2026-08-10."""

from __future__ import annotations

from datetime import date, timedelta

from companies.harel.downloader import HarelDocumentRef, refs_from_archive_html

_PAST_END_DATE = (date.today() - timedelta(days=30)).strftime("%d.%m.%Y")
_FUTURE_START_DATE = (date.today() - timedelta(days=900)).strftime("%d.%m.%Y")

# Shaped like the real page: a header row (skipped - no <a> tag), one
# superseded historical row (past end date, no appendix number), one
# currently-active row (blank end date), and a malformed-date row.
_REAL_SHAPED_HTML = f"""
<table id="policies">
  <tr>
    <td>שם הפוליסה</td><td>מספר נספח</td><td>תחום</td>
    <td>תאריך תחילת שיווק</td><td>תאריך סיום שיווק</td><td>פרטי הפוליסה</td>
  </tr>
  <tr>
    <td><a href="https://www.harel-group.co.il/Policies/old.pdf">כתב שירות ישן</a></td>
    <td>934</td>
    <td>ביטוח בריאות</td>
    <td>{_FUTURE_START_DATE}</td>
    <td>{_PAST_END_DATE}</td>
    <td>להורדה</td>
  </tr>
  <tr>
    <td><a href="https://www.harel-group.co.il/Policies/current.pdf">כתב שירות נוכחי</a></td>
    <td>457</td>
    <td>ביטוח בריאות</td>
    <td>01.01.2019</td>
    <td></td>
    <td>להורדה</td>
  </tr>
  <tr>
    <td><a href="https://www.harel-group.co.il/Policies/bad-date.pdf">תאריך שגוי</a></td>
    <td></td>
    <td>ביטוח בריאות</td>
    <td>not-a-date</td>
    <td>also-not-a-date</td>
    <td>להורדה</td>
  </tr>
</table>
"""


def _refs_by_url() -> dict[str, HarelDocumentRef]:
    return {ref.download_url: ref for ref in refs_from_archive_html("health", _REAL_SHAPED_HTML)}


def test_refs_from_archive_html_parses_marketing_dates() -> None:
    refs = _refs_by_url()

    old = refs["https://www.harel-group.co.il/Policies/old.pdf"]
    assert old.marketing_start_date == date.today() - timedelta(days=900)
    assert old.marketing_end_date == date.today() - timedelta(days=30)


def test_refs_from_archive_html_blank_end_date_is_none() -> None:
    refs = _refs_by_url()

    current = refs["https://www.harel-group.co.il/Policies/current.pdf"]
    assert current.marketing_end_date is None


def test_is_active_true_when_end_date_blank() -> None:
    refs = _refs_by_url()

    current = refs["https://www.harel-group.co.il/Policies/current.pdf"]
    assert current.is_active is True


def test_is_active_false_when_end_date_has_passed() -> None:
    refs = _refs_by_url()

    old = refs["https://www.harel-group.co.il/Policies/old.pdf"]
    assert old.is_active is False


def test_unparseable_dates_fall_back_to_none_and_active() -> None:
    refs = _refs_by_url()

    bad = refs["https://www.harel-group.co.il/Policies/bad-date.pdf"]
    assert bad.marketing_start_date is None
    assert bad.marketing_end_date is None
    assert bad.is_active is True


def test_header_row_is_skipped() -> None:
    refs = refs_from_archive_html("health", _REAL_SHAPED_HTML)

    assert len(refs) == 3
    assert all(ref.download_url.endswith(".pdf") for ref in refs)


def test_is_active_true_when_end_date_is_today() -> None:
    ref = HarelDocumentRef(
        domain="health",
        title="x",
        appendix_numbers=[],
        download_url="https://example/x.pdf",
        marketing_end_date=date.today(),
    )
    assert ref.is_active is True

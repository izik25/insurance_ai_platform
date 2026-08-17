"""Harel (הראל) configuration.

Harel's policy archive (harel-group.co.il/Insurance/Pages/archive.aspx) is a
SharePoint-hosted ASP.NET WebForms page whose "תחום" (area) filter is a
plain `<select>` of GUID-valued options and whose "סנן מידע" (Filter) button
runs `Harel.PoliciesArchive.Filter()` (see harel_common.js) - which builds a
plain query string and does a full navigation, not an AJAX/JSON API call:

    archive.aspx?cn=<company display text>&t2=<area GUID>&p=<page number>

Confirmed live: a plain unauthenticated `httpx` GET against that URL (no
Playwright, no cookies/session beyond defaults) returns the fully
server-rendered results table - the simplest of every company plugin so
far, on par with Direct Insurance. `cn` takes the company's *display text*
(e.g. "הראל"), not a GUID - the same archive also serves several other
brands Harel Group operates (אליהו, דקלה, ציון, שירביט, שלוח), which is why
`cn` must always be sent explicitly rather than left unset.

`AREA_TO_DOMAIN` maps the site's own area GUIDs (confirmed live via the
`archive_area` select's option values) to this platform's domain
(health/life). Covers eight categories: בריאות, חיים, מחלות קשות, אובדן
כושר עבודה, חיים פנסיוני, תאונות אישיות, סיעודי, משכנתא (סיעודי/משכנתא
added 2026-08-17, per explicit user request - previously excluded along
with the site's remaining out-of-scope categories: לעסק, עובדים זרים
ותיירים, חיסכון והשקעה, רכב, נסיעות לחו"ל, דירה, שיניים, רכוש). "אובדן
כושר עבודה"/"מחלות קשות"/"תאונות אישיות"/"סיעודי" are folded into health
(same convention Menorah/Migdal/Phoenix/Clal already use for
long-term-care); "חיים פנסיוני" is folded into life (a pension-adjacent
life product, not a distinct domain the platform tracks). "משכנתא" is
folded into **life**: its core insurable event is death/disability tied to
loan repayment (e.g. "ביטוח חיים למשכנתא"), the same framing as the life
domain's other products - consistent with how Direct Insurance's own
life-adjacent salesGroups were kept as-is rather than split out (see
companies/directinsurance/config.py). The category also contains a
structural/property component ("ביטוח מבנה דירה למשכנתא") that doesn't
cleanly fit either domain; not split out separately, same
don't-over-engineer-a-single-category rationale as Direct Insurance's.
Flagged for the user to correct if this classification doesn't match how
they want mortgage documents to surface in cross-company matching.

Each result row's "מספר נספח" (appendix number) column comes straight from
the site's own metadata - no OCR/LLM guessing needed, same as
Phoenix/Clal/Menorah.

LIVE PRODUCT PAGES (added 2026-08-12): `archive.aspx` is NOT a complete,
up-to-date index. Confirmed live: it can lag *years* behind on adding a
product's current/still-marketed edition - e.g. appendix 466 ("מענקית
זהב", edition 04/2021) superseded archived appendix 455 (edition 10/2018)
over 3 years ago, yet 455 was still the newest row in the מחלות קשות
archive listing (both page 1 and 2, checked live) with no end date, and 466
didn't appear anywhere in it. Same story for appendix 191 ("מגן 1" life
policy, edition 07/2021) against a ביטוח חיים archive listing that only
ever had 2 rows, both from 2013.

The current edition instead lives on each product's own live page under
`harel-group.co.il/insurance/<area>/policies/<slug>`, with its PDFs served
from `media.harel-group.co.il` or a CloudFront mirror - a completely
different URL space from archive.aspx's `Policies/...` PDFs, so the two
sources are additive, not overlapping (`list_documents()` merges both).

`LIVE_PRODUCT_PAGES` was built by crawling each area's
`/insurance/<area>/policies` index page (confirmed live 2026-08-12) for
every `/insurance/<area>/policies/<slug>` link - 28 product pages across
the 5 areas that have one (life pension has no equivalent live product
index found; it stays archive-only, same as before this addition).

These live pages don't expose a "מספר נספח" column the way archive.aspx
does - the number has to be read out of each PDF's own page-1 text
instead (see downloader.py's `_extract_appendix_number`). PyMuPDF's
`get_text()` on some of these PDFs emits RTL text out of visual order
(see core/pdf_processing/reading_order.py's docstring for the confirmed
root cause), so extraction reconstructs reading order first and only then
regexes for "נספח/תכנית (מספר/מס')? NNN"; this is best-effort in the same
sense as AIG's title-text regex (companies/aig/) - not backed by a
structured site field, so treat coverage as partial and spot-checkable,
not guaranteed.
"""

from __future__ import annotations

from pydantic import Field

from core.plugins.base import CompanyConfig

# area GUID -> our domain. See module docstring for what's excluded and why.
AREA_TO_DOMAIN: dict[str, str] = {
    "a4c52143-5928-40d8-b04d-01661f0e2093": "health",  # ביטוח בריאות
    "324cd4d0-ece4-4cca-a936-1a7d87c14098": "health",  # ביטוח אובדן כושר עבודה
    "49078542-cd71-4ac8-a565-f58a728560ef": "health",  # ביטוח מחלות קשות
    "a4b79403-7ff8-4ed3-82f1-e84097f7dab3": "health",  # ביטוח תאונות אישיות
    "46dd45e4-0367-4465-b8a2-4f5419481aae": "health",  # ביטוח סיעודי
    "a4d4bd40-0e52-4fe8-816b-8a41f4b85a9c": "life",  # ביטוח חיים
    "de3f0a61-7ca9-469c-a4f0-40a69aa1174e": "life",  # ביטוח חיים פנסיוני
    "c45976d3-7761-4ecc-b836-0e945cd50ab0": "life",  # ביטוח משכנתא
}

# area GUID -> the Hebrew label as it appears on-site (kept only for logging).
AREA_LABELS: dict[str, str] = {
    "a4c52143-5928-40d8-b04d-01661f0e2093": "ביטוח בריאות",
    "324cd4d0-ece4-4cca-a936-1a7d87c14098": "ביטוח אובדן כושר עבודה",
    "49078542-cd71-4ac8-a565-f58a728560ef": "ביטוח מחלות קשות",
    "a4b79403-7ff8-4ed3-82f1-e84097f7dab3": "ביטוח תאונות אישיות",
    "46dd45e4-0367-4465-b8a2-4f5419481aae": "ביטוח סיעודי",
    "a4d4bd40-0e52-4fe8-816b-8a41f4b85a9c": "ביטוח חיים",
    "de3f0a61-7ca9-469c-a4f0-40a69aa1174e": "ביטוח חיים פנסיוני",
    "c45976d3-7761-4ecc-b836-0e945cd50ab0": "ביטוח משכנתא",
}

# Areas to query WITHOUT the `cn=הראל` company filter - confirmed live: both
# life areas return 0 rows with the filter applied, even though the same
# areas *unfiltered* have real content (2 docs for "ביטוח חיים", ~30 for
# "ביטוח חיים פנסיוני" across 3 pages). None of the unfiltered pension
# results carry any company name in their title at all (unlike health,
# where non-Harel rows are clearly suffixed "..., אליהו חברה לביטוח" etc.) -
# these two categories' documents apparently aren't tagged with a `cn` value
# the filter can match, rather than genuinely belonging to other companies.
# Health areas keep the filter (confirmed live: it correctly narrows out
# Eliahu/Dikla/Tzion/Shirbit/Shlach's own health documents, which are
# numerous - dropping the filter there would pull in mostly non-Harel data).
# סיעודי/משכנתא (added 2026-08-17) also keep the filter - confirmed live,
# filtered gives *more* legitimate Harel rows than unfiltered for both (55
# vs 31 for סיעודי across 6/4 pages; 9 vs 1 for משכנתא), with no genuine
# other-company leakage found in the filtered results (a naive "כלל"
# substring check false-positived on "קופת חולים כללית", not the insurer).
UNFILTERED_AREAS: frozenset[str] = frozenset(
    {
        "a4d4bd40-0e52-4fe8-816b-8a41f4b85a9c",  # ביטוח חיים
        "de3f0a61-7ca9-469c-a4f0-40a69aa1174e",  # ביטוח חיים פנסיוני
    }
)

# Harel site area slug -> platform domain. Same health/life folding as
# AREA_TO_DOMAIN above (see module docstring); life pension and long-term-care
# have no live product-listing page and stay archive-only (confirmed live
# 2026-08-17: /insurance/long-term-care has no /policies sub-index at all,
# only /information pages - Harel doesn't appear to sell it self-service
# here the way every other tracked category is sold).
LIVE_AREA_TO_DOMAIN: dict[str, str] = {
    "health": "health",
    "diseases-disabilities": "health",  # מחלות קשות
    "loss-of-working-ability": "health",  # אובדן כושר עבודה
    "personal-accident": "health",  # תאונות אישיות
    "life": "life",
    "mortgage": "life",  # ביטוח משכנתא - see AREA_TO_DOMAIN's docstring note on this choice
}

# area slug -> product-page slugs, from crawling each
# harel-group.co.il/insurance/<area>/policies index page (confirmed live
# 2026-08-12, mortgage added 2026-08-17 - see module docstring). Re-crawl
# that index periodically; this list is a snapshot, not derived at runtime.
LIVE_PRODUCT_PAGES: dict[str, list[str]] = {
    "health": [
        "ambulatory-care", "child-development", "complementary", "doctor-at-home",
        "medications", "mehashekel-harishon", "online-plus", "personal-doctor",
        "premium-care", "private", "surgery-abroad", "transplants-and-treatments",
        "upgrade-extra", "upgrade-surgeries",
    ],
    "life": [
        "death-accident", "disability-accident", "family-income", "magen",
        "magen-mashlim-child", "magen-monthly",
    ],
    "diseases-disabilities": ["cancer-compensation", "gold-compensation"],
    "loss-of-working-ability": [
        "future-premium", "insurance-umbrella", "premium-release", "strength-tomorrow",
    ],
    "personal-accident": ["midlife-family", "scuba-diving"],
    "mortgage": ["safe-mortgage", "structural", "structural-b"],
}

# Link text substrings that mark a live-page PDF as collateral (application
# forms, "material info" leaflets, sport-exclusion definition sheets) rather
# than an actual policy/appendix document - excluded from ingestion
# entirely, confirmed live 2026-08-12 by inspecting every PDF on all 28
# product pages.
LIVE_PAGE_EXCLUDED_TITLE_SUBSTRINGS: tuple[str, ...] = (
    "טופס הצעה",
    "טופס הצטרפות",
    "מידע מהותי",
    "מידע תמציתי",
    "הגדרת ספורט אתגרי",
    "ענפי ספורט אתגרי",
    "דפי עזר",
)

# Numbers that regex extraction has been confirmed live to false-positive on
# (Harel's own phone star-number, seen constantly in PDF footers/headers) -
# never a real appendix number.
LIVE_PAGE_APPENDIX_NUMBER_DENYLIST: frozenset[str] = frozenset({"2735"})


class HarelConfig(CompanyConfig):
    """Configuration for the Harel (הראל) insurance company plugin."""

    company_id: str = "harel"
    display_name: str = "הראל"

    archive_page_url: str = "https://www.harel-group.co.il/Insurance/Pages/archive.aspx"
    # The archive's own "חברה" (company) filter text - must match a
    # `#comapny` option's text exactly (confirmed live: "הראל").
    company_filter_text: str = "הראל"

    # Base for live product pages - see LIVE_PRODUCT_PAGES/module docstring.
    live_product_base_url: str = "https://www.harel-group.co.il/insurance"

    request_timeout_seconds: float = 30.0
    download_delay_seconds: float = Field(default=0.5, ge=0.0)
    download_retry_max_attempts: int = Field(default=4, ge=1)
    download_retry_base_seconds: float = Field(default=3.0, ge=0.0)

    # No bot-management observed against this endpoint in practice (plain
    # httpx GETs succeeded repeatedly during investigation), but the site
    # does carry F5/Volterra bot-defense cookies (`TS...`-prefixed) - kept
    # modest rather than firing dozens of paginated requests back to back.
    listing_delay_seconds: float = Field(default=1.0, ge=0.0)

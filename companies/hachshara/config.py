"""Hachshara (הכשרה חברה לביטוח בע"מ, hcsra.co.il) configuration.

Data flow (confirmed live on 2026-07-22): each domain has a public
"file-finder" listing page (e.g. /file-finder/health-insurance/) rendered as
static Next.js SSR HTML - every document card (title + PDF link) is present
directly in the raw HTML, no JavaScript execution or API call needed to see
the full list. Confirmed via a bare `curl` (no browser, no bot-management
observed on either the listing page or the PDF host). `robots.txt` allows
`/file-finder/` (only disallows internal SharePoint-list paths).

Health listing had 155 unique PDF links, life had 115, both confirmed live.
Life documents use the same card markup and PDF layout as health, but a
second real-world appendix-number phrasing was found there: "נספח מס' <n>"
(abbreviated "number"), vs. health's "נספח מספר <n>" (full word) - both are
handled by extractor.py's local regex.

The listing page groups documents under five tabs (all forms/insurance
forms/policies/claims/questionnaires/archive), but nothing in the raw HTML
cleanly associates a given card with one specific tab, and per user
instruction ("pull everything") no attempt is made to filter or classify by
tab - every PDF link found on a listing page is ingested.

`/file-finder/mortgage-insurance/` (added 2026-08-17 per explicit user
request) folded into `life`: real appendix content confirmed live (72
docs, e.g. "פוליסת ביטוח מגן למשכנתא | נספח 665 | 11/2022" and its
disability variant "נספח 615"), same "מגן למשכנתא (חיים ומבנה)"
life+structure bundling as every other company's mortgage category, so
kept together rather than split - same precedent as Harel/Direct
Insurance. The site's own nav (confirmed live by parsing every
`/file-finder/*` link off the health-insurance listing page) has no
long-term-care/"סיעוד" category at all - full list is health, life,
mortgage, apartment, car, business, engineering, foreign-employees,
pension-savings, best-invest - so there's nothing to add for that one;
not an omission, genuinely not offered/listed here.
"""

from __future__ import annotations

from pydantic import Field

from core.plugins.base import CompanyConfig

DOMAIN_TO_LISTING_PATH: dict[str, str] = {
    "health": "/file-finder/health-insurance/",
    "life": "/file-finder/life-insurance/",
    "mortgage": "/file-finder/mortgage-insurance/",
}

# DOMAIN_TO_LISTING_PATH's keys double as the platform `domain` each ref is
# tagged with (see downloader.py's `_list_domain`) - "mortgage" isn't a real
# platform domain, so it's remapped to "life" right after listing (see
# downloader.list_documents()) rather than baked in here, to keep this dict
# a straightforward path lookup.
LISTING_DOMAIN_TO_PLATFORM_DOMAIN: dict[str, str] = {
    "health": "health",
    "life": "life",
    "mortgage": "life",
}


class HachsharaConfig(CompanyConfig):
    """Configuration for the Hachshara (הכשרה) plugin."""

    company_id: str = "hachshara"
    display_name: str = 'הכשרה חברה לביטוח בע"מ'

    base_url: str = "https://www.hcsra.co.il"

    request_timeout_seconds: float = 30.0
    download_delay_seconds: float = Field(default=0.3, ge=0.0)
    download_retry_max_attempts: int = Field(default=4, ge=1)
    download_retry_base_seconds: float = Field(default=3.0, ge=0.0)

    # No bot-management observed on the listing pages or the PDF host
    # (confirmed live) - kept modest anyway rather than firing requests back
    # to back, same rationale as Direct Insurance's identical default.
    listing_delay_seconds: float = Field(default=0.5, ge=0.0)

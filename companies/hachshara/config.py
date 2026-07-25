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
"""

from __future__ import annotations

from pydantic import Field

from core.plugins.base import CompanyConfig

DOMAIN_TO_LISTING_PATH: dict[str, str] = {
    "health": "/file-finder/health-insurance/",
    "life": "/file-finder/life-insurance/",
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

"""Phoenix-specific configuration.

Phoenix's policy-terms search (fnx.co.il/spf/Iframe_FormsConditions.aspx)
is a classic ASP.NET/SharePoint search form: pick a "World" (insurance
domain) and a company, and it returns a paginated results table. Unlike
Migdal, the appendix number is already a clean column in that table — no
OCR or text extraction is needed to get it.

The whole fnx.co.il domain sits behind CloudFront + AWS WAF Bot Control,
which blocks default headless Chromium outright (403). It does NOT block
plain HTTP requests (used for the actual PDF downloads) or a headless
browser with basic stealth (hidden navigator.webdriver, a real UA/locale)
— confirmed against the live site on 2026-07-16.
"""

from __future__ import annotations

from pydantic import Field

from core.plugins.base import CompanyConfig

# "World" values the search form accepts, restricted to what this platform
# tracks for Phoenix.
DOMAIN_TO_WORLD: dict[str, str] = {
    "health": "HealthInsCovers",
    "life": "LifeInsCovers",
}


class PhoenixConfig(CompanyConfig):
    """Configuration for the Phoenix (הפניקס) insurance company plugin."""

    company_id: str = "phoenix"
    display_name: str = "הפניקס"

    search_url: str = "https://www.fnx.co.il/spf/Iframe_FormsConditions.aspx"
    company_filter: str = "הפניקס"

    request_timeout_seconds: float = 30.0
    download_delay_seconds: float = Field(default=0.2, ge=0.0)

    # Listing (search-page) pacing is separate from download pacing: it's
    # the browser-driven side that tripped WAF/rate-limit throttling after
    # the initial bulk download, so it gets its own, more conservative knobs.
    listing_page_delay_seconds: float = Field(default=3.0, ge=0.0)
    listing_retry_base_seconds: float = Field(default=5.0, ge=0.0)
    listing_retry_max_attempts: int = Field(default=5, ge=1)

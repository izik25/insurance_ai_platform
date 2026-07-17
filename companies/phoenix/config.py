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

# The health search form's own "Covers" (sub-category) dropdown is
# rendered with broken HTML on the site itself (every <option value=""...>
# instead of a real value) - confirmed live by dumping its outerHTML - so
# it can't be driven by clicking the dropdown. The server still honors
# these Hebrew labels as a plain `cover` query param, though, and querying
# per sub-category is what actually surfaces Phoenix's full health
# archive: the unfiltered query (cover="") gets stuck on an unreliable
# page 10 no matter how it's retried, while each sub-category query stays
# well under that page count. The unfiltered query is deliberately left
# out entirely - confirmed live that its 73 results are a strict subset of
# what the sub-categories already cover (they alone summed to 1000+ raw
# rows against a final deduped total of 972), so it only ever wastes
# ~20 minutes hitting that same page-10 wall for zero new documents. Life's
# search form doesn't expose this sub-category control and paginates
# reliably as a single query, so it's left out of this map (falls back to
# a single unfiltered query).
DOMAIN_COVERS: dict[str, list[str]] = {
    "health": [
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
    ],
}


class PhoenixConfig(CompanyConfig):
    """Configuration for the Phoenix (הפניקס) insurance company plugin."""

    company_id: str = "phoenix"
    display_name: str = "הפניקס"

    search_url: str = "https://www.fnx.co.il/spf/Iframe_FormsConditions.aspx"
    company_filter: str = "הפניקס"

    request_timeout_seconds: float = 30.0
    download_delay_seconds: float = Field(default=0.2, ge=0.0)
    # PDF downloads occasionally hit a transient 502 from fnx.co.il's
    # gateway under sustained load (confirmed live: ~12% of a 2000+ file
    # run) - worth a few retries, unlike a 404, which means the link is
    # genuinely broken upstream and retrying won't help.
    download_retry_max_attempts: int = Field(default=4, ge=1)
    download_retry_base_seconds: float = Field(default=3.0, ge=0.0)

    # Listing (search-page) pacing is separate from download pacing: it's
    # the browser-driven side that tripped WAF/rate-limit throttling after
    # the initial bulk download, so it gets its own, more conservative knobs.
    listing_page_delay_seconds: float = Field(default=3.0, ge=0.0)
    listing_retry_base_seconds: float = Field(default=5.0, ge=0.0)
    listing_retry_max_attempts: int = Field(default=5, ge=1)
    # The site's own pager occasionally renders a clean "no results" page
    # mid-archive (confirmed live: page 10 of Phoenix health returned it
    # with a plain 200, five times in a row) even though its own "last
    # page" link says results continue well past that page. When we know
    # from that link that more pages should exist, it's worth retrying
    # harder before giving up and truncating the archive.
    listing_premature_end_max_attempts: int = Field(default=10, ge=1)
    # If a page still looks premature after exhausting the attempts above,
    # the failure has been reproduced as session-scoped, not page-specific:
    # a completely fresh browser context (new cookies) fetching the exact
    # same URL succeeds immediately (confirmed live). Reset the session and
    # retry the same page rather than truncating the archive there.
    listing_context_reset_max: int = Field(default=3, ge=0)

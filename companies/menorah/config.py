"""Menorah-specific configuration.

Menorah's policy archive (menoramivt.co.il/policy/) is a Next.js app whose
search form posts to `/policy/api/v1/search/` with a JSON body
`{"policyHeaderForDisplay": "", "policyHeaderFullMatch": false,
"lineOfBusiness": <id>, "policyIssueDate": "", "uuid": <int>}` and returns
every matching document in one response (`{"err": null, "data": [...]}`) -
confirmed live, no pagination needed, same pattern as Clal. Each result's
`policyHeader`/`tags` embed the Hebrew "נספח <number>" pattern directly -
reused via `core.extraction.appendix_number.find_appendix_numbers` rather
than a bespoke regex, since it's the exact same shared pattern Migdal
already parses out of raw PDF text.

Unlike Migdal/Phoenix/Clal, Menorah keeps "אובדן כושר עבודה" (disability),
"מחלות קשות" (critical illness) and "תאונות אישיות" (personal accidents)
as their own top-level categories, separate from "בריאות" (health) -
confirmed live via the domain dropdown's option list. All four are folded
into this platform's "health" domain (consistent with how Phoenix/Migdal/
Clal already treat critical-illness/accident coverage as part of health).
"ביטוח חיים" (life) is itself split into four sub-categories (risk-only /
self-employed / individual / employees) rather than being a single value.

The search page sits behind real bot-management with an actual CAPTCHA
challenge (not just a soft block) - confirmed live, a burst of requests in
quick succession triggered a "We think you're a bot" / hCaptcha page. A
single slow request cleared it again a couple of minutes later, so this
looks rate/behavior-based rather than a permanent IP ban, but it means
this plugin MUST be paced far more conservatively than Phoenix or Clal -
long, deliberate delays between listing requests, never parallel. PDF
downloads themselves, served from a separate `cdn.menoramivt.co.il` host,
are NOT behind this protection - a plain HTTP GET with a normal
User-Agent and no cookies works fine (confirmed live).
"""

from __future__ import annotations

from pydantic import Field

from core.plugins.base import CompanyConfig

# lineOfBusiness id -> the dropdown's exact option label (needed to click
# the right option in the UI-driven search - see downloader.py). Restricted
# to what this platform tracks; the site has several more categories (car,
# mortgage, travel, home/business insurance, pension fund rules, etc.) that
# are out of scope.
DOMAIN_TO_LINES_OF_BUSINESS: dict[str, list[tuple[int, str]]] = {
    "health": [
        (5, "ביטוח בריאות"),
        (6, "ביטוח מחלות קשות"),
        (3, "ביטוח אובדן כושר עבודה"),
        (13, "ביטוח תאונות אישיות"),
    ],
    "life": [
        (15, "ביטוח חיים – תוכניות הכוללות רכיב ריסק בלבד"),
        (16, "ביטוח חיים – תוכניות לעצמאים"),
        (17, "ביטוח חיים – תוכניות ביטוח פרט"),
        (18, "ביטוח חיים – תוכניות לשכירים (מנהלים)"),
    ],
}


class MenorahConfig(CompanyConfig):
    """Configuration for the Menorah (מנורה מבטחים) insurance company plugin."""

    company_id: str = "menorah"
    display_name: str = "מנורה מבטחים"

    search_page_url: str = "https://www.menoramivt.co.il/policy/"
    search_api_url_fragment: str = "/api/v1/search"

    request_timeout_seconds: float = 30.0
    download_delay_seconds: float = Field(default=0.5, ge=0.0)
    download_retry_max_attempts: int = Field(default=4, ge=1)
    download_retry_base_seconds: float = Field(default=3.0, ge=0.0)

    # Much more conservative than Phoenix (3s) or Clal (2s) - this site's
    # bot-management issued an actual CAPTCHA challenge after a burst of
    # requests within a couple of minutes (confirmed live).
    listing_delay_seconds: float = Field(default=8.0, ge=0.0)

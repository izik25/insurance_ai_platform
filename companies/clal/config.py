"""Clal-specific configuration.

Clal's policy-terms search (clalbit.co.il/policysearch/) is an Angular SPA
backed by an Umbraco CMS JSON API. Selecting a "Family" (insurance domain)
and "Company" and submitting the search calls
`/umbraco/api/SearchApi/SearchPolicies`, which returns every matching
document in one response (no pagination) - confirmed live: TotalResultCount
exactly matched the number of rows returned for both health (290) and life
(215). Much simpler than Phoenix's page-by-page archive. Each result
already carries the appendix number ("AttachmentNumber") as a clean field -
no OCR or PDF-content reading needed, same as Phoenix.

The API endpoint sits behind bot-management (Imperva/Akamai-style "TS..."
cookies): a bare HTTP GET with no browser session gets a 404 "No HTTP
resource" response even with fully correct parameters (confirmed live). It
only works when called from within a real browser session that has loaded
the search page and driven a real UI search (select the dropdowns, click
the search button) at least once. PDF downloads themselves, served from
/media/, are NOT behind this protection - a plain HTTP GET with a normal
User-Agent and no cookies works fine (confirmed live).
"""

from __future__ import annotations

from pydantic import Field

from core.plugins.base import CompanyConfig

# Clal's "Family" (domain) dropdown values, restricted to what this
# platform tracks. Critical-illness policies ("מחלות קשות") are not a
# separate Family on Clal's site - they're a subset of "בריאות" (health),
# same as how Migdal/Phoenix critical-illness documents already live under
# the "health" domain.
DOMAIN_TO_FAMILY: dict[str, str] = {
    "health": "1520",
    "life": "8277",
}


class ClalConfig(CompanyConfig):
    """Configuration for the Clal (כלל) insurance company plugin."""

    company_id: str = "clal"
    display_name: str = "כלל"

    search_page_url: str = "https://www.clalbit.co.il/policysearch/"
    search_api_url_fragment: str = "SearchPolicies"
    media_base_url: str = "https://www.clalbit.co.il"
    # The site's Company dropdown has TWO distinct Clal entities with
    # completely non-overlapping document sets under the "Family" filter -
    # confirmed live: "כלל ביטוח" (id 1) returns 290 health documents,
    # "כלל בריאות" (id 9, "Clal Health") returns a further 81 with zero
    # FilePath overlap between them (things like "אחריות לחיים סרטן" -
    # cancer/critical-illness cover). Both need to be queried per domain to
    # get the full archive; id 9 returned 0 extra for life, but it's cheap
    # to keep checking both per domain rather than hard-code that asymmetry.
    company_filter_ids: list[str] = Field(default_factory=lambda: ["1", "9"])

    request_timeout_seconds: float = 30.0
    download_delay_seconds: float = Field(default=0.2, ge=0.0)
    # Same reasoning as Phoenix: a 5xx is worth retrying, a 4xx means the
    # link is genuinely broken and retrying wastes time.
    download_retry_max_attempts: int = Field(default=4, ge=1)
    download_retry_base_seconds: float = Field(default=3.0, ge=0.0)

    # Pause between the two domain searches (health/life) on the search
    # page - polite pacing toward the site, mirroring the project's
    # documented principle even though this site hasn't shown any
    # rate-limiting the way fnx.co.il (Phoenix) did.
    listing_delay_seconds: float = Field(default=2.0, ge=0.0)

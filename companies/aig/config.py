"""AIG (aig.co.il) configuration.

`DOMAIN_TO_PAGE_URL` maps this platform's domain to the AIG product page
that lists that domain's policy/disclosure documents - confirmed live on
2026-07-27: unlike the self-service "documents" page (aig.co.il/documents/,
which only serves administrative forms behind a login-gated dropdown),
each product page (health-insurance, life-insurance) is a plain
server-rendered page with every policy/גילוי נאות PDF already in the HTML,
no login and no bot protection observed. Critical illness ("Extra Care")
has no dedicated page of its own - its documents are already listed on
both the health and life pages (see downloader.py for how that's handled).

`mortgage-insurance` (added 2026-08-17 per explicit user request) is the
same server-rendered, no-login pattern - confirmed live, 25 real PDF
policy editions from 1999-2024 (e.g. "פוליסת ביטוח חיים למשכנתא בתוקף
מ-06.2023 ועד 31.10.2024"). Its dict key is "mortgage", not "life" - see
`PAGE_KEY_TO_PLATFORM_DOMAIN` for why and how it's remapped to the
platform's `life` domain (same don't-split-life-and-structure-cover
precedent as every other company's mortgage category added this session -
see companies/harel/config.py for the fuller writeup). Long-term-care
("סיעוד") has no page anywhere on this site - confirmed live, the
homepage nav doesn't link one and every plausible slug guessed
(ltc-insurance, long-term-care-insurance, nursing-care-insurance,
siud-insurance, care-insurance, long-term-care) 200s but is actually AIG's
soft-404 template (identical ~306KB body, confirmed via title tag) -
genuinely not offered/listed here.
"""

from __future__ import annotations

from pydantic import Field

from core.plugins.base import CompanyConfig

DOMAIN_TO_PAGE_URL: dict[str, str] = {
    "health": "https://www.aig.co.il/health-insurance/",
    "life": "https://www.aig.co.il/life-insurance/",
    "mortgage": "https://www.aig.co.il/mortgage-insurance/",
}

# DOMAIN_TO_PAGE_URL's keys double as the platform `domain` each ref is
# tagged with (see downloader.py's list_documents()) - "mortgage" isn't a
# real platform domain, so it's remapped to "life" here rather than baked
# into DOMAIN_TO_PAGE_URL, keeping that dict a straightforward URL lookup.
PAGE_KEY_TO_PLATFORM_DOMAIN: dict[str, str] = {
    "health": "health",
    "life": "life",
    "mortgage": "life",
}


class AigConfig(CompanyConfig):
    """Configuration for the AIG plugin."""

    company_id: str = "aig"
    display_name: str = "AIG"

    request_timeout_seconds: float = 30.0
    download_delay_seconds: float = Field(default=0.3, ge=0.0)
    download_retry_max_attempts: int = Field(default=4, ge=1)
    download_retry_base_seconds: float = Field(default=3.0, ge=0.0)

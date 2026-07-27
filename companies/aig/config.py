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
"""

from __future__ import annotations

from pydantic import Field

from core.plugins.base import CompanyConfig

DOMAIN_TO_PAGE_URL: dict[str, str] = {
    "health": "https://www.aig.co.il/health-insurance/",
    "life": "https://www.aig.co.il/life-insurance/",
}


class AigConfig(CompanyConfig):
    """Configuration for the AIG plugin."""

    company_id: str = "aig"
    display_name: str = "AIG"

    request_timeout_seconds: float = 30.0
    download_delay_seconds: float = Field(default=0.3, ge=0.0)
    download_retry_max_attempts: int = Field(default=4, ge=1)
    download_retry_base_seconds: float = Field(default=3.0, ge=0.0)

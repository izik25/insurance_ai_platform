"""Direct Insurance (ביטוח ישיר, 555.co.il) configuration.

Unlike every other company plugin so far, this site's document-search API
requires no browser session at all - confirmed live, plain `httpx` calls
work end to end (see downloader.py for the full data-flow explanation).

`DOMAIN_TO_PRODUCT` maps this platform's domain to 555.co.il's numeric
`product` id, discovered live via `GET /webapp/api/siteapi/form/formdata`
(product 7 = "ביטוח חיים"/life, product 8 = "ביטוח בריאות"/health, product
9 = "תאונות אישיות"/personal accidents - folded into health, same
convention Harel/Migdal use for their own personal-accidents category;
confirmed live 2026-08-13 this product exists with ~70 documents of its
own and was simply never wired up here despite being named in this
docstring from the start - a plain omission, not a scope decision. Product
10, "תוכניות חסכון"/savings plans, stays excluded, consistent with every
other company plugin only tracking health/life). Unlike Menorah's
`salesGroup` sub-categories (and their valid form types), which are
fetched dynamically from that same `formdata` endpoint rather than
hardcoded here - the site already exposes that taxonomy as data, so
duplicating it as a hardcoded table would only risk going stale.

Per explicit user decision, ALL form types available for a given
salesGroup are queried (not just "פוליסה וכתבי שירות"/policy) - live
testing showed excluding policy returns only administrative forms with no
download link at all, which would defeat the project's purpose of
comparing actual policy coverage terms.

Two salesGroups nested under the life page's product (7) - "אובדן כושר
עבודה" (disability, key=24) and "נכות מתאונה" (accident-disability,
key=22) - are content-wise health-adjacent rather than life/death-benefit
products, and Migdal/Menorah already classify the same content as health.
Kept as domain="life" here anyway per explicit user decision (2026-07):
the cross-company matching pipeline will still surface the right pairing
against its counterpart if relevant, so this wasn't worth the extra
complexity of a per-salesGroup override.

Product 6, "ביטוח משכנתא"/mortgage (added 2026-08-17 per explicit user
request), is folded into `life` for the same reason: its "חיים למשכנתא"
salesGroup is a life/death-benefit product, and the site doesn't expose a
finer split than whole-product salesGroup queries, so its other salesGroup
("מבנה למשכנתא", a structural/property cover) rides along into life too -
same don't-split-a-single-category precedent as the two salesGroups above.
No long-term-care ("סיעוד") product exists anywhere in this site's own
10-product taxonomy (confirmed live 2026-08-17 via `formdata`) - not
omitted, genuinely not offered/listed here.
"""

from __future__ import annotations

from pydantic import Field

from core.plugins.base import CompanyConfig

DOMAIN_TO_PRODUCT: dict[str, list[str]] = {
    "life": ["7", "6"],
    "health": ["8", "9"],
}


class DirectInsuranceConfig(CompanyConfig):
    """Configuration for the Direct Insurance (ביטוח ישיר) plugin."""

    company_id: str = "directinsurance"
    display_name: str = "ביטוח ישיר"

    formdata_url: str = "https://www.555.co.il/webapp/api/siteapi/form/formdata"
    search_url: str = "https://www.555.co.il/webapp/api/siteapi/form/sendformdata"
    download_url_template: str = "https://www.555.co.il/webapp/api/siteapi/form/openform/{form_id}"

    request_timeout_seconds: float = 30.0
    download_delay_seconds: float = Field(default=0.3, ge=0.0)
    download_retry_max_attempts: int = Field(default=4, ge=1)
    download_retry_base_seconds: float = Field(default=3.0, ge=0.0)

    # No bot-management observed on either endpoint (confirmed live) - kept
    # modest anyway rather than firing requests back to back.
    listing_delay_seconds: float = Field(default=0.5, ge=0.0)

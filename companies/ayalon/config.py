"""Ayalon (איילון) configuration.

Ayalon's archive (ayalon-ins.co.il/archive) is a Umbraco-backed site whose
`POST /api/policyarchive/search` endpoint returns EVERY document in the
entire archive (1,139 confirmed live 2026-08-12) in one response, tagged
with `categoryName`/`subjectName` - no per-category looping needed at all,
simpler than every other company plugin so far. See downloader.py for the
full data-flow explanation.

`CATEGORY_TO_DOMAIN` restricts the archive to the categories this platform
tracks, per explicit user decision (2026-08-12): "ביטוח חיים" (life),
"ביטוח מחלות קשות"/"ביטוח תאונות אישיות"/"ביטוח בריאות"/"ביטוח תאונות
אישיות - בריאות" all folded into "health" - same convention every other
company plugin already uses for critical-illness/accident coverage (see
Harel/Clal/Menorah config.py).

"ביטוח קולקטיב" (collective) is not a single domain on this site - it's a
grab-bag category whose own `subjectName` spans unrelated product lines
(דירה/home, רכב/car, חיים/life, בריאות/health, תאונות אישיות/accidents).
Per explicit user decision (2026-08-12), only the three subjects this
platform actually tracks are kept via `COLLECTIVE_SUBJECT_TO_DOMAIN`; דירה
and רכב are out of scope, same as every other company plugin excluding
home/car lines entirely.

"ביטוח משכנתא" (mortgage, added 2026-08-17 per explicit user request)
folded into `life` - confirmed live via the full archive dump, 10 items,
real appendix/policy content (e.g. "משכנתא לאדם עם מוגבלות מקצרת חיים
נספח 1120 מהדורה 10.2024", "דרור למשכנתא 1122 מהדורה 10.2024"). Same
don't-split-life-and-structure-cover precedent as every other company's
mortgage category this session (see companies/harel/config.py for the
fuller writeup) - this category also contains at least one pure
structural/property policy ("פוליסה לביטוח מבנה בבנקים למשכנתאות"), kept
in rather than split out. No "סיעוד"/long-term-care category exists
anywhere in the live archive - all 21 distinct categoryName values were
enumerated live and none match; genuinely not offered/listed here.
"""

from __future__ import annotations

from pydantic import Field

from core.plugins.base import CompanyConfig

# categoryName -> our domain, restricted to what this platform tracks.
CATEGORY_TO_DOMAIN: dict[str, str] = {
    "ביטוח חיים": "life",
    "ביטוח מחלות קשות": "health",
    "ביטוח תאונות אישיות": "health",
    "ביטוח בריאות": "health",
    "ביטוח תאונות אישיות - בריאות": "health",
    "ביטוח משכנתא": "life",
}

# "ביטוח קולקטיב" is handled separately from CATEGORY_TO_DOMAIN: it's kept
# only when its subjectName is one of these three (see module docstring).
COLLECTIVE_CATEGORY_NAME = "ביטוח קולקטיב"
COLLECTIVE_SUBJECT_TO_DOMAIN: dict[str, str] = {
    "חיים": "life",
    "בריאות": "health",
    "תאונות אישיות": "health",
}


class AyalonConfig(CompanyConfig):
    """Configuration for the Ayalon (איילון) insurance company plugin."""

    company_id: str = "ayalon"
    display_name: str = "איילון"

    base_url: str = "https://www.ayalon-ins.co.il"
    warmup_url: str = "https://www.ayalon-ins.co.il/"
    archive_page_url: str = "https://www.ayalon-ins.co.il/archive"
    search_api_url_fragment: str = "/api/policyarchive/search"

    request_timeout_seconds: float = 30.0
    download_delay_seconds: float = Field(default=0.3, ge=0.0)
    download_retry_max_attempts: int = Field(default=4, ge=1)
    download_retry_base_seconds: float = Field(default=3.0, ge=0.0)

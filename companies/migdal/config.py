"""Migdal-specific configuration.

Migdal's public policy-terms archive (my.migdal.co.il/support/policy-terms-arhcive)
serves a single flat list of ~1400 documents tagged with a "Department" taxonomy
term. There is no server-side category filter, so this module encodes the
Department -> domain mapping the platform filters on client-side.

The mapping below was confirmed against a live pull of the full department list
(2026-07-15) and reviewed with the business owner. Categories not listed here
(e.g. "חיסכון אישי" — personal savings) are intentionally excluded because they
are not health or life insurance products.
"""

from __future__ import annotations

from pydantic import Field

from core.plugins.base import CompanyConfig

# Departments that are unambiguously health-insurance products.
HEALTH_DEPARTMENTS: frozenset[str] = frozenset(
    {
        "ביטוח בריאות וסיעוד",
        "הרחבות ביטוחי בריאות",
        "ביטוח מחלות קשות",
        "אובדן כושר עבודה",
        "ביטוח תאונות אישיות",
        "נכות מתאונה",
    }
)

# Departments that are unambiguously life-insurance products.
LIFE_DEPARTMENTS: frozenset[str] = frozenset(
    {
        "ביטוח חיים עם חיסכון",
        "ביטוח למקרה מוות",
    }
)

# Group/collective policies spanning both domains — included under both,
# per business decision, since the API does not expose a finer split.
MIXED_DEPARTMENTS: frozenset[str] = frozenset({"קולקטיבים"})

TARGET_DEPARTMENTS: frozenset[str] = HEALTH_DEPARTMENTS | LIFE_DEPARTMENTS | MIXED_DEPARTMENTS


def classify_department(department_name: str) -> str | None:
    """Return 'health', 'life', 'mixed', or None if not a target department."""
    if department_name in HEALTH_DEPARTMENTS:
        return "health"
    if department_name in LIFE_DEPARTMENTS:
        return "life"
    if department_name in MIXED_DEPARTMENTS:
        return "mixed"
    return None


class MigdalConfig(CompanyConfig):
    """Configuration for the Migdal insurance company plugin."""

    company_id: str = "migdal"
    display_name: str = "מגדל"

    list_endpoint: str = "https://my.migdal.co.il/data/api/ContentData/FrontContentData/"
    blob_base_url: str = "https://storageblobwebprod.blob.core.windows.net/mediaprod/"

    request_timeout_seconds: float = 30.0
    download_delay_seconds: float = Field(default=0.2, ge=0.0)

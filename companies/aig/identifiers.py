"""Internal appendix identifiers for AIG documents.

AIG publishes no appendix numbers at all: not on its site (link text is a
product name plus an effective-date range) and not inside the PDFs (all 93
health/life documents scanned 2026-11-18, OCR included - no "נספח N", no
"תכנית N", no form/product code; the recurring "4910001" appears on
unrelated products). The public API and the deployment export identify a
document by company + appendix number, so every AIG document gets a stable
internal identifier instead:

    AIG-<PRODUCT>[-DISC]-<YYYY.MM | YYYY>       e.g. AIG-EXTRACARE-2023.08

derived only from the title, so a re-sync reproduces it. The "AIG-" prefix
marks it as ours, never AIG's. When a real customer-facing number turns up
(the policy's "דף פרטי הביטוח"), it should be added alongside, not instead.
"""

from __future__ import annotations

import re

# (pattern on the title, product code) - first match wins, so more specific
# rules come before the general ones they overlap with.
_PRODUCT_RULES: tuple[tuple[str, str], ...] = (
    (r"הוראת קבע.*אלמנטרי", "FORM-DEBIT-GENERAL"),
    (r"הוראת קבע.*חיים", "FORM-DEBIT-LIFE"),
    (r"הוראת קבע", "FORM-DEBIT"),
    (r"ייפוי כוח", "FORM-POWER-OF-ATTORNEY"),
    (r"מוטבים", "FORM-BENEFICIARIES"),
    (r"גיוון והכלה", "INFO-DIVERSITY"),
    (r"אזרחים ותיקים", "INFO-SENIORS"),
    (r"רשימת המחלות", "DISEASE-LIST"),
    (r"extra\s*care", "EXTRACARE"),
    (r"אופק לחיים", "OFEK-LACHAIM-PLUS"),
    (r"מוגבלות מקצרת חיים", "LIFE-SHORTENED-LIFE-EXPECTANCY"),
    (r"משכנתא.*(?:מקס|max)|(?:מקס|max).*משכנתא", "MORTGAGE-MAX"),
    (r"משכנתא", "MORTGAGE"),
    (r"בטחון לחיים", "BITACHON-LACHAIM"),
    (r"medicare.*(?:משלים|שב\"?ן)|(?:משלים|שב\"?ן).*medicare", "MEDICARE-SHABAN"),
    (r"medicare.*(?:שקל|השקל)|(?:שקל|השקל).*medicare", "MEDICARE-FIRST-SHEKEL"),
    (r"medicare", "MEDICARE"),
    (r"aig\s*medical", "MEDICAL"),
    (r"aig\s*surgery", "SURGERY"),
    (r"ניתוחים.*בישראל.*השתתפות עצמית", "SURGERY-IL-SHABAN-DEDUCTIBLE"),
    (r"ניתוחים.*בישראל.*(?:משלים|שב\"?ן)", "SURGERY-IL-SHABAN"),
    (r"ניתוחים.*בישראל", "SURGERY-IL-FIRST-SHEKEL"),
    (r"ניתוחים.*(?:בחו\"?ל|מחוץ לישראל)", "SURGERY-ABROAD"),
    (r"השתלות", "TRANSPLANTS"),
    (r"תרופות", "DRUGS"),
    (r"טכנולוגיות מתקדמות", "ADVANCED-TECH"),
    (r"ייעוץ.*בדיקות|אבחון", "DIAGNOSTICS"),
    (r"ליווי אישי", "MEDICAL-ESCORT"),
    (r"רפואה משלימה", "SERVICE-COMPLEMENTARY-MEDICINE"),
    (r"טיפולים רפואיים", "SERVICE-TREATMENTS"),
    (r"בריאות בסיסית", "HEALTH-BASIC"),
    (r"תנאים כלליים", "HEALTH-GENERAL-TERMS"),
    (r"ספר בריאות", "HEALTH-BOOK"),
    (r"ביטוח חיים", "LIFE-RISK"),
)
_DISCLOSURE = re.compile(r"גילוי נאות")

_HEBREW_MONTHS = {
    "ינואר": 1, "פברואר": 2, "מרץ": 3, "מרס": 3, "אפריל": 4, "מאי": 5, "יוני": 6,
    "יולי": 7, "אוגוסט": 8, "ספטמבר": 9, "אוקטובר": 10, "נובמבר": 11, "דצמבר": 12,
}
_D_M_Y = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})(?!\d)")
_M_Y = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{4})(?!\d)")
_HEB_M_Y = re.compile(
    r"(?:מ|ב)?(" + "|".join(sorted(_HEBREW_MONTHS, key=len, reverse=True)) + r")[\s\-]*(\d{4}|\d{2})(?!\d)"
)
_YEAR_ONLY = re.compile(r"(?:שנת|משנת)[\s\-]*(\d{4})")


def _year(text: str) -> int:
    value = int(text)
    return value + 2000 if value < 100 else value


def edition_from_title(title: str) -> str | None:
    """The FIRST date in the title (the edition's start), as YYYY.MM - or
    YYYY when only a year is given. Whichever date format appears earliest
    in the title wins."""
    candidates: list[tuple[int, str]] = []
    for m in _D_M_Y.finditer(title):
        candidates.append((m.start(), f"{_year(m.group(3)):04d}.{int(m.group(2)):02d}"))
    for m in _M_Y.finditer(title):
        candidates.append((m.start(), f"{int(m.group(2)):04d}.{int(m.group(1)):02d}"))
    for m in _HEB_M_Y.finditer(title):
        candidates.append((m.start(), f"{_year(m.group(2)):04d}.{_HEBREW_MONTHS[m.group(1)]:02d}"))
    for m in _YEAR_ONLY.finditer(title):
        candidates.append((m.start(), f"{int(m.group(1)):04d}"))
    if not candidates:
        return None
    return min(candidates)[1]


def product_code(title: str) -> str:
    for pattern, code in _PRODUCT_RULES:
        if re.search(pattern, title, re.IGNORECASE):
            return code
    return "GENERAL"


def internal_appendix_id(title: str) -> str:
    parts = ["AIG", product_code(title)]
    if _DISCLOSURE.search(title):
        parts.append("DISC")
    edition = edition_from_title(title)
    if edition:
        parts.append(edition)
    return "-".join(parts)


def unique_internal_id(title: str, taken: set[str]) -> str:
    """internal_appendix_id, suffixed -2, -3... if already taken (e.g. the
    same Extra Care edition saved from both the health and the life page).
    Adds the result to `taken`."""
    base = internal_appendix_id(title)
    candidate, n = base, 2
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
    taken.add(candidate)
    return candidate

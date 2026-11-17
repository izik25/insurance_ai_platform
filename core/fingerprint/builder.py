"""Pure-code Quantitative Insurance Fingerprint builder - no LLM, no
network, no DB access of its own. Combines a document's taxonomy
classification + Canonical Coverage Profile (both already computed in
Phase 2) into the scalar/enum/numeric feature set matching
DocumentFingerprint's columns, via core/fingerprint/parsers.py.

Deterministic: same input always produces the same output, so it's safely
re-runnable on every fingerprint_version bump without re-touching the LLM
pipeline at all. Takes plain typed arguments (not ORM rows) so it's
directly unit-testable without a live database.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from core.canonical.schema import CanonicalCoverageProfile
from core.fingerprint.parsers import (
    numbers_mentioned,
    parse_amount,
    parse_percentage,
    parse_period_to_days,
)

# Bumped whenever build_fingerprint's logic (or the parsers it calls)
# changes in a way that should invalidate document_pipeline_status.
# fingerprint_at for already-processed documents - same versioning
# convention as core/canonical/schema.py's PROFILE_VERSION/CODES_VERSION.
# v2: rule-based sanity checks below (period cross-checks against the
# question-bank answers, amount/age filtering, non-coverage-document flag),
# added after a 10-appendix manual review found these exact failure modes
# in v1 fingerprints.
FINGERPRINT_VERSION = "v2"

# The (exists, details) question-bank pair behind each period field.
_PERIOD_QUESTIONS = {
    "waiting": ("base.waiting_period_exists", "base.waiting_period_details"),
    "qualifying": ("base.qualifying_period_exists", "base.qualifying_period_details"),
    "survival": ("base.survival_period_exists", "base.survival_period_details"),
}
# "...ובלבד שהמבוטח לא נפטר במהלך 14 ימים" is a survival condition even when
# the policy (or an upstream LLM) calls it "המתנה" - confirmed live on
# Phoenix appendix 5451.
_SURVIVAL_MARKERS = re.compile(r"לא נפטר|בחיים|חי בתום|חיה בתום|הישרדות")
# Stricter than _SURVIVAL_MARKERS: wording in the document itself that the
# insured must stay alive (not the label "הישרדות", which an LLM answer may
# attach to the wrong clause).
_STAYS_ALIVE = re.compile(r"לא נפטר|בחיים|חי בתום|חיה בתום|יישאר|תישאר")
# "נפטר המבוטח בתוך 14 ימים ... תשלם החברה פיצוי" pays ON death - the
# opposite of a survival period (Direct 203/01).
_DEATH_BENEFIT = re.compile(r"נפטר|פטירה|מוות")
_QUALIFYING_MARKERS = re.compile(r"אכשרה")
_WAITING_MARKERS = re.compile(r"המתנה|המתנת")

# Amount strings that are not sums paid to the insured: deductibles,
# premiums, prices (AIG 6056's 5 ₪ monthly premium and Migdal 2356's
# 5,000 ₪ deductible both ended up as "benefit amounts" in v1).
_NOT_A_BENEFIT_MARKERS = re.compile(
    r"השתתפות עצמית|פרמיה|דמי ביטוח|דמי הביטוח|עלות הביטוח|עלות פוליסה|מחיר|תעריף"
)
# Per-unit limits (per day/treatment/consultation/month) stay in
# amount_raw but are excluded from the min/max range, so a 250 ₪-per-day
# limit never sits next to a 3,000,000 ₪ policy cap as if on one scale.
_PER_UNIT_MARKERS = re.compile(
    r"ליום|ללילה|לטיפול|להתייעצות|לביקור|לשעה|לחודש|בחודש"
    r"|(?:לכל|בכל) (?:התייעצות|טיפול|ביקור|יום|פגישה|מפגש)"
)

# Age text that describes premium bands, not who may join.
_PREMIUM_MARKERS = re.compile(r"פרמיה|דמי ביטוח|דמי הביטוח|תעריף|עלות|מחיר")
_ENTRY_MARKERS = re.compile(r"הצטרפות|להצטרף|מצטרף|כניסה|יבוטח|לבטח|יתקבל")
# A quote containing prices is a premium table, whatever else it says.
_PRICE_MARKERS = re.compile(r"פרמיה|דמי ביטוח|דמי הביטוח|תעריף|עלות|מחיר|שקלים|₪|ש\"ח|ש״ח")
_AGE_QUESTION = "base.age_limitations"

# Below this many FOUND answers the "document" answered almost nothing in
# the question bank - in practice a letter/notice/form rather than a
# coverage appendix (confirmed live: Ayalon's group-policy extension
# letter, 1 FOUND out of 27).
_MIN_FOUND_FOR_COVERAGE_DOCUMENT = 3


@dataclass
class FingerprintResult:
    main_category: str | None
    coverage_family: str | None
    coverage_subtype: str | None
    benefit_model: str | None
    target_population: str | None

    waiting_period_days: int | None
    waiting_period_raw: str | None
    qualifying_period_days: int | None
    qualifying_period_raw: str | None
    survival_period_days: int | None
    survival_period_raw: str | None

    min_entry_age: int | None
    max_entry_age: int | None
    termination_age: int | None
    age_raw: str | None

    benefit_type: str | None
    benefit_amount_min: float | None
    benefit_amount_max: float | None
    benefit_amount_currency: str | None
    amount_raw: list[str]
    benefit_percentage: float | None
    maximum_benefit: float | None
    deductible_amount: float | None

    covered_event_count: int
    major_exclusion_count: int
    special_condition_count: int

    raw_features: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def build_fingerprint(
    *,
    main_category: str | None,
    coverage_family: str | None,
    coverage_subtype: str | None,
    benefit_model: str | None,
    target_population: str | None,
    profile: CanonicalCoverageProfile,
    benefit_type_code: str | None,
    answer_statuses: dict[str, str] | None = None,
    answer_texts: dict[str, tuple[str | None, str | None]] | None = None,
) -> FingerprintResult:
    """`benefit_type_code` is the result of a RULE-BASED lookup
    (core.knowledge_base.registry.normalize_to_code(profile.benefit_type,
    "benefit_type")) done by the caller - a single scalar per document
    doesn't need an LLM fallback batch the way canonical-code SETS do
    (core/canonical/code_normalizer.py); if the rule-based lookup found
    nothing, the caller passes None and the raw text is kept as a fallback
    label instead of a code.

    `answer_statuses` (question_id -> FOUND/NOT_FOUND/...) and
    `answer_texts` (question_id -> (answer_text, evidence_text)) are this
    document's question-bank result. The question bank read the full
    document and quotes it, so it gates the profile's period and entry-age
    fields (see _sanitize_periods / _sanitize_entry_ages); it also flags
    non-coverage documents. Every value a sanity check drops or moves is
    recorded in raw_features["sanity_adjustments"], so nothing disappears
    silently.
    """
    adjustments: list[str] = []
    periods = _sanitize_periods(
        {
            "waiting": profile.waiting_period_text,
            "qualifying": profile.qualifying_period_text,
            "survival": profile.survival_period_text,
        },
        answer_statuses or {},
        answer_texts or {},
        adjustments,
    )
    waiting_days = parse_period_to_days(periods["waiting"])
    qualifying_days = parse_period_to_days(periods["qualifying"])
    survival_days = parse_period_to_days(periods["survival"])

    benefit_amounts = []
    for amount_text in profile.amounts:
        if _NOT_A_BENEFIT_MARKERS.search(amount_text):
            adjustments.append(f"amount excluded (deductible/premium): {amount_text}")
        elif _PER_UNIT_MARKERS.search(amount_text):
            adjustments.append(f"amount excluded from min/max (per-unit limit): {amount_text}")
        else:
            benefit_amounts.append(amount_text)
    amount_matches = [m for m in (parse_amount(a) for a in benefit_amounts) if m]
    amount_min = min((v for v, _c in amount_matches), default=None)
    amount_max = max((v for v, _c in amount_matches), default=None)
    amount_currency = next((c for _v, c in amount_matches if c), None)

    cap_matches = [m for m in (parse_amount(c) for c in profile.caps) if m]
    maximum_benefit = max((v for v, _c in cap_matches), default=None)

    deductible_amount = None
    if profile.deductible.applies and profile.deductible.amount_text:
        parsed = parse_amount(profile.deductible.amount_text)
        if parsed:
            deductible_amount = parsed[0]

    benefit_percentage = parse_percentage(profile.benefit_calculation)

    min_age, max_age = _sanitize_entry_ages(
        profile, answer_statuses or {}, answer_texts or {}, adjustments
    )

    found_count = sum(1 for status in (answer_statuses or {}).values() if status == "FOUND")
    is_coverage_document = (
        answer_statuses is None or found_count >= _MIN_FOUND_FOR_COVERAGE_DOCUMENT
    )

    return FingerprintResult(
        main_category=main_category,
        coverage_family=coverage_family,
        coverage_subtype=coverage_subtype,
        benefit_model=benefit_model,
        target_population=target_population,
        waiting_period_days=waiting_days,
        waiting_period_raw=periods["waiting"],
        qualifying_period_days=qualifying_days,
        qualifying_period_raw=periods["qualifying"],
        survival_period_days=survival_days,
        survival_period_raw=periods["survival"],
        min_entry_age=min_age,
        max_entry_age=max_age,
        termination_age=profile.age_restrictions.termination_age,
        age_raw=profile.age_restrictions.description,
        benefit_type=benefit_type_code or profile.benefit_type,
        benefit_amount_min=amount_min,
        benefit_amount_max=amount_max,
        benefit_amount_currency=amount_currency,
        amount_raw=list(profile.amounts),
        benefit_percentage=benefit_percentage,
        maximum_benefit=maximum_benefit,
        deductible_amount=deductible_amount,
        covered_event_count=len(profile.covered_events),
        major_exclusion_count=len(profile.exclusions_normalized),
        special_condition_count=len(profile.special_conditions),
        raw_features={
            "deductible": profile.deductible.model_dump(),
            "age_restrictions": profile.age_restrictions.model_dump(),
            "caps_raw": list(profile.caps),
            "is_coverage_document": is_coverage_document,
            "found_answer_count": found_count if answer_statuses is not None else None,
            "sanity_adjustments": adjustments,
        },
    )


def _unsupported_period_reason(
    slot: str,
    text: str,
    answer_statuses: dict[str, str],
    answer_texts: dict[str, tuple[str | None, str | None]],
) -> str | None:
    """Why the question bank does NOT back this period value, or None if it
    does (or there's no question-bank data to judge by)."""
    exists_q, details_q = _PERIOD_QUESTIONS[slot]
    details_status = answer_statuses.get(details_q)
    if details_status is None:
        return None
    if details_status in ("NOT_FOUND", "NOT_APPLICABLE"):
        return f"question bank found no {slot} period"

    support_parts = [
        part for q in (exists_q, details_q) for part in answer_texts.get(q, ()) if part
    ]
    if not support_parts:
        return None
    support = " ".join(support_parts)
    if slot == "survival" and _DEATH_BENEFIT.search(support) and not _STAYS_ALIVE.search(support):
        return "a payment-on-death clause, not a survival period"
    # The number must appear in the document quote itself, not just in the
    # model's wording of the answer: Harel 824's answer said "90 יום" while
    # its quote read "בת 00 יום" (the 90 existed elsewhere in the document,
    # in an unrelated claims deadline). Falls back to the answer text only
    # when there's no quote at all.
    quotes = " ".join(
        answer_texts[q][1]
        for q in (exists_q, details_q)
        if q in answer_texts and answer_texts[q][1]
    )
    days = parse_period_to_days(text)
    if days is not None and str(days) not in numbers_mentioned(quotes or support):
        return f"{days} days not found in the question-bank quote"
    return None


def _normalize_for_compare(text: str) -> str:
    return re.sub(r"[\s.,;:\"'״׳()\-]+", "", text)


def _sanitize_periods(
    periods: dict[str, str | None],
    answer_statuses: dict[str, str],
    answer_texts: dict[str, tuple[str | None, str | None]],
    adjustments: list[str],
) -> dict[str, str | None]:
    """Rule-based guards against the period mix-ups found in review:

    1. A survival condition ("ובלבד שהמבוטח לא נפטר...") sitting in the
       waiting/qualifying slot moves to survival (if survival is empty).
    2. The same text in two slots (Migdal 2356 / Clal 4151: the qualifying
       period copied into waiting) is kept only in the slot its own wording
       names, or dropped from both if it names neither.
    3. The question bank gates every remaining period: dropped when its
       "details" answer is NOT_FOUND/NOT_APPLICABLE, when the answer's own
       numbers don't include the same day count (Hachshara 49: a 30-day
       "waiting period" taken from a premium-notice clause while the answer
       quoted an unreadable "0? יום"), or - for survival - when the quoted
       clause pays on death instead of requiring the insured to stay alive
       (Direct 203/01).
    """
    result = dict(periods)

    for slot in ("waiting", "qualifying"):
        text = result[slot]
        if text and _SURVIVAL_MARKERS.search(text) and not _QUALIFYING_MARKERS.search(text):
            if not result["survival"]:
                result["survival"] = text
                adjustments.append(
                    f"{slot} period moved to survival (describes staying alive): {text}"
                )
            else:
                adjustments.append(
                    f"{slot} period dropped (describes staying alive; survival already set): {text}"
                )
            result[slot] = None

    slots = ("waiting", "qualifying", "survival")
    for i, first in enumerate(slots):
        for second in slots[i + 1 :]:
            text = result[first]
            if not text or not result[second]:
                continue
            if _normalize_for_compare(text) != _normalize_for_compare(result[second]):
                continue
            if _QUALIFYING_MARKERS.search(text):
                keep = "qualifying"
            elif _WAITING_MARKERS.search(text):
                keep = "waiting"
            elif _SURVIVAL_MARKERS.search(text):
                keep = "survival"
            else:
                keep = None
            for slot in (first, second):
                if slot != keep:
                    result[slot] = None
                    adjustments.append(
                        f"{slot} period dropped (same text as another period field): {text}"
                    )

    for slot in slots:
        if not result[slot]:
            continue
        reason = _unsupported_period_reason(slot, result[slot], answer_statuses, answer_texts)
        if reason:
            adjustments.append(f"{slot} period dropped ({reason}): {result[slot]}")
            result[slot] = None

    return result


def _sanitize_entry_ages(
    profile: CanonicalCoverageProfile,
    answer_statuses: dict[str, str],
    answer_texts: dict[str, tuple[str | None, str | None]],
    adjustments: list[str],
) -> tuple[int | None, int | None]:
    """Entry ages read off a premium table ("דמי הביטוח משתנים לפי קבוצות
    גיל עד גיל 66"), or a max entry age that is really the coverage end age,
    are dropped - only an explicit joining/entry limit counts. When the
    question bank answered the age question, its quote must itself state
    an entry limit and must not be a price table (AIG 6056 / Clal 4151)."""
    ages = profile.age_restrictions
    min_age, max_age = ages.min_age, ages.max_age
    if min_age is None and max_age is None:
        return None, None

    age_status = answer_statuses.get(_AGE_QUESTION)
    if age_status in ("NOT_FOUND", "NOT_APPLICABLE"):
        adjustments.append("entry ages dropped (question bank found no age limit)")
        return None, None
    evidence = (answer_texts.get(_AGE_QUESTION) or (None, None))[1]
    if evidence:
        if not _ENTRY_MARKERS.search(evidence) or _PRICE_MARKERS.search(evidence):
            adjustments.append(f"entry ages dropped (quote is not an entry limit): {evidence}")
            return None, None

    description = ages.description or ""
    mentions_entry = bool(_ENTRY_MARKERS.search(description))

    has_entry_ages = min_age is not None or max_age is not None
    if has_entry_ages and _PREMIUM_MARKERS.search(description) and not mentions_entry:
        adjustments.append(f"entry ages dropped (premium bands, not an entry limit): {description}")
        return None, None
    if max_age is not None and max_age == ages.termination_age and not mentions_entry:
        adjustments.append(f"max entry age dropped (same as coverage end age): {description}")
        max_age = None
    return min_age, max_age

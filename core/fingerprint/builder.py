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

from dataclasses import asdict, dataclass, field

from core.canonical.schema import CanonicalCoverageProfile
from core.fingerprint.parsers import parse_amount, parse_percentage, parse_period_to_days

# Bumped whenever build_fingerprint's logic (or the parsers it calls)
# changes in a way that should invalidate document_pipeline_status.
# fingerprint_at for already-processed documents - same versioning
# convention as core/canonical/schema.py's PROFILE_VERSION/CODES_VERSION.
FINGERPRINT_VERSION = "v1"


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
) -> FingerprintResult:
    """`benefit_type_code` is the result of a RULE-BASED lookup
    (core.knowledge_base.registry.normalize_to_code(profile.benefit_type,
    "benefit_type")) done by the caller - a single scalar per document
    doesn't need an LLM fallback batch the way canonical-code SETS do
    (core/canonical/code_normalizer.py); if the rule-based lookup found
    nothing, the caller passes None and the raw text is kept as a fallback
    label instead of a code.
    """
    waiting_days = parse_period_to_days(profile.waiting_period_text)
    qualifying_days = parse_period_to_days(profile.qualifying_period_text)
    survival_days = parse_period_to_days(profile.survival_period_text)

    amount_matches = [m for m in (parse_amount(a) for a in profile.amounts) if m]
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

    return FingerprintResult(
        main_category=main_category,
        coverage_family=coverage_family,
        coverage_subtype=coverage_subtype,
        benefit_model=benefit_model,
        target_population=target_population,
        waiting_period_days=waiting_days,
        waiting_period_raw=profile.waiting_period_text,
        qualifying_period_days=qualifying_days,
        qualifying_period_raw=profile.qualifying_period_text,
        survival_period_days=survival_days,
        survival_period_raw=profile.survival_period_text,
        min_entry_age=profile.age_restrictions.min_age,
        max_entry_age=profile.age_restrictions.max_age,
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
        },
    )

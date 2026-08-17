"""Export the DB to a single CSV for the "Part A" deployment hand-off to the
frontend/deployment developer, who needs the data as a flat file rather than
live DB queries.

Writes one file (default: data/exports/appendices_full.csv) with exactly one
row per document ("appendix") - no repeated appendix rows anywhere - combining
every 1:1 table (Document, DocumentExtraction including its extracted tables,
DocumentClassification, DocumentCanonicalProfile, DocumentFingerprint) plus
the 1:many tables (question answers, canonical codes, additional findings,
cross-company matches) serialized as JSON in a single cell each, so the whole
"everything about this appendix" picture is one row.

Excludes the leftover test_company_* rows left behind by the test suite.

Usage: python scripts/export_deployment_csv.py [--out-dir DIR] [--company migdal,phoenix]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from core.config.settings import get_settings  # noqa: E402
from core.database.models import (  # noqa: E402
    Document,
    DocumentAdditionalFinding,
    DocumentCanonicalCode,
    DocumentCanonicalProfile,
    DocumentClassification,
    DocumentExtraction,
    DocumentFingerprint,
    DocumentMatch,
    DocumentQuestionAnswer,
)
from core.database.session import session_scope  # noqa: E402
from core.utils.logging import configure_logging, get_logger  # noqa: E402

logger = get_logger(__name__)

TEST_COMPANY_PREFIX = "test_company"


def _real_companies(session: Session, companies: list[str] | None) -> list[str]:
    query = select(Document.company_id).distinct()
    ids = [c for c in session.scalars(query) if not c.startswith(TEST_COMPANY_PREFIX)]
    if companies:
        wanted = set(companies)
        ids = [c for c in ids if c in wanted]
    return sorted(ids)


def _join_list(values: list | None) -> str:
    if not values:
        return ""
    return "; ".join(str(v) for v in values)


def _json_cell(value) -> str:
    if not value:
        return ""
    return json.dumps(value, ensure_ascii=False)


def _question_answers_json(session: Session, document_id: str) -> str:
    rows = session.scalars(
        select(DocumentQuestionAnswer).where(DocumentQuestionAnswer.document_id == document_id)
    )
    return _json_cell(
        [
            {
                "question_id": r.question_id,
                "question_scope": r.question_scope,
                "status": r.status,
                "answer_text": r.answer_text,
                "evidence_text": r.evidence_text,
                "evidence_page": r.evidence_page,
                "confidence": r.confidence,
            }
            for r in rows
        ]
    )


def _canonical_codes_json(session: Session, document_id: str) -> str:
    rows = session.scalars(
        select(DocumentCanonicalCode).where(DocumentCanonicalCode.document_id == document_id)
    )
    return _json_cell(
        [{"code_category": r.code_category, "code": r.code, "raw_phrase": r.raw_phrase} for r in rows]
    )


def _additional_findings_json(session: Session, document_id: str) -> str:
    rows = session.scalars(
        select(DocumentAdditionalFinding).where(DocumentAdditionalFinding.document_id == document_id)
    )
    return _json_cell(
        [{"finding_text": r.finding_text, "related_field": r.related_field} for r in rows]
    )


def _matches_json(session: Session, document_id: str) -> str:
    rows = session.scalars(
        select(DocumentMatch).where(DocumentMatch.document_id == document_id)
    )
    return _json_cell(
        [
            {
                "matched_document_id": r.matched_document_id,
                "similarity_score": r.similarity_score,
                "status": r.status,
                "final_score": r.final_score,
            }
            for r in rows
        ]
    )


def _write_full_csv(session: Session, companies: list[str], out_path: Path) -> int:
    fieldnames = [
        # documents
        "document_id",
        "company_id",
        "original_file_name",
        "file_path",
        "domain",
        "appendix_number",
        "appendix_name",
        "department_name",
        "marketing_start_date",
        "marketing_end_date",
        "is_active",
        "pages_count",
        "extraction_method",
        "created_date",
        # document_extractions (tables/raw_extraction excluded - see appendix_tables.csv)
        "extraction_coverage_type",
        "extraction_coverage_name",
        "extraction_eligibility_conditions",
        "extraction_insurance_amounts",
        "extraction_qualifying_period",
        "extraction_waiting_period",
        "extraction_exclusions",
        "extraction_age_range",
        "extraction_restrictions",
        "extraction_disease_count",
        "extraction_disease_list",
        "extraction_survival_period",
        "extraction_tables",
        # document_classifications
        "classification_taxonomy_version",
        "classification_category_id",
        "classification_main_category",
        "classification_coverage_family",
        "classification_coverage_subtype",
        "classification_coverage_variant",
        "classification_benefit_model",
        "classification_target_population",
        "classification_alternative_categories",
        "classification_confidence",
        "classification_evidence",
        # document_canonical_profiles
        "profile_insured_event",
        "profile_covered_events",
        "profile_covered_conditions",
        "profile_exclusions_normalized",
        "profile_limitations",
        "profile_eligibility_normalized",
        "profile_waiting_period_days",
        "profile_qualifying_period_days",
        "profile_survival_period_days",
        "profile_benefit_type",
        "profile_benefit_calculation",
        "profile_amounts",
        "profile_caps",
        "profile_deductible",
        "profile_age_restrictions",
        "profile_pre_existing_condition_rules",
        "profile_claim_requirements",
        "profile_definitions",
        "profile_extensions",
        "profile_special_conditions",
        "profile_termination_rules",
        "profile_additional_findings_summary",
        # document_fingerprints
        "fingerprint_main_category",
        "fingerprint_coverage_family",
        "fingerprint_coverage_subtype",
        "fingerprint_benefit_model",
        "fingerprint_target_population",
        "fingerprint_waiting_period_days",
        "fingerprint_waiting_period_raw",
        "fingerprint_qualifying_period_days",
        "fingerprint_qualifying_period_raw",
        "fingerprint_survival_period_days",
        "fingerprint_survival_period_raw",
        "fingerprint_min_entry_age",
        "fingerprint_max_entry_age",
        "fingerprint_termination_age",
        "fingerprint_age_raw",
        "fingerprint_benefit_type",
        "fingerprint_benefit_amount_min",
        "fingerprint_benefit_amount_max",
        "fingerprint_benefit_amount_currency",
        "fingerprint_amount_raw",
        "fingerprint_benefit_percentage",
        "fingerprint_maximum_benefit",
        "fingerprint_deductible_amount",
        "fingerprint_covered_event_count",
        "fingerprint_major_exclusion_count",
        "fingerprint_special_condition_count",
        # 1:many, serialized as JSON
        "question_answers",
        "canonical_codes",
        "additional_findings",
        "cross_company_matches",
    ]

    row_count = 0
    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        query = (
            select(
                Document,
                DocumentExtraction,
                DocumentClassification,
                DocumentCanonicalProfile,
                DocumentFingerprint,
            )
            .outerjoin(DocumentExtraction, DocumentExtraction.document_id == Document.id)
            .outerjoin(DocumentClassification, DocumentClassification.document_id == Document.id)
            .outerjoin(DocumentCanonicalProfile, DocumentCanonicalProfile.document_id == Document.id)
            .outerjoin(DocumentFingerprint, DocumentFingerprint.document_id == Document.id)
            .where(Document.company_id.in_(companies))
            .order_by(Document.company_id, Document.id)
        )

        for document, extraction, classification, profile, fingerprint in session.execute(query):
            row = {
                "document_id": document.id,
                "company_id": document.company_id,
                "original_file_name": document.original_file_name,
                "file_path": document.file_path,
                "domain": document.domain,
                "appendix_number": _join_list(document.appendix_number),
                "appendix_name": document.appendix_name,
                "department_name": document.department_name,
                "marketing_start_date": document.marketing_start_date,
                "marketing_end_date": document.marketing_end_date,
                "is_active": document.is_active,
                "pages_count": document.pages_count,
                "extraction_method": document.extraction_method,
                "created_date": document.created_date,
                "question_answers": _question_answers_json(session, document.id),
                "canonical_codes": _canonical_codes_json(session, document.id),
                "additional_findings": _additional_findings_json(session, document.id),
                "cross_company_matches": _matches_json(session, document.id),
            }

            if extraction is not None:
                row.update(
                    {
                        "extraction_coverage_type": extraction.coverage_type,
                        "extraction_coverage_name": extraction.coverage_name,
                        "extraction_eligibility_conditions": extraction.eligibility_conditions,
                        "extraction_insurance_amounts": _join_list(extraction.insurance_amounts),
                        "extraction_qualifying_period": extraction.qualifying_period,
                        "extraction_waiting_period": extraction.waiting_period,
                        "extraction_exclusions": _join_list(extraction.exclusions),
                        "extraction_age_range": extraction.age_range,
                        "extraction_restrictions": _join_list(extraction.restrictions),
                        "extraction_disease_count": extraction.disease_count,
                        "extraction_disease_list": _join_list(extraction.disease_list),
                        "extraction_survival_period": extraction.survival_period,
                        "extraction_tables": _json_cell((extraction.tables or {}).get("tables")),
                    }
                )

            if classification is not None:
                row.update(
                    {
                        "classification_taxonomy_version": classification.taxonomy_version,
                        "classification_category_id": classification.category_id,
                        "classification_main_category": classification.main_category,
                        "classification_coverage_family": classification.coverage_family,
                        "classification_coverage_subtype": classification.coverage_subtype,
                        "classification_coverage_variant": classification.coverage_variant,
                        "classification_benefit_model": classification.benefit_model,
                        "classification_target_population": classification.target_population,
                        "classification_alternative_categories": _json_cell(
                            classification.alternative_categories
                        ),
                        "classification_confidence": classification.confidence,
                        "classification_evidence": classification.evidence,
                    }
                )

            if profile is not None:
                row.update(
                    {
                        "profile_insured_event": profile.insured_event,
                        "profile_covered_events": _json_cell(profile.covered_events),
                        "profile_covered_conditions": _json_cell(profile.covered_conditions),
                        "profile_exclusions_normalized": _json_cell(profile.exclusions_normalized),
                        "profile_limitations": _json_cell(profile.limitations),
                        "profile_eligibility_normalized": profile.eligibility_normalized,
                        "profile_waiting_period_days": profile.waiting_period_days,
                        "profile_qualifying_period_days": profile.qualifying_period_days,
                        "profile_survival_period_days": profile.survival_period_days,
                        "profile_benefit_type": profile.benefit_type,
                        "profile_benefit_calculation": profile.benefit_calculation,
                        "profile_amounts": _json_cell(profile.amounts),
                        "profile_caps": _json_cell(profile.caps),
                        "profile_deductible": _json_cell(profile.deductible),
                        "profile_age_restrictions": _json_cell(profile.age_restrictions),
                        "profile_pre_existing_condition_rules": profile.pre_existing_condition_rules,
                        "profile_claim_requirements": _json_cell(profile.claim_requirements),
                        "profile_definitions": _json_cell(profile.definitions),
                        "profile_extensions": _json_cell(profile.extensions),
                        "profile_special_conditions": _json_cell(profile.special_conditions),
                        "profile_termination_rules": profile.termination_rules,
                        "profile_additional_findings_summary": profile.additional_findings_summary,
                    }
                )

            if fingerprint is not None:
                row.update(
                    {
                        "fingerprint_main_category": fingerprint.main_category,
                        "fingerprint_coverage_family": fingerprint.coverage_family,
                        "fingerprint_coverage_subtype": fingerprint.coverage_subtype,
                        "fingerprint_benefit_model": fingerprint.benefit_model,
                        "fingerprint_target_population": fingerprint.target_population,
                        "fingerprint_waiting_period_days": fingerprint.waiting_period_days,
                        "fingerprint_waiting_period_raw": fingerprint.waiting_period_raw,
                        "fingerprint_qualifying_period_days": fingerprint.qualifying_period_days,
                        "fingerprint_qualifying_period_raw": fingerprint.qualifying_period_raw,
                        "fingerprint_survival_period_days": fingerprint.survival_period_days,
                        "fingerprint_survival_period_raw": fingerprint.survival_period_raw,
                        "fingerprint_min_entry_age": fingerprint.min_entry_age,
                        "fingerprint_max_entry_age": fingerprint.max_entry_age,
                        "fingerprint_termination_age": fingerprint.termination_age,
                        "fingerprint_age_raw": fingerprint.age_raw,
                        "fingerprint_benefit_type": fingerprint.benefit_type,
                        "fingerprint_benefit_amount_min": fingerprint.benefit_amount_min,
                        "fingerprint_benefit_amount_max": fingerprint.benefit_amount_max,
                        "fingerprint_benefit_amount_currency": fingerprint.benefit_amount_currency,
                        "fingerprint_amount_raw": _join_list(fingerprint.amount_raw),
                        "fingerprint_benefit_percentage": fingerprint.benefit_percentage,
                        "fingerprint_maximum_benefit": fingerprint.maximum_benefit,
                        "fingerprint_deductible_amount": fingerprint.deductible_amount,
                        "fingerprint_covered_event_count": fingerprint.covered_event_count,
                        "fingerprint_major_exclusion_count": fingerprint.major_exclusion_count,
                        "fingerprint_special_condition_count": fingerprint.special_condition_count,
                    }
                )

            writer.writerow(row)
            row_count += 1
    return row_count


def main() -> None:
    arg_parser = argparse.ArgumentParser(description=__doc__)
    arg_parser.add_argument("--out-dir", type=str, default=None)
    arg_parser.add_argument("--company", type=str, default=None)
    args = arg_parser.parse_args()

    settings = get_settings()
    configure_logging(settings)

    out_dir = Path(args.out_dir) if args.out_dir else settings.data_dir / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)

    requested_companies = [c.strip() for c in args.company.split(",")] if args.company else None

    with session_scope() as session:
        companies = _real_companies(session, requested_companies)
        if not companies:
            logger.error("No matching companies found.")
            return
        logger.info("Exporting companies: %s", ", ".join(companies))

        full_path = out_dir / "appendices_full.csv"
        full_rows = _write_full_csv(session, companies, full_path)
        logger.info("Wrote %s (%d rows)", full_path, full_rows)


if __name__ == "__main__":
    main()

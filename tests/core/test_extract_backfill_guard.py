from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from core.extraction.schema import PolicyExtraction

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "extract_documents.py"
_spec = importlib.util.spec_from_file_location("extract_documents_under_test", _SCRIPT)
extract_documents = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = extract_documents
_spec.loader.exec_module(extract_documents)


class _Session:
    def __init__(self, document: SimpleNamespace) -> None:
        self.document = document

    def get(self, _model: object, _id: str) -> SimpleNamespace:
        return self.document


def _backfill(company_id: str, llm_numbers: list[str]) -> list[str]:
    document = SimpleNamespace(company_id=company_id, appendix_number=[], appendix_name=None)
    extraction = PolicyExtraction(appendix_number=llm_numbers)
    extract_documents._backfill_appendix_metadata(_Session(document), "doc", extraction)
    return document.appendix_number


def test_aig_section_numbers_are_never_backfilled() -> None:
    """2026-11-18: 42 AIG documents had LLM-invented numbers like "1", "1,2", "4.1"."""
    assert _backfill("aig", ["1", "2"]) == []
    assert _backfill("aig", ["4.1", "5.2"]) == []
    assert _backfill("aig", ["1", "3095"]) == ["3095"]


def test_other_companies_keep_short_numbers() -> None:
    assert _backfill("hachshara", ["49"]) == ["49"]

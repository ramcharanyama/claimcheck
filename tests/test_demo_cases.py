"""Every v3 demo case: quotes are on their pages, and the full pipeline (mock mode, ground-truth
facts) gives the expected rule results, paperwork checks, verdict and payout."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.documents.evidence import check_citation
from src.documents.ocr import is_tesseract_available
from src.ai.claim_loader import load_claim_document
from src.pipeline import analyze
from src.schemas import SourceRef

ROOT = Path(__file__).resolve().parent.parent
CASES = sorted(p for p in (ROOT / "data" / "demo_cases").iterdir() if p.is_dir())
POLICY = (ROOT / "data" / "synthetic_policy.pdf").read_bytes()


def _status(f):
    return str(getattr(f.status, "value", f.status))


def test_nine_demo_cases_exist():
    assert len(CASES) == 9


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.name)
def test_fact_quotes_are_on_their_pages(case):
    expected = json.loads((case / "expected.json").read_text())
    facts = json.loads((case / "ground_truth_facts.json").read_text())
    docs = {}
    for fact in facts:
        doc = fact["source"]["document"]
        if doc in expected["scanned_documents"] and not is_tesseract_available():
            continue
        if doc not in docs:
            docs[doc] = load_claim_document(case / doc)
        assert check_citation(docs[doc], SourceRef(**fact["source"])).verified, (case.name, fact["name"])


@pytest.mark.parametrize("case", CASES, ids=lambda p: p.name)
def test_pipeline_gives_expected_results(case):
    expected = json.loads((case / "expected.json").read_text())
    pdfs = {p.name: p.read_bytes() for p in sorted(case.glob("*.pdf"))}
    result = analyze(POLICY, pdfs, mode="mock", packet_id=case.name, claim_type=expected["claim_type"])
    assert result.errors == [], result.errors

    findings = {f.rule_id: f for f in result.findings}
    for rule_id, (status, impact) in expected["expected_rules"].items():
        assert rule_id in findings, (case.name, rule_id)
        assert _status(findings[rule_id]) == status, (case.name, rule_id, findings[rule_id].explanation)
        if impact is not None:
            assert findings[rule_id].estimated_impact == pytest.approx(impact), (case.name, rule_id)

    checks = {c.rule_id: _status(c) for c in result.document_checks}
    for check_id, status in expected["expected_checks"].items():
        assert checks.get(check_id) == status, (case.name, check_id, checks)

    assert result.payout["verdict"] == expected["expected_verdict"], (case.name, result.payout)
    assert result.payout["payable"] == pytest.approx(expected["expected_payable"]), (case.name, result.payout)

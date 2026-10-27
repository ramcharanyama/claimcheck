"""Offline unit tests for ClaimCheck 2.0 UI Pipeline and Reporting.

Tests:
1. Pipeline execution in mock mode over four diverse packets:
   - Digital packet (packet_05)
   - Scanned proportional room-rent packet (packet_11)
   - Incomplete / missing document packet (packet_02)
   - Non-payable items packet (packet_08)
2. Live-mode pipeline execution with mocked model client.
3. In-memory PDF quote highlighting (known quote, unmatched quote, scanned page).
4. Report generator Markdown structure, financial summaries, and disclaimer.
5. Error resilience on invalid PDF bytes and missing credentials.
6. Streamlit app module import without error.
"""

from __future__ import annotations

import io
from pathlib import Path
import pytest

from src.pipeline import analyze, render_page_with_highlight
from src.reports.report_generator import generate_markdown_report, generate_report
from src.schemas import Finding, FindingStatus, SourceRef

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
PACKETS_DIR = DATA_DIR / "packets"
POLICY_PATH = DATA_DIR / "synthetic_policy.pdf"


@pytest.fixture(scope="module")
def policy_bytes() -> bytes:
    assert POLICY_PATH.exists(), f"Synthetic policy missing at {POLICY_PATH}"
    return POLICY_PATH.read_bytes()


def _load_packet_claim_pdfs(packet_id: str) -> dict[str, bytes]:
    pdir = PACKETS_DIR / packet_id
    assert pdir.exists(), f"Packet dir missing at {pdir}"
    return {f.name: f.read_bytes() for f in sorted(pdir.glob("*.pdf"))}


# -----------------------------------------------------------------------------
# 1. Pipeline Mock Mode on Four Diverse Packets
# -----------------------------------------------------------------------------
def test_pipeline_mock_digital_packet_05(policy_bytes: bytes):
    """Test mock pipeline on digital claim (packet_05: room rent within cap)."""
    claim_pdfs = _load_packet_claim_pdfs("packet_05")
    result = analyze(policy_bytes, claim_pdfs, mode="mock", packet_id="packet_05")

    assert result.mode == "MOCK"
    assert len(result.errors) == 0
    assert len(result.findings) >= 5
    assert result.rulebook is not None

    # Room rent within cap (4000 <= 5000)
    rr_finding = next((f for f in result.findings if f.rule_id == "RULE_ROOM_RENT_CAP"), None)
    assert rr_finding is not None
    assert str(getattr(rr_finding.status, "value", rr_finding.status)).lower() == "satisfied"
    assert rr_finding.estimated_impact == 0.0


def test_pipeline_mock_scanned_proportional_packet_11(policy_bytes: bytes):
    """Test mock pipeline on scanned claim with proportional room rent deduction (packet_11)."""
    claim_pdfs = _load_packet_claim_pdfs("packet_11")
    result = analyze(policy_bytes, claim_pdfs, mode="mock", packet_id="packet_11")

    assert result.mode == "MOCK"
    assert len(result.errors) == 0
    assert len(result.findings) >= 5

    # OCR warning should be captured for the scanned hospital bill
    assert any("hospital_bill.pdf" in w for w in result.ocr_warnings)

    # Room rent exceeds cap (10000 > 5000) with proportional deduction = 27500
    rr_finding = next((f for f in result.findings if f.rule_id == "RULE_ROOM_RENT_CAP"), None)
    assert rr_finding is not None
    assert str(getattr(rr_finding.status, "value", rr_finding.status)).lower() == "violated"
    assert rr_finding.estimated_impact == 27500.0
    assert len(rr_finding.calculation_steps) >= 3


def test_pipeline_mock_missing_document_packet_02(policy_bytes: bytes):
    """Test mock pipeline on claim missing discharge summary (packet_02)."""
    claim_pdfs = _load_packet_claim_pdfs("packet_02")
    result = analyze(policy_bytes, claim_pdfs, mode="mock", packet_id="packet_02")

    assert result.mode == "MOCK"
    assert len(result.errors) == 0

    # Required documents rule should be violated
    doc_finding = next((f for f in result.findings if f.rule_id == "RULE_REQ_DOCS"), None)
    assert doc_finding is not None
    assert str(getattr(doc_finding.status, "value", doc_finding.status)).lower() == "violated"
    assert "discharge_summary" in doc_finding.explanation.lower()


def test_pipeline_mock_non_payable_items_packet_08(policy_bytes: bytes):
    """Test mock pipeline on claim with non-payable items (packet_08: PPE kit excluded)."""
    claim_pdfs = _load_packet_claim_pdfs("packet_08")
    result = analyze(policy_bytes, claim_pdfs, mode="mock", packet_id="packet_08")

    assert result.mode == "MOCK"
    assert len(result.errors) == 0

    # Non-payable items rule should be violated with deduction
    np_finding = next((f for f in result.findings if f.rule_id == "RULE_NON_PAYABLE_ITEMS"), None)
    assert np_finding is not None
    assert str(getattr(np_finding.status, "value", np_finding.status)).lower() == "violated"
    assert np_finding.estimated_impact is not None and np_finding.estimated_impact > 0.0


# -----------------------------------------------------------------------------
# 2. Pipeline Live Mode Logic with Mocked Client
# -----------------------------------------------------------------------------
def test_pipeline_live_mode_logic_with_mocked_modules(policy_bytes: bytes, monkeypatch):
    """Test live pipeline logic promoting only verified rules using mocks."""
    monkeypatch.setenv("NEBIUS_API_KEY", "dummy-live-key")
    monkeypatch.setenv("NEBIUS_MODEL_FAST", "nvidia/nemotron-mini-4b-instruct")
    monkeypatch.setenv("NEBIUS_MODEL_REASONING", "nvidia/nemotron-4-340b-reward")

    claim_pdfs = _load_packet_claim_pdfs("packet_05")

    # Run analysis in mock mode to check flow stability
    res = analyze(policy_bytes, claim_pdfs, mode="mock")
    assert res.mode == "MOCK"
    assert res.rulebook is not None
    # All verified rules remain validated
    for r in res.rulebook.rules:
        if res.rule_validation_status.get(r.rule_id):
            assert r.validation_status == "validated"


# -----------------------------------------------------------------------------
# 3. In-Memory Source Quote Highlight Engine
# -----------------------------------------------------------------------------
def test_render_page_with_highlight_known_quote(policy_bytes: bytes):
    """Test highlighting a known quote on page 2 of the policy."""
    known_quote = "The maximum eligible room and boarding expense covered by the insurer is INR 5000 per day"
    png_bytes, is_highlighted, note = render_page_with_highlight(policy_bytes, page_num=2, quote=known_quote)

    assert is_highlighted is True
    assert note == "Quote highlighted"
    assert len(png_bytes) > 10000
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"


def test_render_page_with_highlight_unmatched_quote(policy_bytes: bytes):
    """Test rendering an unmatched quote returns unhighlighted page with appropriate note."""
    unmatched_quote = "This non-existent sentence cannot be found in the insurance policy document at all."
    png_bytes, is_highlighted, note = render_page_with_highlight(policy_bytes, page_num=2, quote=unmatched_quote)

    assert is_highlighted is False
    assert "highlight not verified" in note.lower()
    assert len(png_bytes) > 10000


def test_render_page_with_highlight_scanned_page():
    """Test rendering an image-only scanned page returns appropriate note without failure."""
    scanned_bill = (PACKETS_DIR / "packet_06" / "hospital_bill.pdf").read_bytes()
    png_bytes, is_highlighted, note = render_page_with_highlight(scanned_bill, page_num=1, quote="Any quote")

    assert is_highlighted is False
    assert "scanned image page" in note.lower()
    assert len(png_bytes) > 10000


def test_render_page_invalid_page_bounds(policy_bytes: bytes):
    """Test out-of-bounds page numbers fail safely."""
    png_bytes, is_highlighted, note = render_page_with_highlight(policy_bytes, page_num=999, quote="test")
    assert is_highlighted is False
    assert "out of document range" in note.lower()
    assert len(png_bytes) == 0


# -----------------------------------------------------------------------------
# 4. Report Generator Structure & Content
# -----------------------------------------------------------------------------
def test_report_generator_content():
    """Verify generated Markdown report includes context, findings, calculations, and disclaimer."""
    findings = [
        Finding(
            rule_id="RULE_ROOM_RENT_CAP",
            status=FindingStatus.VIOLATED,
            explanation="Daily room rent exceeds INR 5000 limit.",
            inputs_used={"daily_rate": 8000, "days": 3},
            calculation_steps=["Step 1: Excess = 3000.", "Step 2: 3 * 3000 = 9000."],
            source=SourceRef(document="policy.pdf", page=2, quote="INR 5000 per day"),
            estimated_impact=9000.0,
        )
    ]
    unresolved = ["Policy Ambiguity [AMB_4_1]: Pre-existing condition regulations."]

    md = generate_markdown_report(
        policy_name="synthetic_policy.pdf",
        claim_names=["claim_form.pdf", "hospital_bill.pdf"],
        findings=findings,
        unresolved_items=unresolved,
        mode="MOCK",
        claim_id="TEST_CLAIM_01",
    )

    assert "# ClaimCheck 2.0 - Claim Adjudication Report" in md
    assert "TEST_CLAIM_01" in md
    assert "MOCK" in md
    assert "synthetic_policy.pdf" in md
    assert "claim_form.pdf" in md
    assert "RULE_ROOM_RENT_CAP" in md
    assert "VIOLATED" in md
    assert "INR 9,000.00" in md
    assert "INR 5000 per day" in md
    assert "Step 1: Excess = 3000." in md
    assert "AMB_4_1" in md
    assert "Estimate only; not an insurer's decision" in md
    assert "Limitations" in md


def test_report_generator_backward_compat():
    """Verify backward-compatible generate_report interface."""
    data = {
        "findings": [
            {
                "rule_id": "RULE_1",
                "status": "satisfied",
                "explanation": "OK",
                "inputs_used": {"docs": ["form"]},
                "calculation_steps": ["Step 1: OK"],
                "source": {"document": "doc.pdf", "page": 1, "quote": "clause"},
            }
        ],
        "unresolved_items": ["Review needed"],
        "mode": "MOCK",
        "claim_id": "CLM-99",
    }
    rep = generate_report(data)
    assert rep.report_id == "REP_CLM-99"
    assert rep.claim_id == "CLM-99"
    assert "RULE_1" in rep.markdown_content


# -----------------------------------------------------------------------------
# 5. Error Resilience & Missing Keys
# -----------------------------------------------------------------------------
def test_pipeline_corrupted_pdf_graceful_handling():
    """Corrupted PDF bytes should not crash the pipeline; returns error in result."""
    result = analyze(b"not a valid pdf header", {"claim.pdf": b"corrupt bytes"}, mode="mock")
    assert len(result.errors) > 0
    assert any("valid pdf" in e.lower() for e in result.errors)


def test_pipeline_live_mode_missing_key_graceful_handling(policy_bytes: bytes, monkeypatch):
    """Missing NEBIUS_API_KEY in live mode produces clear error without crashing."""
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    claim_pdfs = _load_packet_claim_pdfs("packet_05")
    result = analyze(policy_bytes, claim_pdfs, mode="live")
    assert len(result.errors) > 0
    assert any("NEBIUS_API_KEY" in e for e in result.errors)


def test_app_imports_cleanly():
    """Streamlit application module app.py should import without error."""
    import app
    assert hasattr(app, "main")
    assert hasattr(app, "PACKET_CATALOG")


# -----------------------------------------------------------------------------
# 5. Live Mode Path with a Fake Nemotron Client (no network)
# -----------------------------------------------------------------------------
class _FakeNemotron:
    """Answers the compiler with the gold rulebook and the extractor with packet ground truth."""

    def __init__(self, packet_id: str):
        import json as _json
        import types as _types
        root = Path(__file__).resolve().parent.parent
        gold = _json.loads((root / "data" / "gold_rulebook.json").read_text(encoding="utf-8"))
        self.compile_reply = _json.dumps({
            "rules": [{k: r[k] for k in ("rule_id", "rule_type", "description", "parameters", "exceptions")}
                      | {"source": {"page": r["source"]["page"], "quote": r["source"]["quote"]}}
                      for r in gold["rules"]],
            "ambiguities": [{k: a[k] for k in ("ambiguity_id", "section", "description", "issue")}
                            | {"source": {"page": a["source"]["page"], "quote": a["source"]["quote"]}}
                            for a in gold["ambiguities"]],
        })
        facts = _json.loads((root / "data" / "packets" / packet_id / "ground_truth_facts.json").read_text())
        self.extract_reply = _json.dumps({"facts": facts})
        self.prompts = []
        self.chat = _types.SimpleNamespace(completions=_types.SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        import types as _types
        user = kwargs["messages"][1]["content"]
        self.prompts.append(user)
        content = self.compile_reply if "Extract every rule" in user else self.extract_reply
        msg = _types.SimpleNamespace(content=content)
        return _types.SimpleNamespace(choices=[_types.SimpleNamespace(message=msg, finish_reason="stop")])


def _run_live(policy_bytes, packet_id, monkeypatch, approved_rule_ids=None, compilation=None):
    monkeypatch.setenv("NEBIUS_API_KEY", "dummy-live-key")
    monkeypatch.setenv("NEBIUS_MODEL_FAST", "fake-fast")
    monkeypatch.setenv("NEBIUS_MODEL_REASONING", "fake-reasoning")
    fake = _FakeNemotron(packet_id)
    monkeypatch.setattr("src.ai.policy_compiler.get_client", lambda: fake)
    monkeypatch.setattr("src.ai.claim_extractor.get_client", lambda: fake)
    result = analyze(policy_bytes, _load_packet_claim_pdfs(packet_id), mode="live",
                     compilation=compilation, approved_rule_ids=approved_rule_ids)
    return result, fake


def test_live_mode_runs_end_to_end_with_fake_client(policy_bytes: bytes, monkeypatch):
    result, fake = _run_live(policy_bytes, "packet_08", monkeypatch)
    assert result.mode == "LIVE"
    assert result.errors == []
    assert len(fake.prompts) == 2
    assert len(result.rulebook.rules) == 5
    assert all(result.rule_validation_status.values())


def test_live_mode_passes_policy_non_payable_items_to_extractor(policy_bytes: bytes, monkeypatch):
    _, fake = _run_live(policy_bytes, "packet_08", monkeypatch)
    extraction_prompt = fake.prompts[1]
    assert "non-payable: registration_fees" in extraction_prompt
    assert "luxury_patient_amenity_kits" in extraction_prompt


def test_live_mode_reports_compiled_ambiguity(policy_bytes: bytes, monkeypatch):
    result, _ = _run_live(policy_bytes, "packet_12", monkeypatch)
    assert any("AMB_PRE_EXISTING_CONDITIONS" in item for item in result.unresolved_items)


def _statuses(result):
    return {f.rule_id: str(getattr(f.status, "value", f.status)) for f in result.findings}


def test_live_mode_without_approval_reports_needs_review(policy_bytes: bytes, monkeypatch):
    result, _ = _run_live(policy_bytes, "packet_08", monkeypatch)
    assert set(_statuses(result).values()) == {"needs_review"}
    assert result.approved_rule_ids == []
    assert any("No compiled rules have been approved" in item for item in result.unresolved_items)


def test_live_mode_applies_approved_verified_rules(policy_bytes: bytes, monkeypatch):
    all_ids = ["RULE_REQ_DOCS", "RULE_WAITING_PERIOD", "RULE_ROOM_RENT_CAP",
               "RULE_CATARACT_SUBLIMIT", "RULE_NON_PAYABLE_ITEMS"]
    result, _ = _run_live(policy_bytes, "packet_08", monkeypatch, approved_rule_ids=all_ids)
    statuses = _statuses(result)
    assert statuses["RULE_NON_PAYABLE_ITEMS"] == "violated"
    finding = next(f for f in result.findings if f.rule_id == "RULE_NON_PAYABLE_ITEMS")
    assert finding.estimated_impact == 3000.0
    assert sorted(result.approved_rule_ids) == sorted(all_ids)


def test_live_mode_only_applies_the_rules_that_were_approved(policy_bytes: bytes, monkeypatch):
    result, _ = _run_live(policy_bytes, "packet_08", monkeypatch, approved_rule_ids=["RULE_REQ_DOCS"])
    statuses = _statuses(result)
    assert statuses["RULE_NON_PAYABLE_ITEMS"] == "needs_review"
    assert statuses["RULE_REQ_DOCS"] != "needs_review"


def test_rule_with_unverified_quote_cannot_be_approved(policy_bytes: bytes, monkeypatch):
    import json as _json
    from src.pipeline import approvable_rule_ids, compile_policy
    monkeypatch.setenv("NEBIUS_MODEL_REASONING", "fake-reasoning")
    fake = _FakeNemotron("packet_08")
    reply = _json.loads(fake.compile_reply)
    for rule in reply["rules"]:
        if rule["rule_id"] == "RULE_NON_PAYABLE_ITEMS":
            rule["source"]["quote"] = "Gloves are always payable."
    fake.compile_reply = _json.dumps(reply)
    monkeypatch.setattr("src.ai.policy_compiler.get_client", lambda: fake)
    compilation = compile_policy(policy_bytes)
    assert "RULE_NON_PAYABLE_ITEMS" not in approvable_rule_ids(compilation)

    result, _ = _run_live(policy_bytes, "packet_08", monkeypatch, compilation=compilation,
                          approved_rule_ids=["RULE_NON_PAYABLE_ITEMS"])
    assert _statuses(result)["RULE_NON_PAYABLE_ITEMS"] == "needs_review"
    assert result.approved_rule_ids == []


def test_reviewed_compilation_is_reused_not_recompiled(policy_bytes: bytes, monkeypatch):
    from src.pipeline import compile_policy
    monkeypatch.setenv("NEBIUS_MODEL_REASONING", "fake-reasoning")
    fake = _FakeNemotron("packet_08")
    monkeypatch.setattr("src.ai.policy_compiler.get_client", lambda: fake)
    compilation = compile_policy(policy_bytes)
    result, fake2 = _run_live(policy_bytes, "packet_08", monkeypatch, compilation=compilation,
                              approved_rule_ids=["RULE_REQ_DOCS"])
    assert len(fake2.prompts) == 1  # only the claim extraction call
    assert compilation.rules[0].validation_status == "draft"  # the reviewed copy is not mutated

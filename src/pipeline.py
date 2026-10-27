"""End-to-end analysis pipeline for ClaimCheck 2.0.

Orchestrates document extraction, policy compilation, fact extraction,
deterministic rule adjudication, and evidence highlight generation.
Supports both 'live' (Nemotron reasoning/fast models) and 'mock' (gold standard)
modes, ensuring the UI and tests run reliably with zero external dependencies.

Strictly zero emojis and zero decorative Unicode symbols.
"""

from __future__ import annotations

import io
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pymupdf as fitz
from pydantic import BaseModel, Field

from src import settings
from src.engine.rule_engine import evaluate
from src.schemas import (
    Ambiguity,
    ClaimFact,
    Finding,
    FindingStatus,
    Rule,
    Rulebook,
    SourceRef,
    ValidationStatus,
)
from src.documents.pdf_extractor import extract_text_from_pdf, DocumentExtraction, PDFExtractionError
from src.documents.evidence import check_citation, normalize_whitespace
from src.documents.ocr import is_tesseract_available

ROOT_DIR = Path(__file__).resolve().parent.parent
GOLD_RULEBOOK_PATH = ROOT_DIR / "data" / "gold_rulebook.json"
PACKETS_DIR = ROOT_DIR / "data" / "packets"


class AnalysisResult(BaseModel):
    """Complete structured output from analyzing an insurance claim against a policy."""
    mode: str = "MOCK"  # "MOCK" or "LIVE"
    policy_id: str = "synthetic_policy"
    rulebook: Optional[Rulebook] = None
    rule_validation_status: Dict[str, bool] = Field(default_factory=dict)
    extracted_facts: List[ClaimFact] = Field(default_factory=list)
    findings: List[Finding] = Field(default_factory=list)
    ambiguities: List[Ambiguity] = Field(default_factory=list)
    unresolved_items: List[str] = Field(default_factory=list)
    ocr_warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    timings: Dict[str, float] = Field(default_factory=dict)
    tokens: Dict[str, int] = Field(default_factory=dict)
    investigation_result: Optional[Any] = None
    approved_rule_ids: List[str] = Field(default_factory=list)


def compile_policy(policy_pdf_bytes: bytes):
    """Live mode step 1: compile policy rules with Nemotron for a person to review.

    Returns the CompilationResult (rules, ambiguities and citation checks). Nothing
    is applied to a claim until rules are approved and passed to analyze().
    """
    from src.ai.policy_compiler import compile_policy_rules
    from src.documents.policy_document import load_document

    policy_doc = load_document(io.BytesIO(policy_pdf_bytes))
    return compile_policy_rules(policy_doc, policy_id="synthetic_policy")


def approvable_rule_ids(compilation) -> List[str]:
    """Rules a person may approve: only those whose quote was verified on the cited page."""
    return [r.rule_id for r in compilation.rules
            if compilation.citation_checks.get(r.rule_id) and compilation.citation_checks[r.rule_id].verified]


def render_page_with_highlight(
    pdf_bytes: bytes,
    page_num: int,
    quote: Optional[str] = None,
    dpi: int = 150,
) -> Tuple[bytes, bool, str]:
    """Render a PDF page to a PNG image, highlighting the quote in-memory if present.

    Args:
        pdf_bytes: Raw binary bytes of the PDF document.
        page_num: 1-based page number to render.
        quote: Sentence or text clause to locate and highlight.
        dpi: Rendering resolution for the output pixmap.

    Returns:
        Tuple of (png_bytes, is_highlighted, note_message).
    """
    if not pdf_bytes:
        return b"", False, "No PDF content provided."

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        return b"", False, f"Could not open PDF: {exc}"

    doc_len = len(doc)
    if page_num < 1 or page_num > doc_len:
        doc.close()
        return b"", False, f"Page {page_num} is out of document range (1-{doc_len})."

    page = doc[page_num - 1]
    page_text = page.get_text()

    # Check if page is image-only / scanned
    if len(page_text.strip()) < 25:
        pix = page.get_pixmap(dpi=dpi)
        png_bytes = pix.tobytes("png")
        doc.close()
        return png_bytes, False, "Scanned image page: text highlighting unavailable"

    highlighted = False
    note = "Highlight not verified"

    if quote and quote.strip():
        clean_quote = normalize_whitespace(quote)
        # 1. Exact search
        rects = page.search_for(clean_quote)

        # 2. Normalized line blocks or clauses
        if not rects:
            clauses = [c.strip() for c in re.split(r"[.;:\n]", clean_quote) if len(c.strip()) >= 15]
            for clause in clauses:
                clause_rects = page.search_for(clause)
                if clause_rects:
                    rects.extend(clause_rects)

        # 3. First 6 words
        if not rects:
            words = clean_quote.split()
            if len(words) >= 4:
                prefix = " ".join(words[:6])
                rects = page.search_for(prefix)

        if rects:
            for r in rects:
                annot = page.add_highlight_annot(r)
                if annot:
                    annot.set_colors(stroke=(1.0, 0.9, 0.2))  # Warm yellow
                    annot.update()
            highlighted = True
            note = "Quote highlighted"

    pix = page.get_pixmap(dpi=dpi)
    png_bytes = pix.tobytes("png")
    doc.close()
    return png_bytes, highlighted, note


def _detect_packet_id(claim_pdfs: Dict[str, bytes]) -> str:
    """Infer the closest matching benchmark packet ID from claim filenames or content."""
    for k in claim_pdfs:
        match = re.search(r"packet_(\d+)", k)
        if match:
            return f"packet_{int(match.group(1)):02d}"

    bill_bytes = claim_pdfs.get("hospital_bill.pdf") or next(
        (v for k, v in claim_pdfs.items() if "bill" in k.lower()), b""
    )
    if bill_bytes:
        try:
            doc = fitz.open(stream=bill_bytes, filetype="pdf")
            text = " ".join(p.get_text() for p in doc)
            doc.close()

            if "SCENARIO_11" in text or "Deluxe Room billed @ INR 10000" in text or "10000.00/day" in text:
                return "packet_11"
            if "SCENARIO_05" in text or "Twin Sharing" in text or "4000.00/day" in text:
                return "packet_05"
            if "SCENARIO_06" in text or "8000.00/day" in text:
                return "packet_06"
            if "SCENARIO_07" in text or "cataract" in text.lower():
                return "packet_07"
            if "SCENARIO_08" in text or "Luxury Patient" in text:
                return "packet_08"
        except Exception:
            pass

    form_bytes = claim_pdfs.get("claim_form.pdf") or next(
        (v for k, v in claim_pdfs.items() if "form" in k.lower()), b""
    )
    if form_bytes:
        try:
            doc = fitz.open(stream=form_bytes, filetype="pdf")
            text = " ".join(p.get_text() for p in doc)
            doc.close()
            if "Discharge summary is currently awaited" in text:
                return "packet_02"
        except Exception:
            pass

    return "packet_01"


def _check_ocr_readiness(
    name: str,
    pdf_bytes: bytes,
    ocr_available: bool,
) -> Tuple[Optional[DocumentExtraction], List[str]]:
    """Inspect a PDF for low-text density pages and record explicit OCR warnings."""
    warnings: List[str] = []
    try:
        extraction = extract_text_from_pdf(pdf_bytes, document_id=name)
        for page in extraction.pages:
            if page.needs_ocr:
                char_count = len(page.text)
                warnings.append(
                    f"Document '{name}', Page {page.page_number} contains little or no extractable digital text "
                    f"({char_count} chars). Scanned image requires OCR."
                )
        if any(p.needs_ocr for p in extraction.pages) and not ocr_available:
            warnings.append(
                f"OCR Engine is unavailable or missing language traineddata ('eng.traineddata'). "
                f"Scanned pages in '{name}' cannot be transcribed automatically."
            )
        return extraction, warnings
    except PDFExtractionError as exc:
        warnings.append(f"Could not parse '{name}': {exc}")
        return None, warnings
    except Exception as exc:
        warnings.append(f"Unexpected error inspecting '{name}': {exc}")
        return None, warnings


def analyze(
    policy_pdf_bytes: bytes,
    claim_pdfs: Dict[str, bytes],
    mode: str = "mock",
    packet_id: Optional[str] = None,
    compilation: Optional[Any] = None,
    approved_rule_ids: Optional[List[str]] = None,
) -> AnalysisResult:
    """Analyze a claim submission against an insurance policy.

    Args:
        policy_pdf_bytes: PDF bytes of the insurance policy terms.
        claim_pdfs: Mapping of document names to file bytes (e.g. bills, claim forms).
        mode: Adjudication mode - 'mock' (offline ground truth) or 'live' (Nebius models).
        packet_id: Optional explicit sample packet ID to load in mock mode.
        compilation: Live mode only. A CompilationResult from compile_policy() that a person
            has reviewed. If omitted, the policy is compiled inside this call.
        approved_rule_ids: Live mode only. Rule IDs a person approved. A compiled rule is
            applied only if it is approved AND its citation was verified; every other rule
            stays draft and the engine reports it as needs_review.

    Returns:
        AnalysisResult: Structured adjudication result with verified citations and calculation steps.
    """
    t_start = time.time()
    result = AnalysisResult(mode=mode.upper())
    ocr_available = is_tesseract_available("eng")

    # 1. Basic validation of inputs
    if not policy_pdf_bytes:
        result.errors.append("No policy PDF content provided.")
        return result

    if not claim_pdfs:
        result.errors.append("No claim supporting documents provided.")
        return result

    # 2. Inspect Policy Document
    policy_extraction, p_warnings = _check_ocr_readiness("policy.pdf", policy_pdf_bytes, ocr_available)
    result.ocr_warnings.extend(p_warnings)
    if policy_extraction is None:
        result.errors.append("Policy document could not be read as a valid PDF.")
        return result

    # 3. Inspect Claim Documents
    claim_extractions: Dict[str, DocumentExtraction] = {}
    for name, cbytes in claim_pdfs.items():
        cext, c_warnings = _check_ocr_readiness(name, cbytes, ocr_available)
        result.ocr_warnings.extend(c_warnings)
        if cext is not None:
            claim_extractions[name] = cext
        else:
            result.errors.append(f"Claim document '{name}' could not be read as a valid PDF.")

    if not claim_extractions:
        result.errors.append("No readable claim documents found.")
        return result

    # -------------------------------------------------------------------------
    # MOCK MODE
    # -------------------------------------------------------------------------
    if mode.lower() == "mock":
        t_compile_start = time.time()
        # Load gold standard rulebook
        if not GOLD_RULEBOOK_PATH.exists():
            result.errors.append(f"Gold rulebook file not found at {GOLD_RULEBOOK_PATH}")
            return result

        with open(GOLD_RULEBOOK_PATH, "r", encoding="utf-8") as f:
            gold_data = json.load(f)

        rulebook = Rulebook.model_validate(gold_data)
        result.rulebook = rulebook
        result.policy_id = rulebook.policy_id
        result.ambiguities = getattr(rulebook, "ambiguities", [])

        # Check citations against policy text
        for rule in rulebook.rules:
            check = check_citation(policy_extraction, rule.source)
            result.rule_validation_status[rule.rule_id] = check.verified
            if not check.verified:
                rule.validation_status = ValidationStatus.DRAFT

        result.timings["compiler_sec"] = time.time() - t_compile_start

        # Determine packet ID and load ground truth facts
        t_extract_start = time.time()
        pid = packet_id or _detect_packet_id(claim_pdfs)
        packet_dir = PACKETS_DIR / pid
        facts_file = packet_dir / "ground_truth_facts.json"

        facts: List[ClaimFact] = []
        if facts_file.exists():
            try:
                with open(facts_file, "r", encoding="utf-8") as f:
                    facts_raw = json.load(f)
                facts = [ClaimFact.model_validate(item) for item in facts_raw]
            except Exception as exc:
                result.errors.append(f"Failed loading mock ground truth facts for {pid}: {exc}")
        else:
            result.errors.append(f"Mock packet facts not found for {pid}.")

        result.extracted_facts = facts
        result.timings["extractor_sec"] = time.time() - t_extract_start

        # Run Deterministic Rule Engine
        t_engine_start = time.time()
        try:
            findings = evaluate(rulebook, facts)
            result.findings = findings
        except Exception as exc:
            result.errors.append(f"Rule engine execution error: {exc}")
            findings = []

        result.timings["engine_sec"] = time.time() - t_engine_start

        # Compile unresolved items
        for amb in result.ambiguities:
            result.unresolved_items.append(f"Policy Ambiguity [{amb.ambiguity_id}]: {amb.description}")

        for fnd in findings:
            s = str(getattr(fnd.status, "value", fnd.status)).lower()
            if s in ("needs_review", "unable_to_verify"):
                result.unresolved_items.append(f"Rule [{fnd.rule_id}]: {fnd.explanation}")

        result.tokens = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        result.timings["total_sec"] = time.time() - t_start
        return result

    # -------------------------------------------------------------------------
    # LIVE MODE
    # -------------------------------------------------------------------------
    else:
        if not settings.is_set("NEBIUS_API_KEY"):
            result.errors.append(
                "Live mode cannot run: NEBIUS_API_KEY is not set in environment or .env. "
                "Configure your API key in .env or switch to Mock mode."
            )
            return result

        if not settings.is_set("NEBIUS_MODEL_FAST") or not settings.is_set("NEBIUS_MODEL_REASONING"):
            result.errors.append(
                "Live mode requires both NEBIUS_MODEL_FAST and NEBIUS_MODEL_REASONING in .env."
            )
            return result

        try:
            from src.ai.claim_extractor import extract_claim_facts
            from src.ai.claim_loader import load_claim_document

            # Compile policy (or reuse the compilation a person reviewed)
            t_comp = time.time()
            comp_res = compilation if compilation is not None else compile_policy(policy_pdf_bytes)
            rulebook = comp_res.to_rulebook().model_copy(deep=True)

            result.rulebook = rulebook
            result.policy_id = rulebook.policy_id
            result.ambiguities = list(rulebook.ambiguities)
            approved = set(approved_rule_ids or [])
            for r in rulebook.rules:
                chk = comp_res.citation_checks.get(r.rule_id)
                verified = chk.verified if chk else False
                result.rule_validation_status[r.rule_id] = verified
                if verified and r.rule_id in approved:
                    # Human approval: a person reviewed this verified rule and approved it.
                    # Plain string values: the engine compares the status as text.
                    r.validation_status = ValidationStatus.VALIDATED.value
                    r.human_review_required = False
                    result.approved_rule_ids.append(r.rule_id)
                else:
                    r.validation_status = ValidationStatus.DRAFT.value
            if not result.approved_rule_ids:
                result.unresolved_items.append(
                    "No compiled rules have been approved yet. Review the compiled rules and approve "
                    "them before findings can be decided; until then every rule is reported as needs_review."
                )

            result.timings["compiler_sec"] = time.time() - t_comp

            # Extract facts from claims
            t_ext = time.time()
            claim_doc_list = []
            for name, cbytes in claim_pdfs.items():
                cdoc = load_claim_document(cbytes, document_id=name)
                claim_doc_list.append(cdoc)

            # Tell the extractor which bill lines this policy excludes, taken from the
            # compiled rulebook, so non-payable charges are matched in live mode.
            non_payable_items = next(
                (r.parameters.get("excluded_items") for r in rulebook.rules
                 if str(getattr(r.rule_type, "value", r.rule_type)) == "non_payable_items"),
                None,
            )
            extract_res = extract_claim_facts(claim_doc_list, non_payable_items=non_payable_items or None)
            result.extracted_facts = extract_res.facts
            result.timings["extractor_sec"] = time.time() - t_ext

            # Evaluate rules
            t_eng = time.time()
            findings = evaluate(rulebook, extract_res.facts)
            result.findings = findings
            result.timings["engine_sec"] = time.time() - t_eng

            for amb in getattr(rulebook, "ambiguities", []):
                result.unresolved_items.append(f"Ambiguity [{amb.ambiguity_id}]: {amb.description}")
            for fnd in findings:
                s = str(getattr(fnd.status, "value", fnd.status)).lower()
                if s in ("needs_review", "unable_to_verify"):
                    result.unresolved_items.append(f"Rule [{fnd.rule_id}]: {fnd.explanation}")

            result.timings["total_sec"] = time.time() - t_start
            return result

        except Exception as exc:
            result.errors.append(f"Live pipeline execution failed: {type(exc).__name__}: {exc}")
            result.timings["total_sec"] = time.time() - t_start
            return result

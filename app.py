"""ClaimCheck 2.0 - Policy Compiler and Claim Intelligence Interface.

Evidence-grounded Health Insurance Reimbursement Adjudication.
Provides a clean, professional, accessible 5-tab interface:
- Tab 1: Overview (Adjudication counts, documents, pipeline mode, performance, models in use)
- Tab 2: Policy rulebook (Compiled rules with verbatim citations, validation badges, ambiguities list)
- Tab 3: Findings (Findings cards with proportional room-rent calculations and in-memory source page viewer)
- Tab 4: Needs your input (Agent-driven clarification questions, Tavily statutory search, iteration cap)
- Tab 5: Report (Full markdown export with citations, calculation steps, and disclaimers)

Strictly zero emojis and zero decorative Unicode symbols throughout.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import streamlit as st

from src import settings
from src.agent import investigator, tavily_client
from src.ai.config_check import inspect_configuration
from src.engine.rule_engine import evaluate
from src.pipeline import AnalysisResult, analyze, approvable_rule_ids, compile_policy, render_page_with_highlight
from src.reports.report_generator import generate_markdown_report
from src.schemas import ClaimFact, SourceRef

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
PACKETS_DIR = DATA_DIR / "packets"
DEFAULT_POLICY_PATH = DATA_DIR / "synthetic_policy.pdf"

PACKET_CATALOG = {
    "packet_01": "Packet 01: Inadmissible - Missing Claim Form",
    "packet_02": "Packet 02: Inadmissible - Missing Discharge Summary",
    "packet_03": "Packet 03: Waiting Period Satisfied (Day 45)",
    "packet_04": "Packet 04: Waiting Period Violated (Day 15 Illness)",
    "packet_05": "Packet 05: Room Rent Within Cap (Twin Sharing @ 4000)",
    "packet_06": "Packet 06: Room Rent Exceeded (Rasterised Bill @ 8000)",
    "packet_07": "Packet 07: Cataract Sub-limit Exceeded (@ 50000 vs 40000)",
    "packet_08": "Packet 08: Non-Payable PPE Items Excluded",
    "packet_09": "Packet 09: Accidental Injury (Waiting Period Exempt)",
    "packet_10": "Packet 10: Unknown Room Rent Duration (Rasterised Bill)",
    "packet_11": "Packet 11: Room Rent Exceeded with Proportional Deduction",
    "packet_12": "Packet 12: Pre-Existing Condition Ambiguity",
    "packet_13": "Packet 13: Clean Multi-Day Claim Satisfied",
    "packet_14": "Packet 14: Emergency Accidental ICU Admission",
    "packet_15": "Packet 15: Dual Eye Cataract Surgery (Rasterised Bill)",
    "packet_16": "Packet 16: Diagnostic Exclusion - No Active Line Items",
    "packet_17": "Packet 17: Partial Room Rent Violation (Rasterised Bill)",
    "packet_18": "Packet 18: Incomplete Claim Documentation",
    "packet_19": "Packet 19: Late Submission Notice",
    "packet_20": "Packet 20: Cross-Document Billing Discrepancy (Rasterised)",
}

st.set_page_config(
    page_title="ClaimCheck 2.0",
    layout="wide",
    initial_sidebar_state="expanded",
)


def load_bundled_packet(packet_id: str) -> Tuple[bytes, Dict[str, bytes]]:
    """Load policy and claim PDFs for a bundled benchmark packet."""
    policy_bytes = b""
    if DEFAULT_POLICY_PATH.exists():
        policy_bytes = DEFAULT_POLICY_PATH.read_bytes()

    claim_files: Dict[str, bytes] = {}
    pdir = PACKETS_DIR / packet_id
    if pdir.exists():
        for f in sorted(pdir.glob("*.pdf")):
            claim_files[f.name] = f.read_bytes()

    return policy_bytes, claim_files


def get_status_badge_html(status_str: str) -> str:
    """Render a high-contrast plain-text status badge with color and text."""
    s = status_str.upper()
    if s == "SATISFIED":
        bg, fg, border = "#e6f4ea", "#137333", "#ceead6"
        label = "SATISFIED"
    elif s == "VIOLATED":
        bg, fg, border = "#fce8e6", "#c5221f", "#fad2cf"
        label = "VIOLATED"
    elif s == "NEEDS_REVIEW" or s == "NEEDS REVIEW":
        bg, fg, border = "#fef7e0", "#b06000", "#feefc3"
        label = "NEEDS REVIEW"
    elif s == "UNABLE_TO_VERIFY" or s == "UNABLE TO VERIFY":
        bg, fg, border = "#f1f3f4", "#5f6368", "#dadce0"
        label = "UNABLE TO VERIFY"
    else:
        bg, fg, border = "#e8f0fe", "#1a73e8", "#d2e3fc"
        label = s

    return (
        f'<span style="background-color: {bg}; color: {fg}; border: 1px solid {border}; '
        f'padding: 3px 8px; border-radius: 4px; font-weight: 700; font-size: 0.85rem; '
        f'letter-spacing: 0.5px; display: inline-block;">{label}</span>'
    )


def get_validation_badge_html(verified: bool) -> str:
    """Render a plain-text rule citation validation badge."""
    if verified:
        return (
            '<span style="background-color: #e6f4ea; color: #137333; border: 1px solid #ceead6; '
            'padding: 2px 6px; border-radius: 4px; font-size: 0.8rem; font-weight: 600;">VERIFIED CITATION</span>'
        )
    return (
        '<span style="background-color: #f1f3f4; color: #5f6368; border: 1px solid #dadce0; '
        'padding: 2px 6px; border-radius: 4px; font-size: 0.8rem; font-weight: 600;">UNVERIFIED CITATION</span>'
    )


def render_models_in_use_box(cfg, is_mock: bool) -> None:
    """Display configured models box adhering strictly to guidelines."""
    st.markdown("##### Models in Use")
    if is_mock:
        st.info("No model calls made (mock data)")
        return

    box_lines = []
    box_lines.append(f"- Endpoint Host: `{cfg.endpoint_host or 'tokenfactory.nebius.com'}`")
    reasoning_id = cfg.model_reasoning or "Not configured"
    fast_id = cfg.model_fast or "Not configured"
    box_lines.append(f"- Policy Reasoning Model: `{reasoning_id}`")
    box_lines.append(f"- Extraction and Fast Agent Model: `{fast_id}`")

    if cfg.is_nemotron_reasoning and cfg.is_nemotron_fast:
        box_lines.append("- Provider: **NVIDIA Nemotron via Nebius Token Factory**")
    else:
        box_lines.append("- Provider: Nebius Token Factory (Nemotron model check pending)")

    st.markdown("\n".join(box_lines))


def render_rule_approval() -> None:
    """Live mode: let a person review Nemotron-compiled rules and approve them before use."""
    compilation = st.session_state.get("compilation")
    if compilation is None:
        return

    approvable = set(approvable_rule_ids(compilation))
    approved = st.session_state.get("approved_rule_ids")

    st.subheader("Review compiled policy rules")
    st.caption(
        f"NVIDIA Nemotron ({compilation.model}) compiled {len(compilation.rules)} rule(s) from the policy. "
        "Each quote was checked word for word against the cited PDF page. Only rules with a verified quote "
        "can be approved, and only approved rules are applied to claims."
    )
    if approved is None:
        st.warning("Rules are not approved yet. Until you approve them, every finding is reported as Needs Review.")
    else:
        st.success(f"Approved {len(approved)} of {len(compilation.rules)} rule(s). Run the analysis to apply them.")

    with st.form("rule_approval_form"):
        selections: Dict[str, bool] = {}
        for rule in compilation.rules:
            check = compilation.citation_checks.get(rule.rule_id)
            verified = rule.rule_id in approvable
            rule_type = getattr(rule.rule_type, "value", rule.rule_type)
            badge = (f"Quote verified on page {rule.source.page}" if verified
                     else f"Quote not found on cited page {rule.source.page} ({check.reason if check else 'unchecked'})")
            with st.expander(f"{rule.rule_id} ({rule_type}) - {badge}", expanded=not verified):
                st.write(rule.description)
                st.markdown("**Parameters**")
                st.json(rule.parameters, expanded=True)
                if rule.exceptions:
                    st.markdown("**Exceptions:** " + "; ".join(rule.exceptions))
                st.markdown(f"**Policy quote (page {rule.source.page}):**")
                st.markdown(f"> {rule.source.quote}")
                default = verified if approved is None else rule.rule_id in approved
                selections[rule.rule_id] = st.checkbox(
                    "Approve this rule" if verified else "Cannot approve: quote not verified",
                    value=default and verified,
                    disabled=not verified,
                    key=f"approve_{rule.rule_id}",
                )

        ambiguities = getattr(compilation, "ambiguities", [])
        if ambiguities:
            st.markdown("**Ambiguous clauses (always sent to human review, never applied automatically)**")
            for amb in ambiguities:
                st.info(f"{amb.ambiguity_id} ({amb.section}, page {amb.source.page}): {amb.issue}. {amb.description}")

        if st.form_submit_button("Approve selected rules", type="primary"):
            st.session_state.approved_rule_ids = [rid for rid, ok in selections.items() if ok and rid in approvable]
            st.rerun()

    st.markdown("---")


def main() -> None:
    st.title("ClaimCheck 2.0")
    st.caption("Policy compiler and claim intelligence agent for health insurance reimbursement")

    cfg = inspect_configuration()

    # Session State Initialization
    if "result" not in st.session_state:
        st.session_state.result = None
    if "policy_bytes" not in st.session_state:
        st.session_state.policy_bytes = b""
    if "claim_pdfs" not in st.session_state:
        st.session_state.claim_pdfs = {}
    if "selected_packet" not in st.session_state:
        st.session_state.selected_packet = "packet_11"
    if "agent_iteration" not in st.session_state:
        st.session_state.agent_iteration = 1
    if "clarifications" not in st.session_state:
        st.session_state.clarifications = {}
    if "tavily_results" not in st.session_state:
        st.session_state.tavily_results = {}
    if "active_finding_idx" not in st.session_state:
        st.session_state.active_finding_idx = 0

    # -------------------------------------------------------------------------
    # SIDEBAR
    # -------------------------------------------------------------------------
    with st.sidebar:
        st.subheader("Configuration")

        # Mode Selection
        mode_options = ["Mock Mode (Offline Ground Truth)"]
        if cfg.is_live_ready:
            mode_options.append("Live Mode (NVIDIA Nemotron Models)")
        else:
            mode_options.append("Live Mode (Disabled - Missing Credentials)")

        mode_choice = st.radio(
            "Adjudication Mode",
            mode_options,
            index=0,
            help="Mock mode executes entirely offline with gold rulebooks and verified ground truth facts.",
        )
        is_live_selected = "Live Mode (NVIDIA Nemotron" in mode_choice
        pipeline_mode = "live" if is_live_selected else "mock"

        if "Disabled" in mode_choice:
            missing_names = ", ".join(cfg.missing_variables)
            st.warning(
                f"Live mode requires configuration of: {missing_names}. "
                "Add these variables to your .env file or Streamlit secrets to enable Live mode."
            )

        if pipeline_mode == "mock":
            st.info("MOCK DATA: Running with verified offline ground truth and gold rulebook.")

        st.markdown("---")
        st.subheader("Input Documents")

        input_type = st.radio(
            "Document Source",
            ["Bundled Benchmark Packet", "Custom File Upload"],
            index=0,
        )

        policy_bytes = b""
        claim_files: Dict[str, bytes] = {}
        chosen_pid: Optional[str] = None

        if input_type == "Bundled Benchmark Packet":
            packet_keys = list(PACKET_CATALOG.keys())
            chosen_pid = st.selectbox(
                "Select Test Packet",
                packet_keys,
                index=packet_keys.index(st.session_state.selected_packet) if st.session_state.selected_packet in packet_keys else 10,
                format_func=lambda x: PACKET_CATALOG.get(x, x),
            )
            st.session_state.selected_packet = chosen_pid
            policy_bytes, claim_files = load_bundled_packet(chosen_pid)
            st.caption(f"Loaded policy + {len(claim_files)} claim document(s) for {chosen_pid}")

        else:
            uploaded_policy = st.file_uploader("Upload Insurance Policy PDF", type=["pdf"])
            uploaded_claims = st.file_uploader(
                "Upload Supporting Claim Documents (Bills, Forms, Records)",
                type=["pdf"],
                accept_multiple_files=True,
            )
            if uploaded_policy:
                policy_bytes = uploaded_policy.read()
            if uploaded_claims:
                for uf in uploaded_claims:
                    claim_files[uf.name] = uf.read()

        st.markdown("---")
        render_models_in_use_box(cfg, is_mock=(pipeline_mode == "mock"))

        if pipeline_mode == "live" and policy_bytes:
            policy_key = hash(policy_bytes)
            if st.session_state.get("compiled_policy_key") != policy_key:
                st.session_state.compilation = None
                st.session_state.approved_rule_ids = None
            st.markdown("---")
            st.caption("Live mode: 1) compile the policy rules, 2) review and approve them, 3) run the analysis.")
            if st.button("1. Compile policy rules (Nemotron)", use_container_width=True):
                with st.spinner("Compiling policy rules with NVIDIA Nemotron..."):
                    try:
                        st.session_state.compilation = compile_policy(policy_bytes)
                        st.session_state.compiled_policy_key = policy_key
                        st.session_state.approved_rule_ids = None
                    except Exception as exc:
                        st.error(f"Policy compilation failed: {type(exc).__name__}: {exc}")

        st.markdown("---")
        run_analysis = st.button("Run Adjudication Analysis", type="primary", use_container_width=True)

    # Trigger Analysis
    if run_analysis:
        if not policy_bytes:
            st.error("No policy document provided. Please select a packet or upload a policy PDF.")
        elif not claim_files:
            st.error("No claim supporting documents provided. Please upload or select a packet with claim PDFs.")
        else:
            with st.spinner("Analyzing claim documents against policy rules..."):
                st.session_state.policy_bytes = policy_bytes
                st.session_state.claim_pdfs = claim_files
                st.session_state.agent_iteration = 1
                st.session_state.clarifications.clear()
                st.session_state.tavily_results.clear()
                st.session_state.active_finding_idx = 0

                res = analyze(
                    policy_pdf_bytes=policy_bytes,
                    claim_pdfs=claim_files,
                    mode=pipeline_mode,
                    packet_id=chosen_pid,
                    compilation=st.session_state.get("compilation") if pipeline_mode == "live" else None,
                    approved_rule_ids=st.session_state.get("approved_rule_ids") if pipeline_mode == "live" else None,
                )
                st.session_state.result = res

    if pipeline_mode == "live":
        render_rule_approval()

    # Check if analysis results exist
    result: Optional[AnalysisResult] = st.session_state.result

    if result is None:
        st.info(
            "Select an adjudication mode and input packet from the sidebar, "
            "then click 'Run Adjudication Analysis' to begin."
        )
        return

    # Display Errors and OCR Warnings if any
    if result.errors:
        for err in result.errors:
            st.error(f"Pipeline Notice: {err}")

    if result.ocr_warnings:
        with st.expander("OCR and Document Processing Notices", expanded=False):
            for warn in result.ocr_warnings:
                st.warning(warn)

    # -------------------------------------------------------------------------
    # MAIN INTERFACE TABS
    # -------------------------------------------------------------------------
    tab_overview, tab_rulebook, tab_findings, tab_input, tab_report = st.tabs([
        "Overview",
        "Policy rulebook",
        "Findings",
        "Needs your input",
        "Report",
    ])

    # =========================================================================
    # TAB 1: OVERVIEW
    # =========================================================================
    with tab_overview:
        st.subheader("Adjudication Overview")

        # Counts per status computed from real results
        satisfied_n = sum(1 for f in result.findings if str(getattr(f.status, "value", f.status)).lower() == "satisfied")
        violated_n = sum(1 for f in result.findings if str(getattr(f.status, "value", f.status)).lower() == "violated")
        review_n = sum(1 for f in result.findings if str(getattr(f.status, "value", f.status)).lower() == "needs_review")
        unable_n = sum(1 for f in result.findings if str(getattr(f.status, "value", f.status)).lower() == "unable_to_verify")

        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.metric("Satisfied Rules", satisfied_n)
        with m2:
            st.metric("Violated Rules", violated_n)
        with m3:
            st.metric("Needs Review", review_n)
        with m4:
            st.metric("Unable to Verify", unable_n)

        st.markdown("---")
        col_doc, col_perf = st.columns(2)

        with col_doc:
            st.markdown("##### Documents Analysed")
            st.write(f"- Policy: `synthetic_policy.pdf` (Policy ID: `{result.policy_id}`)")
            st.write(f"- Claim Documents ({len(st.session_state.claim_pdfs)}):")
            for cname in st.session_state.claim_pdfs:
                st.write(f"  - `{cname}`")
            st.write(f"- Mode: `{result.mode}`")

        with col_perf:
            st.markdown("##### Execution & Performance")
            timings = result.timings
            total_sec = timings.get("total_sec", 0.0)
            comp_sec = timings.get("compiler_sec", 0.0)
            ext_sec = timings.get("extractor_sec", 0.0)
            eng_sec = timings.get("engine_sec", 0.0)

            st.write(f"- Total Pipeline Latency: `{total_sec:.3f}s`")
            st.write(f"- Compiler: `{comp_sec:.3f}s` | Extractor: `{ext_sec:.3f}s` | Engine: `{eng_sec:.3f}s`")
            st.write(f"- Extracted Claim Facts: `{len(result.extracted_facts)}` facts")
            st.write(f"- Identified Ambiguities: `{len(result.ambiguities)}` clauses")

        st.markdown("---")
        render_models_in_use_box(cfg, is_mock=(result.mode == "MOCK"))

    # =========================================================================
    # TAB 2: POLICY RULEBOOK
    # =========================================================================
    with tab_rulebook:
        st.subheader("Governing Policy Rulebook")
        st.caption("Compiled policy clauses with verbatim source citations and verification states.")

        if result.rulebook and result.rulebook.rules:
            for rule in result.rulebook.rules:
                is_ver = result.rule_validation_status.get(rule.rule_id, True)
                badge_html = get_validation_badge_html(is_ver)

                with st.container():
                    st.markdown(
                        f"**Rule: `{rule.rule_id}`** | Type: `{rule.rule_type}` | {badge_html}",
                        unsafe_allow_html=True,
                    )
                    st.write(f"Description: {rule.description}")

                    # Parameters
                    if rule.parameters:
                        st.markdown(f"- Parameters: `{json.dumps(rule.parameters)}`")

                    # Citation
                    st.markdown(
                        f"- Policy Citation (Page {rule.source.page}): *\"{rule.source.quote}\"* "
                        f"in `{rule.source.document}`"
                    )
                    st.markdown("---")
        else:
            st.info("No policy rules loaded in rulebook.")

        # Ambiguities listed separately (never as executable rules)
        st.subheader("Identified Policy Ambiguities")
        st.caption("Clauses containing undefined terms or regulatory references requiring external interpretation.")
        if result.ambiguities:
            for amb in result.ambiguities:
                st.markdown(
                    f"- **[{amb.ambiguity_id}] Section {amb.section}:** {amb.description}\n"
                    f"  - Issue: *{amb.issue}*\n"
                    f"  - Source (Page {amb.source.page}): *\"{amb.source.quote}\"*"
                )
        else:
            st.write("No policy ambiguities flagged.")

    # =========================================================================
    # TAB 3: FINDINGS
    # =========================================================================
    with tab_findings:
        st.subheader("Claim Adjudication Findings")

        if not result.findings:
            st.info("No findings evaluated.")
        else:
            col_findings_list, col_source_view = st.columns([1, 1])

            with col_findings_list:
                st.markdown("##### Evaluated Rules")
                finding_labels = [
                    f"{idx + 1}. {f.rule_id} [{str(getattr(f.status, 'value', f.status)).upper()}]"
                    for idx, f in enumerate(result.findings)
                ]

                selected_idx = st.selectbox(
                    "Select finding to inspect evidence:",
                    range(len(finding_labels)),
                    format_func=lambda i: finding_labels[i],
                    index=min(st.session_state.active_finding_idx, len(finding_labels) - 1),
                )
                st.session_state.active_finding_idx = selected_idx

                fnd = result.findings[selected_idx]
                raw_stat = str(getattr(fnd.status, "value", fnd.status))

                st.markdown(
                    f"#### Finding: `{fnd.rule_id}`  {get_status_badge_html(raw_stat)}",
                    unsafe_allow_html=True,
                )
                st.write(f"**Explanation:** {fnd.explanation}")

                # Estimated impact
                if fnd.estimated_impact is not None:
                    st.markdown(f"**Estimated Financial Impact:** `INR {fnd.estimated_impact:,.2f}`")
                else:
                    st.markdown("**Estimated Financial Impact:** `None`")
                st.caption("Note: Estimate only; not an insurer's decision")

                # Step-by-step arithmetic calculations
                if fnd.calculation_steps:
                    st.markdown("##### Step-by-Step Calculation")
                    for s_idx, step in enumerate(fnd.calculation_steps, start=1):
                        st.write(f"{s_idx}. {step}")

                # Inputs used
                if fnd.inputs_used:
                    with st.expander("Inputs Used in Evaluation", expanded=False):
                        if isinstance(fnd.inputs_used, dict):
                            for ik, iv in fnd.inputs_used.items():
                                st.write(f"- `{ik}`: `{iv}`")
                        elif isinstance(fnd.inputs_used, list):
                            for item in fnd.inputs_used:
                                st.write(f"- `{item}`")

            with col_source_view:
                st.markdown("##### Source Document Evidence Viewer")
                if fnd.source:
                    doc_name = fnd.source.document
                    page_num = fnd.source.page
                    quote_txt = fnd.source.quote

                    st.caption(f"Document: `{doc_name}` | Cited Page: `{page_num}`")

                    # Identify bytes
                    target_bytes = b""
                    if "policy" in doc_name.lower() or doc_name == "synthetic_policy.pdf":
                        target_bytes = st.session_state.policy_bytes
                    else:
                        target_bytes = st.session_state.claim_pdfs.get(doc_name, b"")
                        if not target_bytes:
                            # Try finding matching key
                            for ck, cv in st.session_state.claim_pdfs.items():
                                if ck.lower() in doc_name.lower() or doc_name.lower() in ck.lower():
                                    target_bytes = cv
                                    break

                    if target_bytes:
                        png_bytes, is_high, note_msg = render_page_with_highlight(
                            pdf_bytes=target_bytes,
                            page_num=page_num,
                            quote=quote_txt,
                            dpi=130,
                        )
                        if png_bytes:
                            st.image(png_bytes, caption=f"{doc_name} (Page {page_num}) - {note_msg}", use_container_width=True)
                        else:
                            st.warning(f"Could not render page: {note_msg}")
                    else:
                        st.info(f"Source document '{doc_name}' is not directly loaded in viewer.")

                    st.markdown(f"> *\"{quote_txt}\"*")

    # =========================================================================
    # TAB 4: NEEDS YOUR INPUT
    # =========================================================================
    with tab_input:
        st.subheader("Needs Your Input & Ambiguity Investigation")
        st.caption(
            "Unresolved items and policy ambiguities flagged during evaluation. "
            "The investigation agent chooses actions to resolve questions or acquire guidance."
        )

        iter_count = st.session_state.agent_iteration
        st.write(f"**Agent Iteration:** `{iter_count} of 2` (Hard iteration cap: maximum 2 iterations)")

        # Prepare list of items needing input
        unresolved_findings = [
            f for f in result.findings
            if str(getattr(f.status, "value", f.status)).lower() in ("needs_review", "unable_to_verify")
        ]
        ambiguities = result.ambiguities

        if not unresolved_findings and not ambiguities:
            st.success("No unresolved items or ambiguities require input. All rules evaluated deterministically.")
        else:
            # Process findings needing input
            if unresolved_findings:
                st.markdown("##### Unresolved Rule Findings")
                for uf in unresolved_findings:
                    st.markdown(f"**Rule `{uf.rule_id}`** - Status: `{str(getattr(uf.status, 'value', uf.status)).upper()}`")
                    st.write(f"Issue: {uf.explanation}")

                    # Ask User Form
                    missing_key = "submitted_documents"
                    if "discharge_summary" in uf.explanation.lower():
                        missing_key = "submitted_documents"
                    elif "room_rent" in uf.explanation.lower():
                        missing_key = "room_rent_billing"

                    user_answer = st.text_input(
                        f"Clarification for `{uf.rule_id}` ({missing_key}):",
                        key=f"input_{uf.rule_id}",
                    )

                    if st.button(f"Submit Clarification for {uf.rule_id}", key=f"btn_{uf.rule_id}"):
                        if user_answer.strip():
                            st.session_state.clarifications[missing_key] = user_answer.strip()
                            new_fact = ClaimFact(
                                name=missing_key,
                                value=user_answer.strip().split(",") if "," in user_answer else user_answer.strip(),
                                source=SourceRef(
                                    document="user_clarification",
                                    page=1,
                                    quote=f"User clarification: {user_answer.strip()}",
                                ),
                            )
                            # Re-run rule engine with added fact
                            updated_facts = list(result.extracted_facts) + [new_fact]
                            result.extracted_facts = updated_facts
                            if result.rulebook:
                                new_findings = evaluate(result.rulebook, updated_facts)
                                result.findings = new_findings
                                st.session_state.result = result
                                st.session_state.agent_iteration = min(iter_count + 1, 2)
                                st.success("Clarification recorded and rules re-evaluated.")
                                st.rerun()
                    st.markdown("---")

            # Process policy ambiguities with Tavily
            if ambiguities:
                st.markdown("##### Policy Ambiguities & Regulatory Search")
                tavily_key_set = settings.is_set("TAVILY_API_KEY")

                for amb in ambiguities:
                    st.markdown(f"**Ambiguity `{amb.ambiguity_id}` (Section {amb.section})**")
                    st.write(f"{amb.description}")
                    st.caption(f"Core Issue: {amb.issue}")

                    query_str = f"IRDAI health insurance regulations {amb.issue}"
                    col_q, col_btn = st.columns([3, 1])
                    with col_q:
                        query_input = st.text_input(
                            f"Search query for {amb.ambiguity_id}:",
                            value=query_str,
                            key=f"q_{amb.ambiguity_id}",
                        )
                    with col_btn:
                        search_clicked = st.button(
                            "Search Tavily",
                            key=f"tavily_btn_{amb.ambiguity_id}",
                            disabled=(not tavily_key_set),
                            help="Search external regulatory guidelines using Tavily REST API",
                        )

                    if not tavily_key_set:
                        st.caption("TAVILY_API_KEY is not set; external guidance unavailable.")

                    if search_clicked and tavily_key_set:
                        with st.spinner("Querying Tavily for statutory context..."):
                            t_res = tavily_client.search(query=query_input)
                            st.session_state.tavily_results[amb.ambiguity_id] = t_res

                    # Show external guidance if available
                    stored_res = st.session_state.tavily_results.get(amb.ambiguity_id)
                    if stored_res and stored_res.success:
                        st.markdown("###### External guidance (not part of the policy)")
                        for rit in stored_res.results:
                            st.markdown(f"- **[{rit.title}]({rit.url})**: {rit.snippet}")
                    elif stored_res and not stored_res.success:
                        st.warning(f"Tavily search notice: {stored_res.error}")

                    st.markdown("---")

        # Tavily in-memory audit log
        audit_log = tavily_client.get_audit_log()
        if audit_log:
            with st.expander("Tavily Search Audit Log", expanded=False):
                for entry in audit_log:
                    st.write(
                        f"- Time: `{entry.get('timestamp')}` | Query: `{entry.get('query')}` | "
                        f"Success: `{entry.get('success')}`"
                    )

    # =========================================================================
    # TAB 5: REPORT
    # =========================================================================
    with tab_report:
        st.subheader("Adjudication Audit Report")
        st.caption("Verifiable Markdown report export based strictly on verified findings.")

        # Prepare external guidance list for report
        ext_guidance_list = []
        for aid, sres in st.session_state.tavily_results.items():
            if sres.success:
                for item in sres.results:
                    ext_guidance_list.append({
                        "query": sres.query,
                        "url": item.url,
                        "title": item.title,
                        "snippet": item.snippet,
                    })

        claim_doc_names = list(st.session_state.claim_pdfs.keys())
        md_report = generate_markdown_report(
            policy_name="synthetic_policy.pdf",
            claim_names=claim_doc_names,
            findings=result.findings,
            unresolved_items=result.unresolved_items,
            mode=result.mode,
            claim_id=st.session_state.selected_packet,
            policy_id=result.policy_id,
            external_guidance=ext_guidance_list,
        )

        st.download_button(
            label="Download Markdown Report",
            data=md_report,
            file_name=f"claim_report_{st.session_state.selected_packet}.md",
            mime="text/markdown",
        )

        st.markdown("---")
        st.markdown(md_report)


if __name__ == "__main__":
    main()

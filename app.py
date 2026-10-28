"""ClaimCheck - health insurance claim checker (Streamlit app).

Six steps: 1 claim setup, 2 policy rules, 3 results and verdict, 4 coverage timeline and
rules, 5 documents and medical history, 6 report. Every number shown comes from the
real check: NVIDIA Nemotron on Nebius Token Factory reads the policy and the documents
(Live AI), ordinary code verifies every quote and does all arithmetic, and a person
approves the AI-read rules once per policy.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from html import escape as esc
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import streamlit as st

from src import settings
from src.agent import tavily_client
from src.ai.config_check import inspect_configuration
from src.documents.evidence import check_citation
from src.documents.pdf_extractor import extract_text_from_pdf
from src.engine.coverage_rules import _date
from src.engine.document_checks import CHECK_LABELS
from src.engine.rule_engine import evaluate
from src.pipeline import AnalysisResult, analyze, approvable_rule_ids, compile_policy, render_page_with_highlight
from src.reports.report_generator import generate_markdown_report
from src.schemas import ClaimFact, Finding, Rulebook, SourceRef

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
PACKETS_DIR = DATA_DIR / "packets"
DEMO_DIR = DATA_DIR / "demo_cases"
DEFAULT_POLICY_PATH = DATA_DIR / "synthetic_policy.pdf"
GOLD_RULEBOOK_PATH = DATA_DIR / "gold_rulebook.json"

PAGES = ["1. Claim setup", "2. Policy rules", "3. Results & verdict", "4. Coverage timeline & rules",
         "5. Documents & medical history", "6. Report"]

PACKET_CATALOG = {
    "packet_01": "All three required documents",
    "packet_02": "Missing discharge summary",
    "packet_03": "Illness on day 45 (waiting period passed)",
    "packet_04": "Illness on day 14 (inside the waiting period)",
    "packet_05": "Room rent within the limit",
    "packet_06": "Private suite above the room limit (scanned bill)",
    "packet_07": "Cataract surgery above the limit",
    "packet_08": "Admission charges and amenity kit on the bill",
    "packet_09": "Road accident on day 10",
    "packet_10": "Room rent above the limit, days not stated (scanned)",
    "packet_11": "Deluxe room with proportional deduction (scanned)",
    "packet_12": "Hypertension with a pre-existing history",
    "packet_13": "Missing claim form",
    "packet_14": "Missing hospital bill",
    "packet_15": "Gloves and masks written oddly on the bill (scanned)",
    "packet_16": "Look-alike item names that are not excluded",
    "packet_17": "Room rent exactly at the limit (scanned)",
    "packet_18": "Illness on day 30 (still inside the waiting period)",
    "packet_19": "Illness on day 31 (waiting period passed)",
    "packet_20": "Claimed amount larger than the bill (scanned)",
}

RULE_ICONS = {
    "required_documents": "inventory", "waiting_period": "hourglass_top", "room_rent_cap": "bed",
    "sub_limit": "visibility", "non_payable_items": "do_not_disturb_on", "pre_hospitalization": "event_upcoming",
    "post_hospitalization": "event_available", "ambulance_limit": "ambulance", "sum_insured": "account_balance_wallet",
    "cashless_preauthorization": "approval", "pre_existing_disclosure": "clinical_notes",
}

st.set_page_config(page_title="ClaimCheck", layout="wide", initial_sidebar_state="collapsed")

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Hanken+Grotesk:wght@400;500;600;700&family=Manrope:wght@500;600;700;800&display=swap');
@import url('https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,300..600,0..1,0&display=swap');
:root {
  --primary: #00685f; --primary-2: #008378; --primary-soft: #89f5e7; --primary-tint: #e6f6f3;
  --surface: #f8f9ff; --low: #eff4ff; --mid: #e5eeff; --high: #dce9ff; --card: #ffffff;
  --ink: #0b1c30; --ink-2: #3d4947; --muted: #565e74; --line: #d3e4fe; --line-2: #bcc9c6;
  --error: #ba1a1a; --error-soft: #ffdad6; --warn: #8d4b00; --warn-soft: #ffdcc3;
}
html, body, .stApp, .stMarkdown, p, li, label, input, button, textarea, select { font-family: 'Hanken Grotesk', sans-serif !important; }
.stApp { background: var(--surface); color: var(--ink); }
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"], [data-testid="collapsedControl"] { display: none !important; }
header[data-testid="stHeader"] { background: transparent; }
.block-container { max-width: 1240px; padding-top: 1.2rem; padding-bottom: 4rem; }
h1, h2, h3, h4, .cc-h { font-family: 'Manrope', sans-serif !important; color: var(--ink); letter-spacing: -0.01em; }
.ms { font-family: 'Material Symbols Outlined' !important; font-weight: normal; font-style: normal; font-size: 20px; line-height: 1;
      display: inline-block; vertical-align: -4px; letter-spacing: normal; text-transform: none; white-space: nowrap;
      font-feature-settings: 'liga'; -webkit-font-feature-settings: 'liga'; }
.ms.fill { font-variation-settings: 'FILL' 1; }

/* top bar */
.cc-top { display: flex; align-items: center; justify-content: space-between; gap: 1rem; background: var(--card);
          border: 1px solid var(--line); border-radius: 14px; padding: .8rem 1.2rem; margin-bottom: .6rem; flex-wrap: wrap; }
.cc-brand { display: flex; align-items: center; gap: .7rem; }
.cc-logo { width: 38px; height: 38px; border-radius: 10px; background: var(--primary); color: #fff; display: grid; place-items: center; flex: none; }
.cc-brand b { font-family: 'Manrope'; font-size: 1.15rem; display: block; line-height: 1.1; }
.cc-brand small { color: var(--muted); font-size: .8rem; }
.cc-chips { display: flex; flex-wrap: wrap; gap: .45rem; justify-content: flex-end; }
.cc-chip { display: inline-flex; align-items: center; gap: .35rem; padding: .3rem .7rem; border-radius: 999px; font-size: .8rem;
           font-weight: 600; background: var(--low); color: var(--ink-2); }
.cc-chip.teal { background: var(--primary-tint); color: var(--primary); }
.cc-chip.nv { background: var(--high); color: #3f465c; }
.cc-dot { width: 7px; height: 7px; border-radius: 50%; background: currentColor; display: inline-block; }

/* Streamlit 1.66 radio markup: [role=radiogroup] > div[data-selected] > label[data-testid=stRadioOption] > span(input) + div > (div circle, div text) */
.st-key-nav { width: 100% !important; }
.st-key-nav [role="radiogroup"] { gap: .3rem; flex-wrap: wrap; border-bottom: 1px solid var(--line); padding-bottom: .35rem; width: 100%; }
.st-key-nav [role="radiogroup"] > div { border-radius: 9px; }
.st-key-nav [data-testid="stRadioOption"] { padding: .5rem .95rem; cursor: pointer; margin: 0; }
.st-key-nav [data-testid="stRadioOption"] > div > div:first-child,
.st-key-w_source [data-testid="stRadioOption"] > div > div:first-child,
.st-key-w_rulefilter [data-testid="stRadioOption"] > div > div:first-child { display: none; }
.st-key-nav [data-testid="stRadioOption"] p { font-weight: 600; color: var(--muted); font-size: .92rem; }
.st-key-nav [role="radiogroup"] > div:hover { background: var(--low); }
.st-key-nav [role="radiogroup"] > div[data-selected="true"] { background: var(--primary); }
.st-key-nav [role="radiogroup"] > div[data-selected="true"] p { color: #fff; }

/* card-style choices */
.st-key-w_claim, .st-key-w_mode { width: 100% !important; }
.st-key-w_claim [role="radiogroup"], .st-key-w_mode [role="radiogroup"] { display: grid !important; grid-template-columns: 1fr 1fr; gap: .8rem; width: 100%; }
.st-key-w_claim [role="radiogroup"] > div, .st-key-w_mode [role="radiogroup"] > div {
  background: var(--low); border: 1.5px solid transparent; border-radius: 12px; padding: .9rem 1rem; }
.st-key-w_claim [role="radiogroup"] > div[data-selected="true"], .st-key-w_mode [role="radiogroup"] > div[data-selected="true"] {
  background: var(--card); border-color: var(--primary); box-shadow: inset 4px 0 0 var(--primary); }
.st-key-w_claim [data-testid="stRadioOption"] p, .st-key-w_mode [data-testid="stRadioOption"] p { font-family: 'Manrope', sans-serif !important; font-weight: 700; font-size: 1.02rem; color: var(--ink); }
.st-key-w_source [role="radiogroup"] { background: var(--low); border-radius: 10px; padding: .25rem; gap: .2rem; }
.st-key-w_source [role="radiogroup"] > div { border-radius: 8px; }
.st-key-w_source [data-testid="stRadioOption"] { padding: .35rem .85rem; margin: 0; }
.st-key-w_source [role="radiogroup"] > div[data-selected="true"] { background: var(--card); box-shadow: 0 1px 2px rgba(11,28,48,.1); }
.st-key-w_source [data-testid="stRadioOption"] p { font-weight: 600; }
.st-key-w_rulefilter [role="radiogroup"] { gap: .4rem; flex-wrap: wrap; }
.st-key-w_rulefilter [role="radiogroup"] > div { border-radius: 999px; background: var(--low); }
.st-key-w_rulefilter [data-testid="stRadioOption"] { padding: .3rem .85rem; margin: 0; }
.st-key-w_rulefilter [role="radiogroup"] > div[data-selected="true"] { background: var(--primary); }
.st-key-w_rulefilter [role="radiogroup"] > div[data-selected="true"] p { color: #fff; }
.st-key-w_rulefilter [data-testid="stRadioOption"] p { font-weight: 600; font-size: .85rem; }
[data-testid="stToolbar"] { display: none !important; }

/* blocks */
.cc-hero { background: linear-gradient(120deg, #e6f0ff 0%, #e8f7f4 100%); border-radius: 18px; padding: 2rem 2.2rem; margin: .6rem 0 1.2rem; }
.cc-hero h1 { font-size: 2.3rem; line-height: 1.15; margin: .5rem 0 .6rem; max-width: 22ch; }
.cc-hero p { color: var(--ink-2); font-size: 1.05rem; max-width: 60ch; margin: 0; }
.cc-card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 1.3rem 1.4rem; margin-bottom: 1rem; }
.cc-card.soft { background: var(--low); border-color: transparent; }
.cc-step { display: flex; align-items: center; gap: .7rem; margin-bottom: .2rem; }
.cc-num { width: 28px; height: 28px; border-radius: 50%; background: var(--primary); color: #fff; display: grid; place-items: center;
          font-weight: 700; font-size: .9rem; flex: none; }
.cc-step h3 { margin: 0; font-size: 1.25rem; padding: 0; }
.cc-sub { color: var(--muted); font-size: .92rem; margin: .1rem 0 .7rem; }
.cc-label { color: var(--muted); font-size: .78rem; font-weight: 700; letter-spacing: .02em; }
.cc-pill { display: inline-flex; align-items: center; gap: .35rem; padding: .22rem .65rem; border-radius: 999px; font-size: .78rem; font-weight: 700; white-space: nowrap; vertical-align: middle; }
.cc-pill.ok { background: var(--primary-tint); color: var(--primary); }
.cc-pill.cut { background: var(--error-soft); color: #93000a; }
.cc-pill.review { background: var(--warn-soft); color: var(--warn); }
.cc-pill.muted { background: var(--mid); color: var(--muted); }
.cc-row { display: flex; justify-content: space-between; align-items: flex-start; gap: 1rem; }
.cc-money { font-family: 'Manrope'; font-weight: 800; font-variant-numeric: tabular-nums; white-space: nowrap; }
.cc-quote { background: var(--low); border-radius: 10px; padding: .7rem .9rem; font-style: italic; color: var(--ink-2); font-size: .93rem; margin-top: .6rem; }
.cc-quote small { display: block; font-style: normal; color: var(--primary); font-weight: 600; margin-top: .3rem; }

.cc-verdict h1 { font-size: 2rem; margin: .5rem 0 .3rem; padding: 0; }
.cc-verdict h1 em { font-style: normal; color: var(--primary); }
.cc-metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: .9rem; margin-top: 1.1rem; }
.cc-metric { border-radius: 12px; padding: 1.1rem 1.2rem; background: var(--low); }
.cc-metric .top { display: flex; justify-content: space-between; align-items: center; gap: .5rem; color: var(--muted); font-size: .78rem; font-weight: 700; }
.cc-metric .v { font-family: 'Manrope'; font-weight: 800; font-size: 2rem; line-height: 1.15; margin: .5rem 0 .2rem; color: var(--ink); }
.cc-metric small { color: var(--muted); }
.cc-metric.cut { background: #fff0ee; } .cc-metric.cut .top, .cc-metric.cut .v { color: var(--error); }
.cc-metric.ok { background: var(--primary-tint); } .cc-metric.ok .top, .cc-metric.ok .v { color: var(--primary); }

.cc-path { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: .7rem; margin: .8rem 0 1rem; }
.cc-pstep { background: var(--low); border-radius: 10px; padding: .8rem .9rem; }
.cc-pstep .t { font-size: .72rem; font-weight: 700; color: var(--muted); min-height: 2.1em; }
.cc-pstep .v { font-family: 'Manrope'; font-weight: 800; font-size: 1.25rem; margin-top: .3rem; }
.cc-pstep.cut .v { color: var(--error); } .cc-pstep.ok { background: var(--primary-tint); } .cc-pstep.ok .v { color: var(--primary); }
.cc-pstep .bar { height: 4px; border-radius: 3px; background: var(--mid); margin-top: .5rem; overflow: hidden; }
.cc-pstep .bar span { display: block; height: 100%; background: var(--error); }
.cc-pstep.ok .bar span, .cc-pstep.base .bar span { background: var(--primary); }
.cc-stack { display: flex; height: 12px; border-radius: 6px; overflow: hidden; background: var(--mid); }
.cc-stack span { height: 100%; }
.cc-legend { display: flex; flex-wrap: wrap; gap: .3rem 1.2rem; font-size: .8rem; color: var(--muted); margin-top: .5rem; }
.cc-legend i { width: 10px; height: 10px; border-radius: 2px; display: inline-block; margin-right: 5px; vertical-align: -1px; }

.cc-grid2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(330px, 1fr)); gap: 1rem; }
.cc-grid3 { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 1rem; }
.cc-attn { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 1.1rem 1.2rem; }
.cc-attn .head { display: flex; align-items: center; gap: .5rem; font-weight: 700; font-size: .82rem; margin-bottom: .5rem; }
.cc-attn.cut .head { color: var(--error); } .cc-attn.review .head { color: var(--warn); } .cc-attn.muted .head { color: var(--muted); }
.cc-attn ul { margin: 0; padding-left: 1.1rem; } .cc-attn li { margin: .25rem 0; font-size: .95rem; }

.cc-check { background: var(--card); border: 1px solid var(--line); border-left: 5px solid var(--primary); border-radius: 12px; padding: 1rem 1.1rem; height: 100%; }
.cc-check.cut { border-left-color: var(--error); } .cc-check.review { border-left-color: #b15f00; } .cc-check.muted { border-left-color: var(--line-2); }
.cc-check h4 { margin: .5rem 0 .3rem; font-size: 1.05rem; padding: 0; }
.cc-check p { margin: 0; font-size: .92rem; color: var(--ink-2); }

.cc-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: .8rem; background: var(--low); border-radius: 12px; padding: 1rem 1.2rem; margin-top: 1rem; }
.cc-stats .v { font-family: 'Manrope'; font-weight: 800; font-size: 1.5rem; }
.cc-table { width: 100%; border-collapse: collapse; font-size: .9rem; }
.cc-table th { text-align: left; font-size: .74rem; color: var(--muted); font-weight: 700; padding: .6rem .5rem; background: var(--low); }
.cc-table td { padding: .7rem .5rem; border-top: 1px solid var(--line); vertical-align: top; }
.cc-table td.num, .cc-table th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.cc-table tr.total td { background: var(--low); font-weight: 700; }
.cc-doc { display: flex; align-items: center; gap: .6rem; background: var(--low); border-radius: 10px; padding: .6rem .7rem; }
.cc-doc > .ms { color: var(--error); }
.cc-doc b { font-size: .86rem; display: block; word-break: break-all; } .cc-doc small { color: var(--muted); font-size: .76rem; }
.cc-log { background: #0b1c30; color: #cfe3ff; border-radius: 10px; padding: .9rem 1rem; font-family: ui-monospace, Menlo, monospace; font-size: .8rem; line-height: 1.6; }
.cc-foot { color: var(--muted); font-size: .8rem; border-top: 1px solid var(--line); margin-top: 2.5rem; padding-top: .9rem; }
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"], .stDownloadButton > button[kind="primary"] {
  background: var(--primary); border-color: var(--primary); font-weight: 700; padding: .6rem 1rem; }
.stButton > button[kind="primary"]:hover, .stFormSubmitButton > button[kind="primary"]:hover { background: var(--primary-2); border-color: var(--primary-2); }
[data-testid="stVerticalBlockBorderWrapper"] { border-radius: 14px; }
:focus-visible { outline: 2px solid var(--primary) !important; outline-offset: 2px; }
</style>
"""


# =========================================================================== small helpers
def inr(x: Optional[float]) -> str:
    """Indian digit grouping: 124600 -> ₹1,24,600."""
    if x is None:
        return "-"
    neg = x < 0
    whole, frac = divmod(round(abs(float(x)) * 100), 100)
    s = str(int(whole))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    out = f"₹{s}" + (f".{int(frac):02d}" if frac else "")
    return ("-" if neg else "") + out


def ms(name: str, fill: bool = False, size: int = 20, color: str = "") -> str:
    style = f"font-size:{size}px;" + (f"color:{color};" if color else "")
    return f'<span class="ms{" fill" if fill else ""}" style="{style}">{name}</span>'


def pill(text: str, tone: str = "muted", icon: str = "") -> str:
    return f'<span class="cc-pill {tone}">{ms(icon, True, 15) if icon else ""}{esc(text)}</span>'


def html(block: str) -> None:
    st.markdown(block, unsafe_allow_html=True)


def status_of(f: Finding) -> str:
    return str(getattr(f.status, "value", f.status)).lower()


def plain(text: str) -> str:
    import re as _re
    return _re.sub(r"INR\s*([\d,]+(?:\.\d+)?)", lambda m: inr(float(m.group(1).replace(",", ""))), text or "")


def go(page: str) -> None:
    st.session_state["_goto"] = page
    st.rerun()


@st.cache_data(show_spinner=False)
def doc_info(data: bytes) -> Tuple[int, int]:
    """(pages, pages that look scanned)."""
    try:
        ext = extract_text_from_pdf(data)
        return ext.total_pages, sum(1 for p in ext.pages if p.needs_ocr)
    except Exception:
        return 0, 0


def doc_kind(name: str) -> str:
    n = name.lower()
    for key, label in (("preauth", "Pre-authorisation form"), ("pre_auth", "Pre-authorisation form"),
                       ("estimate", "Cost estimate"), ("claim_form", "Claim form"), ("pharmacy", "Pharmacy bill"),
                       ("pre_hosp", "Bill before admission"), ("post_hosp", "Bill after discharge"),
                       ("ambulance", "Ambulance bill"), ("discharge", "Discharge summary"), ("prescription", "Prescription"),
                       ("lab", "Lab report"), ("scan", "Scan report"), ("xray", "Scan report"), ("referral", "Doctor's letter"),
                       ("bill", "Hospital bill"), ("policy", "Policy")):
        if key in n:
            return label
    return "Document"


@st.cache_data(show_spinner=False)
def demo_cases() -> List[dict]:
    cases = []
    if DEMO_DIR.exists():
        for folder in sorted(p for p in DEMO_DIR.iterdir() if p.is_dir()):
            meta = json.loads((folder / "expected.json").read_text(encoding="utf-8"))
            facts = json.loads((folder / "ground_truth_facts.json").read_text(encoding="utf-8"))
            by = {f["name"]: f["value"] for f in facts}
            details = by.get("document_details") or [{}]
            cases.append({"id": folder.name, "label": meta["label"], "story": meta["story"],
                          "claim_type": meta["claim_type"], "folder": str(folder),
                          "patient": details[0].get("patient_name"),
                          "claimed": (by.get("claim_amount_summary") or {}).get("total_claimed"),
                          "diagnosis": (by.get("hospitalization_details") or {}).get("diagnosis")})
    return cases


@st.cache_data(show_spinner=False)
def gold_rulebook_with_checks() -> Tuple[dict, Dict[str, bool]]:
    from src.documents.policy_document import load_document
    rb = Rulebook.model_validate(json.loads(GOLD_RULEBOOK_PATH.read_text(encoding="utf-8")))
    pol = load_document(DEFAULT_POLICY_PATH)
    checks = {r.rule_id: check_citation(pol, r.source).verified for r in rb.rules}
    return rb.model_dump(mode="json"), checks


# =========================================================================== rule wording
def rule_types(result: Optional[AnalysisResult]) -> Dict[str, str]:
    if not result or not result.rulebook:
        return {}
    return {r.rule_id: str(getattr(r.rule_type, "value", r.rule_type)) for r in result.rulebook.rules}


def rule_title_for(rule) -> str:
    t = str(getattr(rule.rule_type, "value", rule.rule_type))
    p = rule.parameters or {}

    def num(*keys):
        for k in keys:
            try:
                if p.get(k) is not None:
                    return float(p[k])
            except (TypeError, ValueError):
                pass
        return None

    return {
        "required_documents": "Required documents submitted",
        "waiting_period": f"No illness claims in the first {int(num('duration_days') or 30)} days",
        "room_rent_cap": f"Room rent limit of {inr(num('cap', 'daily_limit_inr'))} a day",
        "sub_limit": f"Cataract surgery limit of {inr(num('max_limit_inr'))} per eye",
        "non_payable_items": "Items the policy never pays for",
        "pre_hospitalization": f"Bills up to {int(num('window_days') or 30)} days before admission",
        "post_hospitalization": f"Bills up to {int(num('window_days') or 60)} days after discharge",
        "ambulance_limit": f"Ambulance limit of {inr(num('max_limit_inr'))}",
        "sum_insured": f"Yearly sum insured of {inr(num('sum_insured_inr'))}",
        "cashless_preauthorization": "Cashless pre-approval request",
        "pre_existing_disclosure": "Existing illnesses declared",
    }.get(t, rule.description)


def rule_title(rule_id: str, result: AnalysisResult) -> str:
    if rule_id in CHECK_LABELS:
        return CHECK_LABELS[rule_id]
    rule = next((r for r in (result.rulebook.rules if result.rulebook else []) if r.rule_id == rule_id), None)
    if rule is None:
        amb = next((a for a in result.ambiguities if a.ambiguity_id == rule_id), None)
        return f"Unclear policy wording ({amb.section})" if amb else rule_id
    return rule_title_for(rule)


def display_status(f: Finding, result: AnalysisResult) -> Tuple[str, str]:
    if f.rule_id in result.not_applicable_rule_ids:
        return "Not applicable", "muted"
    s = status_of(f)
    if s == "violated":
        return ("Money deducted", "cut") if f.estimated_impact else ("Not met", "cut")
    return {"satisfied": ("Passed", "ok"), "needs_review": ("Needs review", "review"),
            "unable_to_verify": ("Can't check yet", "muted")}.get(s, (s, "muted"))


def explain(f: Finding, result: AnalysisResult) -> str:
    import re as _re
    amb = next((a for a in result.ambiguities if a.ambiguity_id == f.rule_id), None)
    if amb is not None:
        return (f"{amb.section} of the policy is unclear: {amb.issue[0].lower() + amb.issue[1:]}. This claim mentions a "
                "related medical history, so a claims officer has to decide how that section applies.")
    if rule_types(result).get(f.rule_id) == "room_rent_cap" and status_of(f) == "violated":
        m = _re.search(r"room rent of INR\s*([\d,.]+) exceeds the policy cap of INR\s*([\d,.]+)", f.explanation)
        if m:
            rate, cap = float(m.group(1).replace(",", "")), float(m.group(2).replace(",", ""))
            text = f"The room cost {inr(rate)} a day but the policy covers {inr(cap)} a day, so you pay the extra room rent."
            if "proportional" in f.explanation.lower():
                text += (f" Because the room was above the limit, the policy also pays only {cap / rate * 100:.1f}% of "
                         "the doctor and operation theatre fees.")
            return text + f" In total {inr(f.estimated_impact)} is not covered."
    return plain(f.explanation)


def find_doc_bytes(name: str) -> bytes:
    pdfs = st.session_state.claim_pdfs
    if name in pdfs:
        return pdfs[name]
    if "policy" in name.lower():
        return st.session_state.policy_bytes
    return next((v for k, v in pdfs.items() if k.lower() in name.lower() or name.lower() in k.lower()), b"")


def proof(f: Finding, key: str, result: AnalysisResult) -> None:
    with st.expander("See the proof"):
        if explain(f, result) != plain(f.explanation):
            st.caption(plain(f.explanation))
        src = f.source
        html(f'<div class="cc-quote">"{esc(src.quote)}"<small>{esc(src.document)}, page {src.page}</small></div>')
        if f.calculation_steps:
            st.markdown("**How this was worked out**")
            st.markdown("\n".join(f"{i}. {plain(s)}" for i, s in enumerate(f.calculation_steps, 1)))
        if st.checkbox("Show the page with the quote highlighted", key=f"page_{key}"):
            data = find_doc_bytes(src.document)
            if data:
                png, _, note = render_page_with_highlight(data, src.page, src.quote, dpi=120)
                if png:
                    st.image(png, caption=f"{src.document}, page {src.page}: {note}", width="stretch")
                else:
                    st.caption(note)
            else:
                st.caption("This document is not loaded, so the page cannot be shown.")


# =========================================================================== top bar
def top_bar(cfg) -> None:
    setup = st.session_state.setup
    live = setup["mode"] == "live"
    engine = (f'<span class="cc-chip nv">{ms("memory", size=16)}NVIDIA Nemotron via Nebius</span>' if live
              else f'<span class="cc-chip">{ms("science", size=16)}Demo data (no AI)</span>')
    claim = "Cashless pre-approval" if setup["claim_type"] == "cashless" else "Reimbursement"
    html(f'''<div class="cc-top"><div class="cc-brand"><div class="cc-logo">{ms("health_and_safety", True, 22)}</div>
      <div><b>ClaimCheck</b><small>Health insurance claim checker</small></div></div>
      <div class="cc-chips"><span class="cc-chip teal"><span class="cc-dot"></span>{esc(claim)}</span>{engine}
      <span class="cc-chip">{ms("description", size=16)}Policy: {esc(setup.get("policy_name", "Aura Health Assure (Plan B)"))}</span></div></div>''')


# =========================================================================== page 1: claim setup
def page_setup(cfg) -> None:
    ss = st.session_state
    setup = ss.setup
    html('''<div class="cc-hero"><span class="cc-pill ok">Check before you submit</span>
      <h1>Check your health claim before your insurer does.</h1>
      <p>Know what the policy is expected to pay, what will be deducted and why, and which document mistakes to fix.
      Every answer quotes the policy page it comes from.</p></div>''')

    left, right = st.columns([1.75, 1], gap="large")
    with left:
        with st.container(border=True):
            html('<div class="cc-step"><span class="cc-num">1</span><h3>What kind of claim?</h3></div>'
                 '<p class="cc-sub">This decides which policy rules apply.</p>')
            ct = st.radio("Claim type", ["Reimbursement", "Cashless pre-approval"],
                          index=1 if setup["claim_type"] == "cashless" else 0, key="w_claim",
                          captions=["I already paid the hospital", "Before a planned or emergency admission"],
                          label_visibility="collapsed")
            setup["claim_type"] = "cashless" if ct.startswith("Cashless") else "reimbursement"

        with st.container(border=True):
            c1, c2 = st.columns([1.3, 1])
            with c1:
                html('<div class="cc-step"><span class="cc-num">2</span><h3>Your documents</h3></div>'
                     '<p class="cc-sub">Pick a ready-made case, or upload your own PDFs.</p>')
            with c2:
                src = st.radio("Documents", ["Use a demo case", "Upload my documents"],
                               index=1 if setup["source"] == "upload" else 0, key="w_source",
                               horizontal=True, label_visibility="collapsed")
            setup["source"] = "upload" if src.startswith("Upload") else "demo"
            files: Dict[str, bytes] = {}
            if setup["source"] == "demo":
                cases = [c for c in demo_cases() if c["claim_type"] == setup["claim_type"]]
                options = [c["id"] for c in cases] + (list(PACKET_CATALOG) if setup["claim_type"] == "reimbursement" else [])
                labels = {c["id"]: c["label"] for c in cases} | {k: f"Test packet {k[-2:]}: {v}" for k, v in PACKET_CATALOG.items()}
                current = setup.get("case_id") if setup.get("case_id") in options else options[0]
                case_id = st.selectbox("Demo case", options, index=options.index(current), key="w_case",
                                       format_func=lambda k: labels.get(k, k))
                setup["case_id"] = case_id
                chosen = next((c for c in cases if c["id"] == case_id), None)
                folder = Path(chosen["folder"]) if chosen else PACKETS_DIR / case_id
                files = {f.name: f.read_bytes() for f in sorted(folder.glob("*.pdf"))}
                story = chosen["story"] if chosen else PACKET_CATALOG.get(case_id, "")
                html(f'<div class="cc-card soft" style="margin:.6rem 0">{ms("clinical_notes", size=22, color="var(--primary)")} '
                     f'<span class="cc-label" style="color:var(--primary)">CASE IN ONE LINE</span><div style="margin-top:.3rem">'
                     f'{esc(story)}</div></div>')
            else:
                setup["case_id"] = None
                ups = st.file_uploader("Claim form, bills, prescriptions, reports, discharge summary, estimate...",
                                       type=["pdf"], accept_multiple_files=True, key="w_uploads")
                if ups:
                    ss.uploads = {u.name: u.getvalue() for u in ups}
                files = dict(ss.uploads)
                pol = st.file_uploader("Your policy document (optional)", type=["pdf"], key="w_policy")
                if pol:
                    ss.own_policy = (pol.name, pol.getvalue())
                if ss.own_policy:
                    st.caption(f"Using your policy: {ss.own_policy[0]}")
                else:
                    st.caption("Using the sample policy (Aura Health Assure, Plan B).")
            ss.pending_files = files
            if files:
                tiles = []
                for name, data in files.items():
                    pages, scanned = doc_info(data)
                    note = f"{pages} page{'s' if pages != 1 else ''}" + (" &middot; scanned" if scanned else "")
                    tiles.append(f'<div class="cc-doc">{ms("picture_as_pdf", True, 24)}<div><b>{esc(name)}</b>'
                                 f'<small>{doc_kind(name)} &middot; {note}</small></div></div>')
                html(f'<div class="cc-label" style="margin:.6rem 0 .4rem">{len(files)} DOCUMENTS</div>'
                     f'<div class="cc-grid3" style="grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:.5rem">{"".join(tiles)}</div>')

        with st.container(border=True):
            html('<div class="cc-step"><span class="cc-num">3</span><h3>How to check</h3></div>'
                 '<p class="cc-sub">Demo data uses each case\'s answer key. Live AI reads the documents with NVIDIA Nemotron.</p>')
            live_label = "Live AI (NVIDIA Nemotron)"
            mode = st.radio("Check mode", ["Demo data (no AI)", live_label], index=1 if setup["mode"] == "live" else 0,
                            key="w_mode", label_visibility="collapsed",
                            captions=["Instant, works offline, demo cases only",
                                      "Nebius Token Factory" + ("" if cfg.is_live_ready else " - add API keys to enable")])
            setup["mode"] = "live" if mode == live_label and cfg.is_live_ready else "mock"
            if mode == live_label and not cfg.is_live_ready:
                html(f'<div class="cc-card soft">{ms("key_off", size=20, color="var(--warn)")} <b>Live AI needs settings:</b> '
                     f'{esc(", ".join(cfg.missing_variables))}. Add them to <code>.env</code>.</div>')

    with right:
        own = setup["source"] == "upload" and ss.own_policy
        policy_bytes = ss.own_policy[1] if own else DEFAULT_POLICY_PATH.read_bytes()
        ss.pending_policy = policy_bytes
        setup["policy_name"] = ss.own_policy[0] if own else "Aura Health Assure (Plan B)"
        chosen = next((c for c in demo_cases() if c["id"] == setup.get("case_id")), None)
        rows = ""
        if chosen:
            for k, v in (("Patient", chosen["patient"]), ("Diagnosis", chosen["diagnosis"]),
                         ("Claim type", "Cashless pre-approval" if chosen["claim_type"] == "cashless" else "Reimbursement")):
                if v:
                    rows += (f'<div class="cc-row" style="font-size:.88rem;margin:.25rem 0"><span style="color:var(--muted)">{k}</span>'
                             f'<b style="text-align:right">{esc(str(v))}</b></div>')
        claimed = chosen["claimed"] if chosen else None
        title = esc(chosen["label"]) if chosen else ("Your documents" if setup["source"] == "upload" else "Test packet")
        amount = ""
        if claimed:
            amount = (f'<div class="cc-card soft" style="margin:.8rem 0 0;display:flex;justify-content:space-between;align-items:center">'
                      f'<span class="cc-label">{"AMOUNT REQUESTED" if setup["claim_type"] == "cashless" else "AMOUNT CLAIMED"}</span>'
                      f'<span class="cc-money" style="font-size:1.6rem">{inr(claimed)}</span></div>')
        html(f'''<div class="cc-card"><div class="cc-row"><span class="cc-label">SELECTED CLAIM</span>{pill(f"{len(ss.pending_files)} documents", "ok")}</div>
          <h3 style="margin:.6rem 0 .5rem;font-size:1.15rem;padding:0">{title}</h3>{rows}{amount}</div>''')
        run = st.button("Check my claim", type="primary", width="stretch", icon=":material/verified:")
        if setup["mode"] == "live":
            if st.button("Read the policy with AI", width="stretch", icon=":material/auto_stories:"):
                compile_now(policy_bytes)
            approved = ss.approved_rule_ids
            st.caption("Rules approved: " + (f"{len(approved)}" if approved is not None else "not yet") +
                       ". Approve the rules first, or every result will say Needs review.")
        else:
            st.caption("Takes about a second with demo data.")
        html(f'<div class="cc-card soft" style="margin-top:1rem">{ms("verified", True, 20, "var(--primary)")} '
             '<b>How answers are checked</b><p style="margin:.4rem 0 0;font-size:.9rem;color:var(--ink-2)">Every rule and '
             'fact quotes a page, checked word for word against the PDF. All amounts are calculated by code, not by the AI.</p></div>')

    if run:
        run_check()


def compile_now(policy_bytes: bytes) -> None:
    ss = st.session_state
    with st.spinner("NVIDIA Nemotron is reading the policy..."):
        try:
            ss.compilation = compile_policy(policy_bytes)
            ss.compiled_policy_key = hashlib.sha1(policy_bytes).hexdigest()
            ss.approved_rule_ids = None
        except Exception as exc:
            st.error(f"Could not read the policy: {type(exc).__name__}: {plain(str(exc))}")
            return
    go(PAGES[1])


def run_check() -> None:
    ss = st.session_state
    setup = ss.setup
    files, policy_bytes = ss.pending_files, ss.pending_policy or DEFAULT_POLICY_PATH.read_bytes()
    if not files:
        st.error("Add at least one document, or pick a demo case.")
        return
    if setup["mode"] == "mock" and setup["source"] == "upload":
        st.error("Demo data cannot read uploaded documents. Choose Live AI in step 3.")
        return
    if setup["mode"] == "live" and ss.get("compiled_policy_key") != hashlib.sha1(policy_bytes).hexdigest():
        ss.compilation, ss.approved_rule_ids = None, None
    with st.spinner("Checking the claim against the policy..."):
        ss.policy_bytes, ss.claim_pdfs = policy_bytes, files
        ss.tavily_results = {}
        ss.result = analyze(policy_pdf_bytes=policy_bytes, claim_pdfs=files, mode=setup["mode"],
                            packet_id=setup.get("case_id") if setup["mode"] == "mock" else None,
                            claim_type=setup["claim_type"],
                            compilation=ss.compilation if setup["mode"] == "live" else None,
                            approved_rule_ids=ss.approved_rule_ids if setup["mode"] == "live" else None)
        ss.result_case = setup.get("case_id")
    go(PAGES[2])


# =========================================================================== page 2: policy rules
def rule_card(rule, ok: bool) -> str:
    t = str(getattr(rule.rule_type, "value", rule.rule_type))
    params = "".join(f'<div style="margin:.15rem 0"><span style="color:var(--muted)">{esc(k.replace("_", " "))}:</span> '
                     f'<b>{esc(", ".join(map(str, v)) if isinstance(v, list) else str(v))}</b></div>'
                     for k, v in (rule.parameters or {}).items())
    exc = (f'<div class="cc-card soft" style="margin:0"><span class="cc-label">EXCEPTIONS</span><div>{esc("; ".join(rule.exceptions))}</div></div>'
           if rule.exceptions else "")
    badge = pill("Quote verified", "ok", "check_circle") if ok else pill("Quote not found on page", "cut", "gpp_bad")
    return f'''<div class="cc-row"><div style="display:flex;gap:.7rem;align-items:center">{ms(RULE_ICONS.get(t, "rule"), size=24, color="var(--primary)")}
      <div><h3 style="margin:0;font-size:1.1rem;padding:0">{esc(rule_title_for(rule))}</h3><small style="color:var(--muted)">{esc(rule.description)}</small></div></div>
      <div style="text-align:right">{badge}<div style="color:var(--muted);font-size:.8rem;margin-top:.3rem">Page {rule.source.page}</div></div></div>
      <div class="cc-grid2" style="margin-top:.8rem;gap:.6rem"><div class="cc-card soft" style="margin:0"><span class="cc-label">NUMBERS</span>{params or "<div>-</div>"}</div>{exc}</div>
      <div class="cc-quote">"{esc(rule.source.quote)}"<small>Policy page {rule.source.page}</small></div>'''


def page_rules(cfg) -> None:
    ss = st.session_state
    live = ss.setup["mode"] == "live"
    if live:
        comp = ss.compilation
        if comp is None:
            html(f'<div class="cc-card">{ms("auto_stories", size=24, color="var(--primary)")} <b>The policy has not been read yet.</b>'
                 '<p class="cc-sub" style="margin-top:.4rem">NVIDIA Nemotron reads the policy and lists its rules, each with a '
                 'quote. You approve them once, then every claim uses them.</p></div>')
            if st.button("Read the policy with AI", type="primary", icon=":material/auto_stories:"):
                compile_now(ss.pending_policy or DEFAULT_POLICY_PATH.read_bytes())
            return
        rules, ambs, model = comp.rules, comp.ambiguities, comp.model
        verified = {r.rule_id: bool(comp.citation_checks.get(r.rule_id) and comp.citation_checks[r.rule_id].verified) for r in rules}
    else:
        data, verified = gold_rulebook_with_checks()
        rb = Rulebook.model_validate(data)
        rules, ambs, model = rb.rules, rb.ambiguities, None

    n_ok = sum(1 for r in rules if verified.get(r.rule_id))
    approved = ss.approved_rule_ids
    intro = ("The AI read these rules from your policy. Each quote was checked word for word against the policy page. "
             "<b>Only rules you approve are used to calculate your claim.</b>") if live else \
        "These are the sample policy's rules. Each quote was checked word for word against the policy page. Demo data uses them directly."
    html(f'''<div class="cc-hero" style="display:flex;justify-content:space-between;gap:1.5rem;flex-wrap:wrap;align-items:center">
      <div style="max-width:680px">{pill("Read by NVIDIA Nemotron" if live else "Sample policy rules", "ok")}
      <h1 style="font-size:1.8rem">Check the rules read from your policy</h1><p>{intro}</p></div>
      <div class="cc-card" style="margin:0;min-width:220px"><span class="cc-label">QUOTES FOUND ON THEIR PAGE</span>
      <div class="cc-money" style="font-size:2rem;color:var(--primary)">{n_ok} of {len(rules)}</div>
      <small style="color:var(--muted)">{esc(model) if model else "Checked against the sample policy PDF"}</small></div></div>''')
    if live:
        status = (pill("Not approved yet", "review", "pending_actions") if approved is None
                  else pill(f"{len(approved)} of {len(rules)} rules approved", "ok", "check_circle"))
        html(f'<div class="cc-card cc-row" style="align-items:center"><div><span class="cc-label">CURRENT POLICY</span>'
             f'<h3 style="margin:.2rem 0 0;font-size:1.1rem;padding:0">{esc(ss.setup.get("policy_name", "Policy"))}</h3></div>{status}</div>')
    for amb in ambs:
        html(f'''<div class="cc-card" style="background:#fff3ea;border-color:#ffdcc3"><div class="cc-row">
          <span class="cc-label" style="color:var(--warn)">{ms("warning", True, 18)} UNCLEAR POLICY WORDING</span>
          <small style="color:var(--muted)">{esc(amb.section)}, page {amb.source.page}</small></div>
          <h3 style="margin:.4rem 0;font-size:1.1rem;padding:0">{esc(amb.issue)}</h3><p style="margin:0;color:var(--ink-2)">{esc(amb.description)}
          Claims that touch this are always sent to a person.</p></div>''')

    html(f'<h3 style="margin:1.2rem 0 .6rem">{ms("fact_check", size=24, color="var(--primary)")} Rules and their numbers</h3>')
    if not live:
        for rule in rules:
            with st.container(border=True):
                html(rule_card(rule, verified.get(rule.rule_id, False)))
        return
    with st.form("rule_approval_form"):
        selections: Dict[str, bool] = {}
        for rule in rules:
            ok = verified[rule.rule_id]
            with st.container(border=True):
                html(rule_card(rule, ok))
                default = ok if approved is None else rule.rule_id in approved
                selections[rule.rule_id] = st.checkbox("Approve this rule" if ok else "Cannot approve: the quote was not found",
                                                       value=default and ok, disabled=not ok, key=f"approve_{rule.rule_id}")
        submitted = st.form_submit_button("Approve selected rules", type="primary", icon=":material/done_all:")
    if submitted:
        ss.approved_rule_ids = [rid for rid, ok in selections.items() if ok and verified.get(rid)]
        st.rerun()
    if approved is not None:
        st.success(f"{len(approved)} rules approved. Now check the claim.")
        if st.button("Check my claim", type="primary", icon=":material/verified:"):
            run_check()


# =========================================================================== page 3: results
VERDICT_TONE = {"approved": "ok", "rejected": "cut", "incomplete": "cut", "fix_documents": "cut",
                "needs_review": "review", "no_amount": "muted"}


def need_result() -> Optional[AnalysisResult]:
    result = st.session_state.result
    if result is None:
        html(f'<div class="cc-card">{ms("pending", size=24, color="var(--muted)")} <b>No claim checked yet.</b>'
             '<p class="cc-sub" style="margin-top:.4rem">Go to step 1, choose your documents and click Check my claim.</p></div>')
        return None
    if result.errors and not result.findings:
        st.error("The claim could not be checked.")
        for err in result.errors:
            st.caption(plain(err))
        st.write("Try **Check my claim** again. If it keeps failing, check the API key and model names in `.env`, "
                 "or switch to Demo data to see how the results look.")
        return None
    return result


def previous_paid(result: AnalysisResult) -> float:
    cas = next((f for f in result.extracted_facts if f.name == "claim_amount_summary"), None)
    if cas and isinstance(cas.value, dict):
        try:
            return float(cas.value.get("previously_paid_this_year") or 0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def page_results(cfg) -> None:
    result = need_result()
    if result is None:
        return
    p = result.payout or {}
    verdict, est = p.get("verdict", "needs_review"), p.get("is_estimate")
    claimed, payable, deducted = p.get("claimed"), p.get("payable"), p.get("total_deducted") or 0.0
    for err in result.errors:
        st.error(err)

    if verdict == "rejected":
        headline = "This claim is <em>not payable</em> under the policy"
    elif payable is not None and claimed:
        headline = (f"Estimated approval <em>{inr(payable)}</em> of {inr(claimed)} requested" if est
                    else f"Expected payout <em>{inr(payable)}</em> of {inr(claimed)} claimed")
    elif deducted:
        headline = f"<em>{inr(deducted)}</em> would be deducted under the policy"
    else:
        headline = "Here is what the policy says about this claim"
    sub = {"approved": "Every rule that applies was checked; the deductions below follow the policy wording.",
           "needs_review": "Some items need a person to decide before the amount is final.",
           "fix_documents": "Fix the document mistakes below before submitting, or the insurer may query or reject the claim.",
           "incomplete": "A required document or step is missing; the claim is on hold until it is fixed.",
           "rejected": "A policy condition rules this claim out; see the reason below.",
           "no_amount": "No claim amount was found in the documents, so only the rules could be checked."}.get(verdict, "")

    metrics = ""
    if claimed:
        pct_cut = deducted / claimed * 100
        share = (payable / claimed * 100) if payable is not None else 0
        metrics = f'''<div class="cc-metrics">
          <div class="cc-metric"><div class="top">{"AMOUNT REQUESTED" if est else "CLAIMED AMOUNT"}{ms("receipt_long", size=20)}</div>
            <div class="v">{inr(claimed)}</div><small>{"Doctor's cost estimate" if est else "As written on the claim form"}</small></div>
          <div class="cc-metric cut"><div class="top">TOTAL DEDUCTIONS {pill(f"-{pct_cut:.1f}%", "cut")}</div>
            <div class="v">-{inr(deducted)}</div><small>{len(p.get("deductions", []))} policy rule(s), each with proof</small></div>
          <div class="cc-metric ok"><div class="top">{"ESTIMATED APPROVAL" if est else "EXPECTED PAYOUT"}{ms("verified", True, 20)}</div>
            <div class="v">{inr(payable)}</div><small>{share:.1f}% of the {"request" if est else "claim"}</small></div>
        </div>'''
    html(f'''<div class="cc-card cc-verdict" style="padding:1.6rem 1.8rem">{pill(p.get("verdict_text", ""), VERDICT_TONE.get(verdict, "muted"))}
      <h1>{headline}</h1><p style="color:var(--ink-2);margin:0">{ms("task_alt", True, 18, "var(--primary)")} {esc(sub)}</p>{metrics}</div>''')

    if claimed and payable is not None and verdict != "rejected":
        steps = [f'<div class="cc-pstep base"><div class="t">1. {"REQUEST" if est else "CLAIM"} BASE</div><div class="v">{inr(claimed)}</div>'
                 f'<div class="bar"><span style="width:100%"></span></div></div>']
        i = 2
        for d in p.get("deductions", []):
            steps.append(f'<div class="cc-pstep cut"><div class="t">{i}. {esc(rule_title(d["rule_id"], result)).upper()}</div>'
                         f'<div class="v">-{inr(d["amount"])}</div><div class="bar"><span style="width:{min(d["amount"] / claimed * 100, 100):.1f}%"></span></div></div>')
            i += 1
        for label, amt in (("ABOVE THE BILLS", p.get("reduced_to_billed")), ("ABOVE THE SUM INSURED", p.get("capped_by_sum_insured"))):
            if amt:
                steps.append(f'<div class="cc-pstep cut"><div class="t">{i}. {label}</div><div class="v">-{inr(amt)}</div>'
                             f'<div class="bar"><span style="width:{min(amt / claimed * 100, 100):.1f}%"></span></div></div>')
                i += 1
        steps.append(f'<div class="cc-pstep ok"><div class="t">{i}. {"APPROVED" if est else "NET PAYABLE"}</div><div class="v">{inr(payable)}</div>'
                     f'<div class="bar"><span style="width:{payable / claimed * 100:.1f}%"></span></div></div>')
        si, prev = p.get("sum_insured"), previous_paid(result)
        si_block = ""
        if si:
            remaining = max(si - prev - payable, 0)
            si_block = f'''<div class="cc-card soft" style="margin:0"><div class="cc-row"><span class="cc-label">SUM INSURED THIS YEAR</span>
              <b style="font-size:.85rem">{inr(remaining)} left of {inr(si)}</b></div><div class="cc-stack" style="margin-top:.6rem">
              <span style="width:{prev / si * 100:.2f}%;background:#565e74"></span><span style="width:{payable / si * 100:.2f}%;background:var(--primary)"></span></div>
              <div class="cc-legend"><span><i style="background:#565e74"></i>Paid earlier {inr(prev)}</span>
              <span><i style="background:var(--primary)"></i>This {"approval" if est else "claim"} {inr(payable)}</span>
              <span><i style="background:var(--mid)"></i>Still available {inr(remaining)}</span></div></div>'''
        html(f'''<div class="cc-card"><h3 style="margin:0;padding:0">Where the money goes</h3>
          <p class="cc-sub">From the amount {"requested" if est else "claimed"} to the amount {"approved" if est else "paid"}, step by step.</p>
          <div class="cc-path">{"".join(steps)}</div>{si_block}</div>''')

    attention(result)
    deductions_section(result)
    needs_input_section(result)


def attention(result: AnalysisResult) -> None:
    types = rule_types(result)
    mistakes = [c for c in result.document_checks if status_of(c) == "violated"]
    blocking = [f for f in result.findings if status_of(f) == "violated" and not f.estimated_impact and f.rule_id in types]
    review = [f for f in result.findings + result.document_checks if status_of(f) == "needs_review"]
    missing = [f for f in result.findings if status_of(f) == "unable_to_verify" and f.rule_id not in result.not_applicable_rule_ids]
    cards = []
    if mistakes:
        cards.append(("cut", "difference", "Mistakes in the documents", [plain(c.explanation) for c in mistakes]))
    if blocking:
        cards.append(("cut", "timer_off", "Policy steps not met", [f"{rule_title(f.rule_id, result)}: {explain(f, result)}" for f in blocking]))
    if review:
        cards.append(("review", "person_search", "A person must review",
                      [explain(f, result) if (f.rule_id in types or f.rule_id.startswith("AMB")) else plain(f.explanation) for f in review]))
    if missing:
        cards.append(("muted", "help", "Missing information", [f"{rule_title(f.rule_id, result)}: {plain(f.explanation)}" for f in missing]))
    if not cards:
        has_cuts = bool((result.payout or {}).get("total_deducted"))
        html(f'<div class="cc-card" style="border-left:5px solid var(--primary)">{ms("check_circle", True, 22, "var(--primary)")} '
             f'<b>No action needed.</b> {"Nothing to fix in your documents; the deductions follow the policy rules." if has_cuts else "Every check that applies to this claim passed."}</div>')
        return
    total = sum(len(c[3]) for c in cards)
    body = "".join(f'<div class="cc-attn {tone}"><div class="head">{ms(icon, size=20)} {title.upper()}</div><ul>'
                   + "".join(f"<li>{esc(t)}</li>" for t in items) + "</ul></div>" for tone, icon, title, items in cards)
    html(f'''<div style="margin:1.4rem 0 .7rem" class="cc-row"><div><h3 style="margin:0;padding:0">What needs attention before you submit</h3>
      <p class="cc-sub">Sort these out to avoid queries or a rejected claim.</p></div>{pill(f"{total} item(s)", "cut" if mistakes or blocking else "review")}</div>
      <div class="cc-grid2">{body}</div>''')


def deductions_section(result: AnalysisResult) -> None:
    types = rule_types(result)
    cut = [f for f in result.findings if status_of(f) == "violated" and f.estimated_impact and types.get(f.rule_id) != "sum_insured"]
    html(f'<h3 style="margin:1.6rem 0 .2rem">{ms("policy", size=24, color="var(--primary)")} Money deducted, with proof</h3>'
         '<p class="cc-sub">Each deduction quotes the policy and shows the working.</p>')
    if not cut:
        st.success("No money is deducted under the policy rules.")
    for i, f in enumerate(cut):
        with st.container(border=True):
            t = types.get(f.rule_id, "")
            html(f'''<div class="cc-row"><div style="display:flex;gap:.8rem">
              <div style="background:var(--error-soft);color:var(--error);border-radius:10px;padding:.5rem;height:fit-content">{ms(RULE_ICONS.get(t, "rule"), size=24)}</div>
              <div><h3 style="margin:0;font-size:1.08rem;padding:0">{esc(rule_title(f.rule_id, result))} {pill(f"Policy page {f.source.page}", "muted")}</h3>
              <p style="margin:.3rem 0 0;color:var(--ink-2)">{esc(explain(f, result))}</p></div></div>
              <div class="cc-money" style="color:var(--error);font-size:1.4rem">-{inr(f.estimated_impact)}</div></div>''')
            proof(f, f"ded{i}", result)
    p = result.payout or {}
    if p.get("reduced_to_billed"):
        st.info(f"{inr(p['reduced_to_billed'])} was claimed above what the bills add up to, so the payout starts from the bills total of {inr(p.get('billed'))}.")
    if p.get("capped_by_sum_insured"):
        st.info(f"{inr(p['capped_by_sum_insured'])} is above what is left of the yearly sum insured.")


def needs_input_section(result: AnalysisResult) -> None:
    types = rule_types(result)
    open_items = [f for f in result.findings if status_of(f) in ("needs_review", "unable_to_verify")
                  and f.rule_id not in result.not_applicable_rule_ids and f.rule_id in types]
    if not open_items and not result.ambiguities:
        return
    html(f'<h3 style="margin:1.6rem 0 .2rem">{ms("contact_support", size=24, color="var(--primary)")} Needs your input</h3>'
         '<p class="cc-sub">Answer a question and the rules are checked again straight away.</p>')
    cols = st.columns(2, gap="medium")
    with cols[0]:
        if not open_items:
            st.caption("No questions for you.")
        for f in open_items:
            with st.container(border=True):
                html(f'<span class="cc-label">{esc(rule_title(f.rule_id, result)).upper()}</span><p style="margin:.3rem 0">{esc(explain(f, result))}</p>')
                fact_name = {"room_rent_cap": "room_rent_billing", "required_documents": "submitted_documents"}.get(types.get(f.rule_id), "user_clarification")
                answer = st.text_input("Your answer", key=f"input_{f.rule_id}")
                if st.button("Add and re-check", key=f"btn_{f.rule_id}", icon=":material/sync:") and answer.strip() and result.rulebook:
                    new_fact = ClaimFact(name=fact_name, value=answer.strip().split(",") if "," in answer else answer.strip(),
                                         source=SourceRef(document="user_clarification", page=1, quote=f"User clarification: {answer.strip()}"))
                    result.extracted_facts = list(result.extracted_facts) + [new_fact]
                    result.findings = evaluate(result.rulebook, result.extracted_facts)
                    st.session_state.result = result
                    st.rerun()
    with cols[1]:
        for amb in result.ambiguities:
            tavily_box(amb, "res")


def tavily_box(amb, where: str) -> None:
    ss = st.session_state
    with st.container(border=True):
        html(f'<span class="cc-label">UNCLEAR POLICY WORDING - {esc(amb.section).upper()}</span>'
             f'<div class="cc-quote" style="margin-top:.4rem">"{esc(amb.source.quote)}"</div>')
        ready = settings.is_set("TAVILY_API_KEY")
        query = st.text_input("Search official guidance for", value=f"IRDAI health insurance regulations {amb.issue}",
                              key=f"q_{where}_{amb.ambiguity_id}")
        if st.button("Search official guidance (Tavily)", key=f"tavily_btn_{where}_{amb.ambiguity_id}", disabled=not ready,
                     icon=":material/travel_explore:"):
            with st.spinner("Searching..."):
                ss.tavily_results[amb.ambiguity_id] = tavily_client.search(query=query)
        if not ready:
            st.caption("Add TAVILY_API_KEY to enable the search.")
        res = ss.tavily_results.get(amb.ambiguity_id)
        if res and res.success:
            html(pill("Outside guidance - does not change the policy result", "review"))
            for item in res.results:
                st.markdown(f"- [{item.title}]({item.url}): {item.snippet}")
        elif res:
            st.warning(f"Search did not work: {res.error}")


# =========================================================================== page 4: timeline and rules
def page_timeline(cfg) -> None:
    result = need_result()
    if result is None:
        return
    facts = {f.name: f for f in result.extracted_facts}
    hosp = facts.get("hospitalization_details")
    adm = _date(hosp.value.get("admission_date")) if hosp and isinstance(hosp.value, dict) else None
    dis = _date(hosp.value.get("discharge_date")) if hosp and isinstance(hosp.value, dict) else None
    rules = result.rulebook.rules if result.rulebook else []

    def window(t, default):
        return next((int(r.parameters.get("window_days", default)) for r in rules
                     if str(getattr(r.rule_type, "value", r.rule_type)) == t), default)

    pre, post = window("pre_hospitalization", 30), window("post_hospitalization", 60)
    bills = []
    for name, side in (("pre_hospitalization_expenses", "before"), ("post_hospitalization_expenses", "after")):
        fact = facts.get(name)
        for item in (fact.value if fact and isinstance(fact.value, list) else []):
            d = _date(item.get("date"))
            try:
                amt = float(item.get("amount") or 0)
            except (TypeError, ValueError):
                amt = 0.0
            anchor = adm if side == "before" else dis
            days = ((anchor - d).days if side == "before" else (d - anchor).days) if (d and anchor) else None
            inside = None if days is None else 0 <= days <= (pre if side == "before" else post)
            bills.append({"date": d, "item": item.get("description") or "Bill", "amount": amt, "inside": inside, "side": side, "days": days})
    ok_sum = sum(b["amount"] for b in bills if b["inside"])
    out_sum = sum(b["amount"] for b in bills if b["inside"] is False)

    html(f'''<div class="cc-row" style="margin:.4rem 0 1rem;flex-wrap:wrap"><div><h1 style="font-size:1.9rem;margin:0;padding:0">Coverage timeline and policy rules</h1>
      <p class="cc-sub">Bills before admission and after discharge are covered only inside the policy windows.</p></div>
      <div style="display:flex;gap:.7rem;flex-wrap:wrap"><div class="cc-metric ok" style="min-width:170px"><div class="top">COVERED BILLS</div><div class="v" style="font-size:1.5rem">{inr(ok_sum)}</div></div>
      <div class="cc-metric cut" style="min-width:170px"><div class="top">OUTSIDE THE WINDOW</div><div class="v" style="font-size:1.5rem">{inr(out_sum)}</div></div></div></div>''')

    with st.container(border=True):
        if adm is None:
            html(f'{ms("info", size=20, color="var(--muted)")} The admission date is not in the documents, so the timeline cannot be drawn.')
        else:
            end = dis or adm
            desc = (f'{pre} days before admission ({(adm - timedelta(days=pre)).strftime("%d %b")} to {adm.strftime("%d %b")}), '
                    f'hospital stay {adm.strftime("%d %b")} to {end.strftime("%d %b %Y")}')
            if dis:
                desc += f', {post} days after discharge (to {(dis + timedelta(days=post)).strftime("%d %b %Y")})'
            html(f'<div class="cc-step"><span class="cc-num">{ms("timeline", size=18)}</span><h3>Hospital stay and covered windows</h3></div>'
                 f'<p class="cc-sub">{desc}.</p>')
            try:
                import altair as alt
                import pandas as pd
                bands = [{"label": f"{pre} days before admission", "start": adm - timedelta(days=pre), "end": adm, "kind": "Covered window"},
                         {"label": "In hospital", "start": adm, "end": end + timedelta(days=1), "kind": "Hospital stay"}]
                if dis:
                    bands.append({"label": f"{post} days after discharge", "start": dis, "end": dis + timedelta(days=post), "kind": "Covered window"})
                pts = [b for b in bills if b["date"]]
                rows = (["Bills"] if pts else []) + [b["label"] for b in bands]
                y = alt.Scale(domain=rows)
                layers = [alt.Chart(pd.DataFrame(bands)).mark_bar(height=24, cornerRadius=6).encode(
                    x=alt.X("start:T", title=None), x2="end:T", y=alt.Y("label:N", title=None, scale=y, axis=alt.Axis(labelLimit=240)),
                    color=alt.Color("kind:N", scale=alt.Scale(domain=["Covered window", "Hospital stay"], range=["#89f5e7", "#0b1c30"]), title=None),
                    tooltip=["label:N", "start:T", "end:T"])]
                if pts:
                    df = pd.DataFrame([{"date": b["date"], "row": "Bills", "label": f'{b["item"]} ({inr(b["amount"])})',
                                        "status": "Covered" if b["inside"] else "Outside the window"} for b in pts])
                    layers.append(alt.Chart(df).mark_point(filled=True, size=170).encode(
                        x="date:T", y=alt.Y("row:N", title=None, scale=y),
                        color=alt.Color("status:N", scale=alt.Scale(domain=["Covered", "Outside the window"], range=["#00685f", "#ba1a1a"]), title=None),
                        tooltip=["label:N", "date:T", "status:N"]))
                st.altair_chart(alt.layer(*layers).resolve_scale(color="independent").properties(height=56 * len(rows)), width="stretch")
            except Exception:
                pass
    if bills:
        rows_html = ""
        for b in sorted(bills, key=lambda x: (x["date"] is None, x["date"] or 0)):
            if b["inside"] is None:
                status, adm_amt = pill("Date missing", "review"), "-"
            elif b["inside"]:
                status, adm_amt = pill(f'Covered ({b["days"]} days {b["side"]})', "ok", "check_circle"), inr(b["amount"])
            else:
                status, adm_amt = pill(f'Outside ({b["days"]} days {b["side"]})', "cut", "cancel"), f'<s>{inr(0)}</s>'
            rows_html += (f'<tr><td>{b["date"].strftime("%d %b %Y") if b["date"] else "-"}</td><td><b>{esc(b["item"])}</b><br>'
                          f'<small style="color:var(--muted)">{"Before admission" if b["side"] == "before" else "After discharge"}</small></td>'
                          f'<td class="num">{inr(b["amount"])}</td><td>{status}</td><td class="num"><b>{adm_amt}</b></td></tr>')
        html(f'''<div class="cc-card"><h3 style="margin:0 0 .7rem;padding:0">Bills before and after the stay</h3><table class="cc-table">
          <tr><th>DATE</th><th>ITEM</th><th class="num">BILLED</th><th>WINDOW</th><th class="num">COVERED</th></tr>{rows_html}
          <tr class="total"><td colspan="2">Total</td><td class="num">{inr(ok_sum + out_sum)}</td><td>{inr(out_sum)} outside the window</td><td class="num">{inr(ok_sum)}</td></tr></table></div>''')

    html(f'<h3 style="margin:1.6rem 0 .2rem">{ms("rule", size=24, color="var(--primary)")} All policy rules</h3>'
         f'<p class="cc-sub">{len(result.findings)} rules and policy clauses checked against this claim.</p>')
    groups: Dict[str, List[Finding]] = {}
    for f in result.findings:
        groups.setdefault(display_status(f, result)[0], []).append(f)
    labels = ["All"] + [g for g in ("Money deducted", "Not met", "Needs review", "Can't check yet", "Passed", "Not applicable") if g in groups]
    choice = st.radio("Filter", [f"{g} ({len(result.findings) if g == 'All' else len(groups[g])})" for g in labels],
                      horizontal=True, key="w_rulefilter", label_visibility="collapsed")
    picked = choice.rsplit(" (", 1)[0]
    shown = result.findings if picked == "All" else groups.get(picked, [])
    types = rule_types(result)
    for i, f in enumerate(shown):
        word, tone = display_status(f, result)
        t = types.get(f.rule_id, "")
        impact = (f'<div style="text-align:right"><small class="cc-label">AMOUNT</small><div class="cc-money" style="color:var(--error);font-size:1.3rem">'
                  f'-{inr(f.estimated_impact)}</div></div>') if (status_of(f) == "violated" and f.estimated_impact) else ""
        bg = {"cut": "var(--error-soft)", "review": "var(--warn-soft)", "ok": "var(--primary-tint)"}.get(tone, "var(--mid)")
        fg = {"cut": "var(--error)", "review": "var(--warn)", "ok": "var(--primary)"}.get(tone, "var(--muted)")
        with st.container(border=True):
            html(f'''<div class="cc-row"><div style="display:flex;gap:.8rem"><div style="background:{bg};color:{fg};border-radius:10px;padding:.5rem;height:fit-content">{ms(RULE_ICONS.get(t, "policy"), size=22)}</div>
              <div><h3 style="margin:0;font-size:1.05rem;padding:0">{esc(rule_title(f.rule_id, result))} {pill(word, tone)}</h3>
              <p style="margin:.3rem 0 0;color:var(--ink-2)">{esc(explain(f, result))}</p></div></div>{impact}</div>''')
            if f.rule_id not in result.not_applicable_rule_ids:
                proof(f, f"rule{i}", result)


# =========================================================================== page 5: documents and medical history
def page_documents(cfg) -> None:
    result = need_result()
    if result is None:
        return
    ss = st.session_state
    checks = result.document_checks
    clear = sum(1 for c in checks if status_of(c) == "satisfied")
    issues = [c for c in checks if status_of(c) == "violated"]
    pages = sum(doc_info(b)[0] for b in ss.claim_pdfs.values())
    disc = next((f for f in result.findings if rule_types(result).get(f.rule_id) == "pre_existing_disclosure"
                 and f.rule_id not in result.not_applicable_rule_ids), None)
    disc_word = {"needs_review": ("Review needed", "var(--warn)"), "satisfied": ("All declared", "var(--primary)")}.get(
        status_of(disc) if disc else "", ("Not checked", "var(--muted)"))
    html(f'''<div class="cc-card" style="padding:1.6rem 1.8rem"><h1 style="font-size:1.9rem;margin:0;padding:0">Document checks and medical history</h1>
      <p class="cc-sub">Cross-checks between your claim form, bills and medical records, and the illnesses found in them.</p>
      <div class="cc-stats"><div><span class="cc-label">DOCUMENTS READ</span><div class="v">{len(ss.claim_pdfs)} <small style="font-size:.8rem;color:var(--primary)">{pages} pages</small></div></div>
      <div><span class="cc-label">CHECKS PASSED</span><div class="v" style="color:var(--primary)">{clear} of {len(checks)}</div></div>
      <div><span class="cc-label">MISTAKES FOUND</span><div class="v" style="color:{"var(--error)" if issues else "var(--primary)"}">{len(issues)}</div></div>
      <div><span class="cc-label">DECLARED ILLNESSES</span><div class="v" style="color:{disc_word[1]}">{disc_word[0]}</div></div></div></div>''')

    html(f'<h3 style="margin:1.4rem 0 .2rem">{ms("fact_check", size=24, color="var(--primary)")} Document cross-checks</h3>'
         '<p class="cc-sub">These never change the payout; they show what to fix before submitting.</p>')
    if not checks:
        st.info("There was not enough detail in the documents to cross-check them. Add the claim form, bills and discharge summary together.")
    else:
        cards = []
        order = {"violated": 0, "needs_review": 1, "satisfied": 2}
        for c in sorted(checks, key=lambda c: order.get(status_of(c), 3)):
            s = status_of(c)
            tone, word, icon = {"violated": ("cut", "Mistake found", "crisis_alert"), "needs_review": ("review", "Please confirm", "pending_actions"),
                                "satisfied": ("ok", "No problem", "verified")}.get(s, ("muted", s, "help"))
            cards.append(f'''<div class="cc-check {tone}"><div class="cc-row"><span class="cc-label">{ms(icon, True, 16)} CHECK</span>{pill(word, tone)}</div>
              <h4>{esc(CHECK_LABELS.get(c.rule_id, c.rule_id))}</h4><p>{esc(plain(c.explanation))}</p>
              <div class="cc-quote" style="font-size:.84rem">"{esc(c.source.quote)}"<small>{esc(c.source.document)}</small></div></div>''')
        html(f'<div class="cc-grid3">{"".join(cards)}</div>')

    html(f'<h3 style="margin:1.8rem 0 .2rem">{ms("clinical_notes", size=24, color="var(--warn)")} Medical history and disclosure</h3>'
         '<p class="cc-sub">Illnesses the patient declared, compared with illnesses found anywhere in the medical records.</p>')
    left, right = st.columns([1.7, 1], gap="large")
    facts = {f.name: f for f in result.extracted_facts}
    with left:
        declared = facts.get("declared_conditions")
        conds = declared.value.get("conditions", []) if declared and isinstance(declared.value, dict) else None
        decl_text = ", ".join(conds) if conds else ("None declared" if declared else "Not stated in the documents")
        found = facts.get("medical_conditions_found")
        found_items = found.value if found and isinstance(found.value, list) else []
        declared_tokens = [set(str(d).lower().split()) for d in (conds or [])]
        found_cards = ""
        for c in found_items:
            name = str(c.get("condition"))
            was_declared = any(set(name.lower().split()) & t for t in declared_tokens)
            tone = "ok" if was_declared else "review"
            tag = pill("Declared", "ok") if was_declared else pill("Not declared", "review")
            inferred = pill("Inferred from a long-term medicine", "muted") if c.get("inferred_from_medicine") else ""
            found_cards += (f'<div class="cc-check {tone}"><div class="cc-row"><span class="cc-label">FOUND IN {esc(str(c.get("document", "records"))).upper()}</span>{tag}</div>'
                            f'<h4>{esc(name)}</h4><p>{esc(str(c.get("evidence", "")))}</p><div style="margin-top:.4rem">{inferred}</div></div>')
        html(f'''<div class="cc-card"><span class="cc-label">DECLARED ON THE CLAIM OR PRE-AUTHORISATION FORM</span>
          <h3 style="margin:.3rem 0 0;font-size:1.1rem;padding:0">{esc(decl_text)}</h3></div>
          <div class="cc-label" style="margin:.4rem 0 .5rem">FOUND IN THE MEDICAL RECORDS</div>
          <div class="cc-grid2" style="margin-bottom:1rem">{found_cards or '<div class="cc-card soft">No existing illness found in the records.</div>'}</div>''')
        related = [f for f in result.findings if (rule_types(result).get(f.rule_id) == "pre_existing_disclosure"
                   or f.rule_id in {a.ambiguity_id for a in result.ambiguities}) and f.rule_id not in result.not_applicable_rule_ids]
        for i, f in enumerate(related):
            word, tone = display_status(f, result)
            with st.container(border=True):
                html(f'<div class="cc-row"><h3 style="margin:0;font-size:1.05rem;padding:0">{esc(rule_title(f.rule_id, result))}</h3>{pill(word, tone)}</div>'
                     f'<p style="margin:.4rem 0 0;color:var(--ink-2)">{esc(explain(f, result))}</p>')
                proof(f, f"med{i}", result)
        for amb in result.ambiguities:
            tavily_box(amb, "med")
    with right:
        rows = ""
        for name, data in ss.claim_pdfs.items():
            pg, scanned = doc_info(data)
            note = pill("Scanned, read by OCR", "review") if scanned else pill("Text", "ok")
            rows += (f'<div class="cc-doc" style="margin-bottom:.5rem">{ms("description", size=22)}<div style="flex:1"><b>{esc(name)}</b>'
                     f'<small>{doc_kind(name)} &middot; {pg} page(s)</small></div>{note}</div>')
        html(f'<div class="cc-card"><div class="cc-row"><h3 style="margin:0;font-size:1.1rem;padding:0">Documents read</h3>'
             f'{pill(f"{len(ss.claim_pdfs)} files", "ok")}</div><div style="margin-top:.8rem">{rows}</div></div>')


# =========================================================================== page 6: report
def page_report(cfg) -> None:
    result = need_result()
    if result is None:
        return
    ss = st.session_state
    p = result.payout or {}
    est = p.get("is_estimate")
    guidance = [{"query": r.query, "url": i.url, "title": i.title, "snippet": i.snippet}
                for r in ss.tavily_results.values() if r.success for i in r.results]
    md = generate_markdown_report(policy_name="synthetic_policy.pdf", claim_names=list(ss.claim_pdfs), findings=result.findings,
                                  unresolved_items=result.unresolved_items, mode=result.mode,
                                  claim_id=ss.get("result_case") or "uploaded_claim",
                                  policy_id=result.policy_number or result.policy_id, external_guidance=guidance)
    summary = ["## Payout summary", "", f"- Claim type: {result.claim_type}", f"- Result: {p.get('verdict_text', '-')}",
               f"- {'Requested' if est else 'Claimed'}: {inr(p.get('claimed'))}", f"- Deducted under the policy: {inr(p.get('total_deducted'))}",
               f"- {'Estimated approval' if est else 'Payable'}: {inr(p.get('payable'))}", "", "## Document checks", ""]
    summary += [f"- {CHECK_LABELS.get(c.rule_id, c.rule_id)}: {status_of(c)}. {plain(c.explanation)}" for c in result.document_checks]
    full = "\n".join(summary) + "\n\n" + md

    top = st.columns([3, 1])
    with top[0]:
        html(f'<h1 style="font-size:1.9rem;margin:.2rem 0 0;padding:0">Report and how it was checked</h1>'
             f'<p class="cc-sub">Case: {esc(ss.get("result_case") or "uploaded documents")} &middot; Policy {esc(result.policy_number or "-")}</p>')
    with top[1]:
        st.download_button("Download the report", data=full, file_name="claimcheck_report.md", mime="text/markdown",
                           type="primary", width="stretch", icon=":material/download:")
    si = p.get("sum_insured")
    remaining = max(si - previous_paid(result) - (p.get("payable") or 0), 0) if si else None
    html(f'''<div class="cc-card" style="padding:0;overflow:hidden"><div style="background:var(--low);padding:1.2rem 1.6rem" class="cc-row">
      <div>{pill(("Cashless pre-approval" if est else "Reimbursement"), "muted")}<h3 style="margin:.5rem 0 0;padding:0">Final result</h3></div>
      {pill(p.get("verdict_text", "-"), VERDICT_TONE.get(p.get("verdict"), "muted"))}</div>
      <div class="cc-metrics" style="padding:0 1.6rem 1.4rem">
      <div class="cc-metric"><div class="top">{"REQUESTED" if est else "TOTAL CLAIMED"}{ms("receipt_long")}</div><div class="v">{inr(p.get("claimed"))}</div></div>
      <div class="cc-metric cut"><div class="top">DEDUCTED{ms("remove_circle_outline")}</div><div class="v">-{inr(p.get("total_deducted") or 0)}</div></div>
      <div class="cc-metric ok"><div class="top">{"ESTIMATED APPROVAL" if est else "EXPECTED PAYOUT"}{ms("check_circle", True)}</div><div class="v">{inr(p.get("payable"))}</div></div>
      <div class="cc-metric"><div class="top">SUM INSURED LEFT{ms("shield")}</div><div class="v">{inr(remaining)}</div></div></div></div>''')

    if result.document_checks:
        items = ""
        for c in result.document_checks:
            s = status_of(c)
            tone, word, icon = {"violated": ("cut", "Fix", "error"), "needs_review": ("review", "Confirm", "pending"),
                                "satisfied": ("ok", "Valid", "check")}.get(s, ("muted", s, "help"))
            items += (f'<div class="cc-doc" style="background:{"#fff0ee" if tone == "cut" else "var(--low)"}">'
                      f'{ms(icon, True, 22, "var(--error)" if tone == "cut" else "var(--primary)")}'
                      f'<div style="flex:1"><b>{esc(CHECK_LABELS.get(c.rule_id, c.rule_id))}</b><small>{esc(plain(c.explanation))[:150]}</small></div>{pill(word, tone)}</div>')
        clear = sum(1 for c in result.document_checks if status_of(c) == "satisfied")
        html(f'<div class="cc-card"><div class="cc-row"><h3 style="margin:0;padding:0">Document checks</h3>{pill(f"{clear} of {len(result.document_checks)} passed", "ok")}</div>'
             f'<div class="cc-grid3" style="margin-top:.8rem;gap:.6rem">{items}</div></div>')

    rows = ""
    for f in result.findings:
        if f.rule_id in result.not_applicable_rule_ids:
            continue
        word, tone = display_status(f, result)
        cut = f.estimated_impact if status_of(f) == "violated" and f.estimated_impact else 0
        rows += (f'<tr><td><b>{esc(rule_title(f.rule_id, result))}</b><br><small style="color:var(--muted)">{esc(f.source.document)}, page {f.source.page}</small></td>'
                 f'<td>{pill(word, tone)}</td><td>{esc(explain(f, result))}</td>'
                 f'<td class="num" style="color:{"var(--error)" if cut else "inherit"}"><b>{"-" + inr(cut) if cut else inr(0)}</b></td></tr>')
    html(f'''<div class="cc-card"><span class="cc-label">RULE BY RULE</span><h3 style="margin:.2rem 0 .8rem;padding:0">Policy rules applied to this claim</h3>
      <table class="cc-table"><tr><th>RULE AND SOURCE</th><th>RESULT</th><th>WHY</th><th class="num">DEDUCTED</th></tr>{rows}
      <tr class="total"><td colspan="3">Total deducted under the policy</td><td class="num">-{inr(p.get("total_deducted") or 0)}</td></tr></table></div>''')

    html(f'<h3 style="margin:1.6rem 0 .6rem">{ms("developer_board", size=24, color="var(--primary)")} How this was checked</h3>')
    live = result.mode == "LIVE"
    model_txt = (f"Policy read by <code>{esc(cfg.model_reasoning or '-')}</code>, documents read by <code>{esc(cfg.model_fast or '-')}</code>, "
                 f"both on Nebius Token Factory (<code>{esc(cfg.endpoint_host or '-')}</code>).") if live else \
        "Demo data: the facts come from the case's answer key, so no AI was used for this run."
    html(f'''<div class="cc-card cc-row" style="align-items:center"><div style="display:flex;gap:.9rem;align-items:center">
      <div class="cc-logo" style="width:46px;height:46px">{ms("smart_toy", True, 24)}</div><div><h3 style="margin:0;font-size:1.1rem;padding:0">
      {"NVIDIA Nemotron" if live else "Demo data"} {pill("via Nebius Token Factory", "muted") if live else ""}</h3>
      <p style="margin:.2rem 0 0;color:var(--ink-2)">{model_txt}</p></div></div>{pill("Live AI" if live else "No AI used", "ok" if live else "muted")}</div>
      <div class="cc-grid2"><div class="cc-card">{ms("calculate", True, 22, "var(--primary)")} <b>Amounts calculated by code</b>
      <p style="margin:.4rem 0 0;color:var(--ink-2)">Every rupee amount, limit, window and proportional deduction is calculated with exact decimal arithmetic in Python. The AI never produces amounts.</p></div>
      <div class="cc-card">{ms("find_in_page", True, 22, "var(--primary)")} <b>Quotes checked against the PDF</b>
      <p style="margin:.4rem 0 0;color:var(--ink-2)">Every rule and fact cites a page and a quote. The quote must appear word for word on that page, or the item is flagged for review.</p></div></div>''')
    t = result.timings or {}
    n_docs = len(ss.claim_pdfs)
    pages = sum(doc_info(b)[0] for b in ss.claim_pdfs.values())
    scanned = sum(doc_info(b)[1] for b in ss.claim_pdfs.values())
    n_rules = len(result.rulebook.rules) if result.rulebook else 0
    log = [f"documents: {n_docs} files, {pages} pages, {scanned} scanned page(s) read with OCR",
           f"policy rules: {n_rules} in the rulebook, {len(result.approved_rule_ids) if live else n_rules} applied",
           f"facts extracted: {len(result.extracted_facts)}; rule results: {len(result.findings)}; document checks: {len(result.document_checks)}"]
    log += [f"time {k.replace('_sec', '')}: {v:.2f}s" for k, v in t.items()]
    log += [f"note: {w}" for w in result.ocr_warnings]
    html(f'''<div class="cc-card"><div class="cc-row"><b>{ms("document_scanner", size=20)} Run details</b>{pill(result.mode.title() + " mode", "muted")}</div>
      <div class="cc-log" style="margin-top:.7rem">{"<br>".join(esc(line) for line in log)}</div></div>''')
    with st.expander("Full report text"):
        st.markdown(full)


# =========================================================================== main
def main() -> None:
    html(CSS)
    cfg = inspect_configuration()
    ss = st.session_state
    defaults = {"result": None, "policy_bytes": b"", "claim_pdfs": {}, "tavily_results": {}, "compilation": None,
                "approved_rule_ids": None, "uploads": {}, "own_policy": None, "pending_files": {}, "pending_policy": b"",
                "setup": {"claim_type": "reimbursement", "source": "demo", "case_id": None, "mode": "mock"}}
    for k, v in defaults.items():
        ss.setdefault(k, v)
    if "_goto" in ss:
        ss["nav"] = ss.pop("_goto")
    top_bar(cfg)
    page = st.radio("Step", PAGES, key="nav", horizontal=True, label_visibility="collapsed")
    {PAGES[0]: page_setup, PAGES[1]: page_rules, PAGES[2]: page_results, PAGES[3]: page_timeline,
     PAGES[4]: page_documents, PAGES[5]: page_report}[page](cfg)
    html(f'<div class="cc-foot">{ms("verified", True, 16, "var(--primary)")} Amounts calculated by code &middot; quotes checked against the PDF '
         '&middot; AI rules applied only after a person approves them. Fictional sample data; not an insurer\'s decision.</div>')


if __name__ == "__main__":
    main()

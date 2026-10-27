"""Live baseline run: policy compiler + claim extractor against ground truth.

Usage:  python scripts/run_baseline.py [output_folder]
        (default output folder: evaluation/baseline; use a new folder for later runs,
        e.g. evaluation/after_fixes, so the original baseline is never overwritten)

Makes real Nebius Token Factory calls (1 compile + 3 claim extractions, plus any
automatic retries). Nothing in the compiler, extractor or gold data is changed:
a recording wrapper around the API client captures the raw replies, token usage,
latency and finish reasons.

Writes:
  evaluation/baseline/compiled_rulebook_live.json
  evaluation/baseline/compiler_raw_reply.txt
  evaluation/baseline/compiler_vs_gold.md
  evaluation/baseline/claim_extraction_results.md
  evaluation/baseline/run_info.md
  evaluation/baseline/calls.json
  data/packets/<packet>/extracted_facts_live.json   (next to ground_truth_facts.json)
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ai.claim_extractor import ALLOWED_FACTS, extract_claim_facts  # noqa: E402
from src.ai.claim_loader import load_claim_document  # noqa: E402
from src.ai.nebius_client import get_client, get_model, parse_json_response  # noqa: E402
from src.ai.policy_compiler import compile_policy_rules  # noqa: E402
from src.documents.evidence import check_citation  # noqa: E402
from src.documents.ocr import is_tesseract_available  # noqa: E402
from src.documents.policy_document import load_document  # noqa: E402
from src.schemas import Rulebook  # noqa: E402

OUT = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "evaluation/baseline")
RUN_TAG = "live" if OUT.name == "baseline" else OUT.name
PACKETS = {
    "packet_05": "digital (room rent within limit)",
    "packet_11": "scanned hospital bill (proportional room-rent case)",
    "packet_02": "missing document (discharge summary replaced by an 'awaited' notice)",
}


# --------------------------------------------------------------------------- recording client
class RecordingClient:
    """Pass-through wrapper that records every chat.completions.create call."""

    def __init__(self, real):
        self._real = real
        self.calls = []
        self.label = ""
        outer = self

        class _Completions:
            def create(self, **kwargs):
                start = time.time()
                record = {"label": outer.label, "model": kwargs.get("model"),
                          "max_tokens": kwargs.get("max_tokens")}
                try:
                    resp = real.chat.completions.create(**kwargs)
                except Exception as exc:
                    record.update(latency_s=round(time.time() - start, 2), error=repr(exc))
                    outer.calls.append(record)
                    raise
                choice = resp.choices[0]
                usage = getattr(resp, "usage", None)
                record.update(
                    latency_s=round(time.time() - start, 2),
                    finish_reason=getattr(choice, "finish_reason", None),
                    prompt_tokens=getattr(usage, "prompt_tokens", None),
                    completion_tokens=getattr(usage, "completion_tokens", None),
                    total_tokens=getattr(usage, "total_tokens", None),
                    raw_content=choice.message.content,
                    reasoning_content=getattr(choice.message, "reasoning_content", None),
                )
                outer.calls.append(record)
                return resp

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


# --------------------------------------------------------------------------- comparison helpers
def norm(v):
    """Normalise for comparison only: snake_case strings, floats, sorted lists."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().lower()
        try:
            return float(s.replace(",", ""))
        except ValueError:
            return re.sub(r"[^a-z0-9/.]+", "_", s).strip("_")
    if isinstance(v, list):
        return sorted((norm(x) for x in v), key=str)
    if isinstance(v, dict):
        return {k: norm(x) for k, x in v.items()}
    return v


def fmt(v):
    return "`" + json.dumps(v, ensure_ascii=False) + "`"


def git(*args):
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


# --------------------------------------------------------------------------- step 1 + 2
def run_compiler(client, policy, gold):
    client.label = "policy_compiler"
    error = None
    result = None
    try:
        result = compile_policy_rules(policy, policy_id=gold.policy_id, client=client)
    except Exception as exc:
        error = "".join(traceback.format_exception_only(type(exc), exc)).strip()

    raw_calls = [c for c in client.calls if c["label"] == "policy_compiler"]
    with open(OUT / "compiler_raw_reply.txt", "w", encoding="utf-8") as fh:
        for i, c in enumerate(raw_calls, 1):
            fh.write(f"===== CALL {i} | model={c.get('model')} | max_tokens={c.get('max_tokens')} "
                     f"| finish_reason={c.get('finish_reason')} =====\n")
            if c.get("reasoning_content"):
                fh.write("----- reasoning_content -----\n" + c["reasoning_content"] + "\n")
            fh.write("----- content -----\n" + (c.get("raw_content") or "<EMPTY>") + "\n\n")
            if c.get("error"):
                fh.write(f"ERROR: {c['error']}\n\n")

    if result is not None:
        (OUT / "compiled_rulebook_live.json").write_text(json.dumps({
            "policy_id": result.policy_id,
            "ambiguities": [a.model_dump(mode="json") for a in getattr(result, "ambiguities", [])],
            "model": result.model,
            "rules": [r.model_dump(mode="json") for r in result.rules],
            "citation_checks": {k: v.model_dump(mode="json") for k, v in result.citation_checks.items()},
            "rejected": result.rejected,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
    return result, error, raw_calls


def compare_compiler(result, error, raw_calls, policy, gold):
    lines = ["# Policy compiler vs gold rulebook (live baseline)", ""]
    if result is None:
        lines += [f"**Compiler failed:** `{error}`", "", "No rules to compare. See `compiler_raw_reply.txt`."]
        return "\n".join(lines), {}

    lines += [f"- Model: `{result.model}`",
              f"- Rules returned: {len(result.rules)} accepted, {len(result.rejected)} rejected by validation",
              "- Matching: each gold rule is matched to the first compiled rule with the same `rule_type`.",
              "- Values are compared after normalisation only (case, spaces/underscores, int vs float, list order).",
              "  Wording differences such as `illness` vs `illnesses` are counted as **wrong** and shown below.",
              ""]

    summary_rows, diff_blocks, used, stats = [], [], set(), {"params_ok": 0, "params_total": 0}
    for g in gold.rules:
        match = next((r for r in result.rules if r.rule_type == g.rule_type and r.rule_id not in used), None)
        if match:
            used.add(match.rule_id)
            chk = check_citation(policy, match.source)
            cite = f"{'yes' if chk.verified else 'NO'} (cited p{match.source.page}, gold p{g.source.page}, {chk.reason})"
            ok_keys, bad = [], []
            for key, gval in g.parameters.items():
                cval = match.parameters.get(key, "<missing>")
                stats["params_total"] += 1
                if key in match.parameters and norm(cval) == norm(gval):
                    ok_keys.append(key)
                    stats["params_ok"] += 1
                else:
                    bad.append((key, gval, cval))
            extra_keys = [k for k in match.parameters if k not in g.parameters]
            summary_rows.append(f"| {g.rule_id} | yes (`{match.rule_id}`) | yes | "
                                f"{len(ok_keys)}/{len(g.parameters)} | {cite} |")
            if bad or extra_keys:
                block = [f"### {g.rule_id} ({g.rule_type})", "", "| Parameter | Gold | Compiled |", "|---|---|---|"]
                block += [f"| {k} | {fmt(gv)} | {fmt(cv)} |" for k, gv, cv in bad]
                block += [f"| {k} (extra, not in gold) | - | {fmt(match.parameters[k])} |" for k in extra_keys]
                diff_blocks += block + [""]
        else:
            stats["params_total"] += len(g.parameters)
            summary_rows.append(f"| {g.rule_id} | **NO** | - | 0/{len(g.parameters)} | - |")

    extras = [r for r in result.rules if r.rule_id not in used]
    raw_text = " ".join((c.get("raw_content") or "") + " " + (c.get("reasoning_content") or "") for c in raw_calls)
    pre_rule = [r for r in result.rules if re.search(r"pre.?existing", r.description + json.dumps(r.parameters), re.I)]
    raw_rules = []
    for c in raw_calls:
        if (c.get("raw_content") or "").strip():
            try:
                raw_rules = parse_json_response(c["raw_content"]).get("rules") or []
            except Exception:
                pass
    accepted_ids = {r.rule_id for r in result.rules}
    pre_rejected = [f"`{r.get('rule_id')}` (rule_type `{r.get('rule_type')}`, p{(r.get('source') or {}).get('page')})"
                    for r in raw_rules if isinstance(r, dict) and r.get("rule_id") not in accepted_ids
                    and re.search(r"pre.?existing|ambig", json.dumps(r), re.I)]
    pre_raw = bool(re.search(r"pre.?existing", raw_text, re.I))
    pre_amb = [a for a in getattr(result, "ambiguities", [])
               if re.search(r"pre.?existing|4\.1", f"{a.section} {a.description} {a.issue} {a.source.quote}", re.I)]
    amb_checks = getattr(result, "ambiguity_checks", {})
    ambiguity = ("yes, as a compiled ambiguity: " + ", ".join(
                    f"`{a.ambiguity_id}` ({a.section}, p{a.source.page}, citation "
                    f"{'verified' if amb_checks.get(a.ambiguity_id) and amb_checks[a.ambiguity_id].verified else 'NOT verified'})"
                    for a in pre_amb)) if pre_amb else \
                ("yes, as an accepted rule: " + ", ".join(r.rule_id for r in pre_rule)) if pre_rule else \
                ("proposed but rejected by validation: " + "; ".join(pre_rejected)) if pre_rejected else \
                ("mentioned in the raw reply but not emitted as a rule" if pre_raw else "**no** (not mentioned anywhere in the reply)")

    rf = next((r for r in result.rules if r.rule_type == "room_rent_cap"), None)
    formula = "room_rent_cap rule not found"
    if rf:
        p = json.dumps(rf.parameters).lower() + " " + rf.description.lower()
        has_ratio = "5000 / actual" in p or "ratio" in p or "proportion" in p or "formula" in p
        formula = (f"compiled `formula_basis` = {fmt(rf.parameters.get('formula_basis', '<missing>'))}, "
                   f"`affected_categories` = {fmt(rf.parameters.get('affected_categories', '<missing>'))}; "
                   f"proportional deduction mentioned anywhere in the rule: {'yes' if has_ratio else 'no'}")

    stats.update(
        found=sum(1 for row in summary_rows if "| yes (" in row), gold_total=len(gold.rules),
        cites_ok=sum(1 for row in summary_rows if "| yes (cited" in row),
        extras=len(extras), rejected=len(result.rejected), ambiguity=ambiguity, formula=formula,
    )

    lines += ["## Per-rule summary", "",
              "| Gold rule | Found | rule_type correct | Parameters correct | Quote verbatim on right page |",
              "|---|---|---|---|---|", *summary_rows, "",
              "## Room-rent proportional formula", "", formula, "",
              "## Pre-existing-conditions ambiguity (Section 4.1)", "", ambiguity, "",
              "## Extra rules (not in gold)", ""]
    if extras:
        lines += [f"- `{r.rule_id}` ({r.rule_type}), p{r.source.page}, citation "
                  f"{'verified' if result.citation_checks[r.rule_id].verified else 'NOT verified'}: {r.description}"
                  for r in extras]
    else:
        lines.append("None accepted.")
    if result.rejected:
        lines += ["", "Rejected by validation (proposed by the model, not accepted):", ""]
        lines += [f"- {m}" for m in result.rejected]
    other_ambs = [a for a in getattr(result, "ambiguities", []) if a not in pre_amb]
    lines += ["", "## Other ambiguities reported by the compiler", ""]
    lines += [f"- `{a.ambiguity_id}` ({a.section}, p{a.source.page}): {a.issue}" for a in other_ambs] or ["None."]
    lines += ["", "## Exact diffs (gold vs compiled)", ""]
    lines += diff_blocks or ["No parameter differences."]
    lines += ["", "## Totals", "",
              f"- Rules found: {stats['found']}/{stats['gold_total']}",
              f"- Parameters correct: {stats['params_ok']}/{stats['params_total']}",
              f"- Citations verbatim on the cited page: {stats['cites_ok']}/{stats['found']}",
              f"- Extra rules accepted: {stats['extras']}; rejected proposals: {stats['rejected']}", ""]
    return "\n".join(lines), stats


# --------------------------------------------------------------------------- step 3
def run_claims(client, compiled):
    non_payable = []
    if compiled is not None:
        npr = next((r for r in compiled.rules if r.rule_type == "non_payable_items"), None)
        if npr:
            non_payable = npr.parameters.get("excluded_items") or npr.parameters.get("items") or []

    md = ["# Claim extractor vs ground truth (live baseline)", "",
          f"- Fast model: `{get_model('fast')}`",
          f"- Tesseract available: {is_tesseract_available()}",
          f"- Non-payable list passed to the extractor: from the **compiled** rulebook ({len(non_payable)} items)",
          f"- Fact names the extractor is allowed to emit: {', '.join(ALLOWED_FACTS)}", ""]
    totals = {}
    for packet, kind in PACKETS.items():
        folder = ROOT / "data" / "packets" / packet
        gt = json.loads((folder / "ground_truth_facts.json").read_text(encoding="utf-8"))
        md += [f"## {packet}: {kind}", ""]

        docs, ocr_rows = [], []
        for pdf in sorted(folder.glob("*.pdf")):
            doc = load_claim_document(pdf)
            docs.append(doc)
            for p in doc.pages:
                ocr_rows.append(f"| {doc.document_id} | {p.page_number} | {p.source_type} | "
                                f"{'yes' if p.needs_ocr else 'no'} | {len(p.text.strip())} |")
        md += ["| Document | Page | Text source | Still needs OCR | Characters |", "|---|---|---|---|---|",
               *ocr_rows, ""]

        client.label = f"claim_extractor:{packet}"
        error, result = None, None
        try:
            result = extract_claim_facts(docs, client=client, non_payable_items=non_payable or None)
        except Exception as exc:
            error = "".join(traceback.format_exception_only(type(exc), exc)).strip()

        out = {"packet": packet, "error": error,
               "documents": {d.document_id: [{"page": p.page_number, "source_type": p.source_type,
                                               "needs_ocr": p.needs_ocr, "text": p.text} for p in d.pages]
                             for d in docs}}
        if result is not None:
            out.update(model=result.model,
                       facts=[f.model_dump(mode="json") for f in result.facts],
                       citation_checks={k: v.model_dump(mode="json") for k, v in result.citation_checks.items()},
                       needs_review=result.needs_review, rejected=result.rejected)
        (folder / f"extracted_facts_{RUN_TAG}.json").write_text(json.dumps(out, indent=2, ensure_ascii=False),
                                                          encoding="utf-8")

        if result is None:
            md += [f"**Extraction failed:** `{error}`", ""]
            totals[packet] = "failed"
            continue

        rows, matched = [], 0
        for g in gt:
            f = result.fact(g["name"])
            if f is None:
                allowed = g["name"] in ALLOWED_FACTS
                rows.append(f"| {g['name']} | **missing** | - | - | "
                            f"{'' if allowed else 'fact name not in extractor vocabulary'} |")
                continue
            chk = result.citation_checks[g["name"]]
            gv, cv = g["value"], f.value
            if isinstance(gv, dict) and isinstance(cv, dict):
                bad = [(k, gv[k], cv.get(k, "<missing>")) for k in gv if norm(cv.get(k)) != norm(gv[k])]
            else:
                bad = [] if norm(cv) == norm(gv) else [("value", gv, cv)]
            if not bad:
                matched += 1
            diff = "; ".join(f"{k}: gold {fmt(a)} vs got {fmt(b)}" for k, a, b in bad) or "all values match"
            rows.append(f"| {g['name']} | {'match' if not bad else 'MISMATCH'} | "
                        f"{'yes' if chk.verified else 'NO'} ({chk.reason}) | {diff} | |")
        extra = [f.name for f in result.facts if f.name not in {g['name'] for g in gt}]
        md += ["| Ground-truth fact | Result | Citation verified | Diff | Note |", "|---|---|---|---|---|",
               *rows, "",
               f"Matched {matched}/{len(gt)}. Extra facts not in ground truth: {', '.join(extra) or 'none'}. "
               f"Rejected by validation: {'; '.join(result.rejected) or 'none'}.", ""]
        totals[packet] = f"{matched}/{len(gt)}"
    return "\n".join(md), totals


# --------------------------------------------------------------------------- step 4
def write_run_info(client, commit, dirty, started, stats, totals, compiler_error):
    calls = client.calls
    (OUT / "calls.json").write_text(json.dumps(
        [{k: v for k, v in c.items() if k not in ("raw_content", "reasoning_content")} for c in calls],
        indent=2), encoding="utf-8")
    rows = [f"| {i} | {c['label']} | `{c.get('model')}` | {c.get('max_tokens')} | {c.get('prompt_tokens')} | "
            f"{c.get('completion_tokens')} | {c.get('latency_s')} | {c.get('finish_reason') or c.get('error')} |"
            for i, c in enumerate(calls, 1)]
    tin = sum(c.get("prompt_tokens") or 0 for c in calls)
    tout = sum(c.get("completion_tokens") or 0 for c in calls)
    retries = [c for c in calls if c.get("finish_reason") == "length" or c.get("error")
               or not (c.get("raw_content") or "").strip()]
    lines = [
        "# Run info (live baseline)", "",
        f"- Date: {started}",
        f"- Git commit: `{commit}`{' (working tree had uncommitted changes)' if dirty else ''}",
        f"- Reasoning model (compiler): `{get_model('reasoning')}`",
        f"- Fast model (claim extractor): `{get_model('fast')}`",
        f"- Tesseract available: {is_tesseract_available()}", "",
        "## Calls", "",
        "| # | Step | Model | max_tokens | Input tokens | Output tokens | Latency (s) | Finish / error |",
        "|---|---|---|---|---|---|---|---|", *rows, "",
        f"**Total: {tin} input tokens, {tout} output tokens across {len(calls)} calls.**", "",
        "## Retries and errors", "",
    ]
    if retries:
        lines += [f"- Call to `{c['label']}` (max_tokens={c.get('max_tokens')}): "
                  f"finish_reason={c.get('finish_reason')!r}, empty={not (c.get('raw_content') or '').strip()}, "
                  f"error={c.get('error')}" for c in retries]
    else:
        lines.append("None.")
    if compiler_error:
        lines.append(f"- Compiler raised: `{compiler_error}`")
    lines += ["", "## Results at a glance", ""]
    if stats:
        lines += [f"- Compiler: {stats['found']}/{stats['gold_total']} rules found, "
                  f"{stats['params_ok']}/{stats['params_total']} parameters correct, "
                  f"{stats['cites_ok']}/{stats['found']} citations verified",
                  f"- Ambiguity: {stats['ambiguity']}"]
    lines += [f"- Claims {p}: {t}" for p, t in totals.items()]
    lines += ["", "## Surprises / notes", "", "_Written by hand after reviewing the run, see below._", ""]
    (OUT / "run_info.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    started = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    commit, dirty = git("rev-parse", "HEAD"), bool(git("status", "--porcelain", "--", "src", "data", "benchmark"))

    client = RecordingClient(get_client())
    policy = load_document(ROOT / "data" / "synthetic_policy.pdf")
    gold = Rulebook.model_validate(json.loads((ROOT / "data" / "gold_rulebook.json").read_text(encoding="utf-8")))

    print("Step 1: compiling policy ...")
    result, error, raw_calls = run_compiler(client, policy, gold)
    md, stats = compare_compiler(result, error, raw_calls, policy, gold)
    (OUT / "compiler_vs_gold.md").write_text(md, encoding="utf-8")
    print("  ", "FAILED: " + error if error else
          f"{stats['found']}/{stats['gold_total']} rules, {stats['params_ok']}/{stats['params_total']} params, "
          f"{stats['cites_ok']} citations verified")

    print("Step 3: extracting claim facts ...")
    claims_md, totals = run_claims(client, result)
    (OUT / "claim_extraction_results.md").write_text(claims_md, encoding="utf-8")
    for p, t in totals.items():
        print(f"   {p}: {t}")

    write_run_info(client, commit, dirty, started, stats, totals, error)
    print(f"\nDone. Files in {OUT}")


if __name__ == "__main__":
    main()

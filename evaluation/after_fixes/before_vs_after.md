# Live results before and after the fixes

Same script (`scripts/run_baseline.py`), same models, same policy and packets, no changes to the gold data.

- Before: `evaluation/baseline/` (commit `b6f503b`, 2026-10-25)
- After: `evaluation/after_fixes/` (branch `fix/live-mode-gaps`, 2026-10-27)
- Models: `nvidia/nemotron-3-super-120b-a12b` (compiler), `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` (claims)

## Policy compiler vs gold rulebook

| Measure | Before | After |
|---|---|---|
| Rules found | 5/5 | 5/5 |
| Parameters correct | 14/18 | **17/18** |
| Quotes verbatim on the cited page | 5/5 | 5/5 |
| Room-rent ratio (`formula_basis`, `affected_categories`) | missing | **`5000 / actual_daily_rate`, professional fees and OT charges** |
| Pre-existing-conditions ambiguity (Section 4.1) | not mentioned | **found, quote verified (p4)** |
| Invented rules or quotes | none | none |

Remaining difference: `excess_payable_by` is `insured_person` (gold: `insured`), a wording difference.

## Claim extractor vs ground truth

| Packet | Before | After | Note |
|---|---|---|---|
| packet_05 (digital) | 1/1 | 1/1 | |
| packet_11 (scanned, proportional case) | 1/3 | **3/3** | professional fees and OT charges now extracted; OCR worked in both runs |
| packet_02 (missing discharge summary) | 1/1 | 1/1 | correctly lists only claim form and hospital bill |

## Cost and speed

| | Before | After |
|---|---|---|
| Input tokens | 5,059 | 5,752 |
| Output tokens | 15,590 | 17,163 |
| Estimated cost (team price table) | ~$0.019 | ~$0.021 |
| Compiler latency | 20.1 s | 13.0 s |
| Slowest claim call | 107.2 s (packet_11) | 48.3 s (packet_05) |

The longer prompts add about 700 input tokens. Output length varies run to run, mostly reasoning.

## What changed

1. Compiler asks for `cap`, `formula_basis`, `affected_categories` and the whole room-rent clause, and returns an
   `ambiguities` list that is citation-checked and passed to the rule engine.
2. Extractor can emit `professional_fees_billing`, `operation_theatre_billing` and `patient_medical_history`;
   rejects non-cataract surgery in `surgical_expenses`; free-text causes become `reported_cause`.
3. Live pipeline passes the compiled non-payable list to the extractor.
4. Live mode applies compiled rules only after a person approves them in the app (team decision, option B).

## Found while testing (not fixed here)

- **Ambiguity relevance depended on the word "policy" (rule engine), fixed on this branch.** An earlier note here
  said packet_05 was flagged; that was wrong (it only happened when the ambiguity ID was requested directly). The
  real problem: the engine linked a claim fact to the Section 4.1 ambiguity by any shared word, and packet_12 only
  matched through the generic word "policy". A routine claim mentioning "policy" was flagged, and a real
  pre-existing case worded without that word was missed. The engine now matches on subject words only, ignores
  empty fields, and reads field names such as `pre_existing_history`. Findings for all 20 packets and 20 scenarios
  are unchanged; tests cover both failure cases and the live compiler's wording.
- **Proportional deduction applied unconditionally (rule engine).** The engine applies the 5000/rate ratio
  whenever room rent exceeds the cap, without checking `formula_basis`. Correct for this policy, wrong for a
  policy without that clause.

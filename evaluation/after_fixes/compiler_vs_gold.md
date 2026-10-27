# Policy compiler vs gold rulebook (live baseline)

- Model: `nvidia/nemotron-3-super-120b-a12b`
- Rules returned: 5 accepted, 0 rejected by validation
- Matching: each gold rule is matched to the first compiled rule with the same `rule_type`.
- Values are compared after normalisation only (case, spaces/underscores, int vs float, list order).
  Wording differences such as `illness` vs `illnesses` are counted as **wrong** and shown below.

## Per-rule summary

| Gold rule | Found | rule_type correct | Parameters correct | Quote verbatim on right page |
|---|---|---|---|---|
| RULE_REQ_DOCS | yes (`RULE_required_docs`) | yes | 2/2 | yes (cited p1, gold p1, verified) |
| RULE_WAITING_PERIOD | yes (`RULE_waiting_period`) | yes | 3/3 | yes (cited p2, gold p2, verified) |
| RULE_ROOM_RENT_CAP | yes (`RULE_room_rent_cap`) | yes | 6/7 | yes (cited p2, gold p2, verified) |
| RULE_CATARACT_SUBLIMIT | yes (`RULE_sub_limit_cataract`) | yes | 5/5 | yes (cited p3, gold p3, verified) |
| RULE_NON_PAYABLE_ITEMS | yes (`RULE_non_payable_items`) | yes | 1/1 | yes (cited p3, gold p3, verified) |

## Room-rent proportional formula

compiled `formula_basis` = `"5000 / actual_daily_rate"`, `affected_categories` = `["professional_fees", "operation_theatre_charges"]`; proportional deduction mentioned anywhere in the rule: yes

## Pre-existing-conditions ambiguity (Section 4.1)

yes, as a compiled ambiguity: `AMB_pre_existing_definition` (Section 4.1, p4, citation verified)

## Extra rules (not in gold)

None accepted.

## Other ambiguities reported by the compiler

None.

## Exact diffs (gold vs compiled)

### RULE_ROOM_RENT_CAP (room_rent_cap)

| Parameter | Gold | Compiled |
|---|---|---|
| excess_payable_by | `"insured"` | `"insured_person"` |


## Totals

- Rules found: 5/5
- Parameters correct: 17/18
- Citations verbatim on the cited page: 5/5
- Extra rules accepted: 0; rejected proposals: 0

# Run info (live baseline)

- Date: 2026-10-25 11:16:26 IST
- Git commit: `b6f503b411f77dc8a91c825156aadb3ae9091f33`
- Reasoning model (compiler): `nvidia/nemotron-3-super-120b-a12b`
- Fast model (claim extractor): `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`
- Tesseract available: True

## Calls

| # | Step | Model | max_tokens | Input tokens | Output tokens | Latency (s) | Finish / error |
|---|---|---|---|---|---|---|---|
| 1 | policy_compiler | `nvidia/nemotron-3-super-120b-a12b` | 8192 | 2254 | 3777 | 20.13 | stop |
| 2 | claim_extractor:packet_05 | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | 8192 | 928 | 3174 | 30.77 | stop |
| 3 | claim_extractor:packet_11 | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | 8192 | 928 | 7693 | 107.19 | stop |
| 4 | claim_extractor:packet_02 | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | 8192 | 949 | 946 | 10.84 | stop |

**Total: 5059 input tokens, 15590 output tokens across 4 calls.**

## Retries and errors

None.

## Results at a glance

- Compiler: 5/5 rules found, 14/18 parameters correct, 5/5 citations verified
- Ambiguity: **no** (not mentioned anywhere in the reply)
- Claims packet_05: 1/1
- Claims packet_11: 1/3
- Claims packet_02: 1/1

## Cost estimate

Using the prices in `config/model_pricing.json` (team-entered, not checked against the live Nebius price list):

| Model | Input tokens | Output tokens | Est. cost (USD) |
|---|---|---|---|
| nemotron-3-super-120b-a12b (compiler) | 2,254 | 3,777 | 0.0109 |
| NVIDIA-Nemotron-3-Nano-30B-A3B (3 claim packets) | 2,805 | 11,813 | 0.0076 |
| **Total** | 5,059 | 15,590 | **~0.019** |

The whole 4-page policy is about 2.3k input tokens, far inside the 256k context window, so the full policy fits
in one call comfortably. Output tokens dominate the cost, and most of them are reasoning, not JSON.

## Surprises / notes

1. **Model used was Super, not Ultra.** `NEBIUS_MODEL_REASONING` in `.env` is `nemotron-3-super-120b-a12b`.
   This baseline is a Super baseline; Ultra has not been run.
2. **The proportional room-rent rule was dropped, not misread.** The compiled `room_rent_cap` quote stops after
   the INR 5000/day sentence and leaves out the very next sentence ("payable only in the ratio 5000 / actual
   daily rate"). The model's reasoning never mentions the ratio. Likely cause: the compiler prompt's
   `PARAMETER_SCHEMA` has no `cap`, `formula_basis` or `affected_categories` keys, so the model filled only the
   keys it was asked for. The gold rulebook gained these keys after the prompt was written.
3. **The pre-existing-conditions ambiguity (Section 4.1) was not detected at all**: zero mentions in the reply or
   the reasoning. The prompt only asks for the 5 rule types, and `RuleType` has no ambiguity type, so the
   compiler currently has no way to report it even if the model noticed it.
4. **No invented rules or quotes.** 5 rules, 5/5 quotes verbatim on the correct page, no extra or rejected rules.
5. **Packet 11 (scanned): OCR worked.** Tesseract read the rasterised bill cleanly (411 characters, every line
   readable) and the room-rent fact matched with a verified citation. The 1/3 score is a vocabulary gap, not OCR:
   the extractor is only allowed 5 fact names, and `professional_fees_billing` / `operation_theatre_billing` are
   not among them.
6. **Packet 11: the model forced the missing facts into the cataract schema.** It emitted `surgical_expenses` for
   a knee arthroplasty with `surgeon_fee` 12000 and `ot_charges` 15000; the anesthetist (5000) and physician
   (3000) fees were lost. It also put the procedure name into `hospitalization_details.cause`, which should be
   `illness` or `accidental_injury`. The rule engine should ignore a `surgical_expenses` fact whose procedure is
   not cataract, or this could trigger the wrong sub-limit.
7. **Packet 02 (missing document) handled correctly.** The discharge summary file is an "awaited" notice; the
   model listed only `claim_form` and `hospital_bill` as submitted, matching ground truth.
8. **Packet 05: policy inception date was not found** in the claim documents, so `days_elapsed` is null. The
   value has to come from the policy schedule, not the claim.
9. **Latency.** Nano took 107 s on packet 11 (7,693 output tokens of mostly reasoning) versus 11 s on packet 02.
   No call hit the token limit, so the empty-reply retry did not trigger in this run.
10. **Earlier token numbers were placeholders.** `benchmark/compiler_eval.py` uses hard-coded token counts
    (2200/750). These are the first real measured numbers.
11. **Dependency note.** `pip install -r requirements.txt` failed on Python 3.15: the new pin `pymupdf==2.0`
    pulls in `onnxruntime`, which has no Python 3.15 build. The run used the already installed PyMuPDF. Teammates
    on Python 3.11/3.12 are probably unaffected.

## Reproduce

```bash
python scripts/run_baseline.py   # 4 live calls, about 3 minutes
```

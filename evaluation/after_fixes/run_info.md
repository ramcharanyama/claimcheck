# Run info (live baseline)

- Date: 2026-10-27 21:21:40 IST
- Git commit: `9b43d990e2d46bfc0dabcc8acf496fed103d33ed`
- Reasoning model (compiler): `nvidia/nemotron-3-super-120b-a12b`
- Fast model (claim extractor): `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`
- Tesseract available: True

## Calls

| # | Step | Model | max_tokens | Input tokens | Output tokens | Latency (s) | Finish / error |
|---|---|---|---|---|---|---|---|
| 1 | policy_compiler | `nvidia/nemotron-3-super-120b-a12b` | 8192 | 2503 | 3312 | 12.97 | stop |
| 2 | claim_extractor:packet_05 | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | 8192 | 1076 | 7488 | 48.26 | stop |
| 3 | claim_extractor:packet_11 | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | 8192 | 1076 | 4626 | 33.16 | stop |
| 4 | claim_extractor:packet_02 | `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | 8192 | 1097 | 1737 | 8.05 | stop |

**Total: 5752 input tokens, 17163 output tokens across 4 calls.**

## Retries and errors

None.

## Results at a glance

- Compiler: 5/5 rules found, 17/18 parameters correct, 5/5 citations verified
- Ambiguity: yes, as a compiled ambiguity: `AMB_pre_existing_definition` (Section 4.1, p4, citation verified)
- Claims packet_05: 1/1
- Claims packet_11: 3/3
- Claims packet_02: 1/1

## Surprises / notes

_Written by hand after reviewing the run, see below._

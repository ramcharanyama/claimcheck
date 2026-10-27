# Claim extractor vs ground truth (live baseline)

- Fast model: `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B`
- Tesseract available: True
- Non-payable list passed to the extractor: from the **compiled** rulebook (7 items)
- Fact names the extractor is allowed to emit: submitted_documents, hospitalization_details, room_rent_billing, surgical_expenses, non_payable_items_breakdown

## packet_05: digital (room rent within limit)

| Document | Page | Text source | Still needs OCR | Characters |
|---|---|---|---|---|
| claim_form.pdf | 1 | pdf_text | no | 327 |
| discharge_summary.pdf | 1 | pdf_text | no | 332 |
| hospital_bill.pdf | 1 | pdf_text | no | 414 |

| Ground-truth fact | Result | Citation verified | Diff | Note |
|---|---|---|---|---|
| room_rent_billing | match | yes (verified) | all values match | |

Matched 1/1. Extra facts not in ground truth: submitted_documents, hospitalization_details. Rejected by validation: none.

## packet_11: scanned hospital bill (proportional room-rent case)

| Document | Page | Text source | Still needs OCR | Characters |
|---|---|---|---|---|
| claim_form.pdf | 1 | pdf_text | no | 314 |
| discharge_summary.pdf | 1 | pdf_text | no | 328 |
| hospital_bill.pdf | 1 | ocr | no | 411 |

| Ground-truth fact | Result | Citation verified | Diff | Note |
|---|---|---|---|---|
| room_rent_billing | match | yes (verified) | all values match | |
| professional_fees_billing | **missing** | - | - | fact name not in extractor vocabulary |
| operation_theatre_billing | **missing** | - | - | fact name not in extractor vocabulary |

Matched 1/3. Extra facts not in ground truth: hospitalization_details, surgical_expenses. Rejected by validation: none.

## packet_02: missing document (discharge summary replaced by an 'awaited' notice)

| Document | Page | Text source | Still needs OCR | Characters |
|---|---|---|---|---|
| claim_form.pdf | 1 | pdf_text | no | 523 |
| discharge_summary.pdf | 1 | pdf_text | no | 426 |
| hospital_bill.pdf | 1 | pdf_text | no | 426 |

| Ground-truth fact | Result | Citation verified | Diff | Note |
|---|---|---|---|---|
| submitted_documents | match | yes (verified) | all values match | |

Matched 1/1. Extra facts not in ground truth: none. Rejected by validation: none.

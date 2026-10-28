"""Generate the ClaimCheck v3 demo cases in data/demo_cases/.

Each case is a realistic, fictional claim folder with any mix of documents (claim form,
bills, prescriptions, lab reports, discharge summary, pre/post-hospitalisation bills,
ambulance bill, pre-authorisation form, cost estimate), plus:

  ground_truth_facts.json  - the facts a perfect extractor should return (quotes are verbatim)
  expected.json            - expected rule results, paperwork checks, verdict and payout

Kept separate from data/packets/ so the 20-packet benchmark is unchanged.
Usage:  python benchmark/generate_demo_cases.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from benchmark.generate_packets import _render_digital_pdf_bytes, _render_rasterised_pdf_bytes  # noqa: E402
from benchmark.ground_truth import PacketDocumentContent, PacketSpec  # noqa: E402

DEMO_DIR = ROOT_DIR / "data" / "demo_cases"
POLICY = "POL-AURA-2026-001"
INCEPTION = "2026-01-01"


class Case:
    def __init__(self, case_id: str, label: str, claim_type: str, story: str, patient: str, claim_ref: str):
        self.case_id, self.label, self.claim_type, self.story = case_id, label, claim_type, story
        self.patient, self.claim_ref = patient, claim_ref
        self.docs: Dict[str, dict] = {}
        self.facts: List[dict] = []
        self.details: List[dict] = []
        self.expected_rules: Dict[str, list] = {}
        self.expected_checks: Dict[str, str] = {}
        self.expected_verdict: Optional[str] = None
        self.expected_payable: Optional[float] = None

    def doc(self, filename, title, doc_type, paragraphs, *, scanned=False, patient=None, policy=POLICY,
            detail=None):
        id_line = f"Patient: {patient or self.patient} | Policy No: {policy} | Claim Ref: {self.claim_ref}"
        self.docs[filename] = {"title": title, "doc_type": doc_type, "paragraphs": [id_line] + paragraphs,
                               "scanned": scanned}
        entry = {"document": filename, "page": 1, "quote": id_line, "patient_name": patient or self.patient,
                 "policy_number": policy}
        entry.update(detail or {})
        self.details.append(entry)
        return id_line

    def fact(self, name, value, document, quote):
        assert quote in self.docs[document]["paragraphs"], f"{self.case_id}: quote not in {document}: {quote}"
        self.facts.append({"name": name, "value": value,
                           "source": {"document": document, "page": 1, "quote": quote}})

    def finish_details(self, document):
        self.facts.append({"name": "document_details", "value": self.details,
                           "source": {"document": document, "page": 1,
                                      "quote": self.docs[document]["paragraphs"][0]}})


def money(x):
    return f"INR {x:,.2f}"


# --------------------------------------------------------------------------- cases
def case_21() -> Case:
    c = Case("case_21_reimbursement_all_clear", "Reimbursement - everything in order", "reimbursement",
             "A complete kidney-infection claim with pre- and post-hospitalisation bills inside the covered windows.",
             "Anita Sharma", "CLM-2026-0421")
    adm, dis = "2026-04-06", "2026-04-09"
    claimed = 38200.0
    q_claim = f"Total amount claimed: {money(claimed)}."
    q_decl = "Existing medical conditions declared by the insured: None."
    q_docs = "Documents enclosed: Claim Form, Hospital Bill, Discharge Summary."
    c.doc("claim_form.pdf", "HEALTH INSURANCE REIMBURSEMENT CLAIM FORM", "Claim Form",
          [q_docs, f"Date of admission: {adm}. Date of discharge: {dis}.", q_decl, q_claim],
          detail={"document_type": "claim_form", "admission_date": adm, "discharge_date": dis})
    q_room = "Room rent (Twin sharing): 3 days @ INR 4000.00/day = INR 12000.00."
    items = [("Room rent (Twin sharing)", 12000), ("Doctor consultation fees", 6000), ("Pharmacy and medicines", 9200),
             ("Laboratory investigations", 4500), ("Nursing charges", 3000)]
    c.doc("hospital_bill.pdf", "CITY CARE HOSPITAL - FINAL BILL", "Hospital Bill",
          [f"Admitted: {adm} | Discharged: {dis}", q_room, "Doctor consultation fees: INR 6000.00.",
           "Pharmacy and medicines: INR 9200.00.", "Laboratory investigations: INR 4500.00.",
           "Nursing charges: INR 3000.00.", "Total bill amount: INR 34700.00."],
          detail={"document_type": "hospital_bill", "admission_date": adm, "discharge_date": dis,
                  "stated_total": 34700, "line_items": [{"item": i, "amount": a} for i, a in items]})
    q_dx = "Diagnosis: Acute pyelonephritis (kidney infection). Cause: illness."
    c.doc("discharge_summary.pdf", "DISCHARGE SUMMARY", "Discharge Summary",
          [f"Date of admission: {adm}. Date of discharge: {dis}.", q_dx,
           "Past medical history: No known chronic illness.", "Condition at discharge: Stable."],
          detail={"document_type": "discharge_summary", "admission_date": adm, "discharge_date": dis})
    q_pre = "28-Mar-2026: Urine culture and consultation for fever and flank pain: INR 1500.00."
    c.doc("pre_hospitalisation_bill.pdf", "CITY CARE CLINIC - OUTPATIENT BILL", "Pre-Hospitalisation Bill",
          [q_pre, "Total: INR 1500.00."],
          detail={"document_type": "pre_hospitalization_bill", "stated_total": 1500,
                  "line_items": [{"item": "Urine culture and consultation", "amount": 1500}]})
    q_post1 = "20-Apr-2026: Follow-up consultation: INR 800.00."
    q_post2 = "20-Apr-2026: Antibiotics course completion: INR 1200.00."
    c.doc("post_hospitalisation_bill.pdf", "CITY CARE CLINIC - FOLLOW-UP BILL", "Post-Hospitalisation Bill",
          [q_post1, q_post2, "Total: INR 2000.00."],
          detail={"document_type": "post_hospitalization_bill", "stated_total": 2000,
                  "line_items": [{"item": "Follow-up consultation", "amount": 800},
                                 {"item": "Antibiotics course completion", "amount": 1200}]})
    c.doc("prescription.pdf", "PRESCRIPTION", "Prescription",
          ["Rx: Tab Ciprofloxacin 500 mg twice daily for 7 days. Tab Paracetamol 650 mg when required."],
          detail={"document_type": "prescription"})
    c.fact("submitted_documents", ["claim_form", "hospital_bill", "discharge_summary"], "claim_form.pdf", q_docs)
    c.fact("hospitalization_details", {"policy_inception_date": INCEPTION, "admission_date": adm, "discharge_date": dis,
                                       "days_elapsed": 95, "cause": "illness", "diagnosis": "acute pyelonephritis"},
           "discharge_summary.pdf", q_dx)
    c.fact("room_rent_billing", {"room_type": "twin_sharing", "daily_rate": 4000.0, "days": 3, "total_amount": 12000.0},
           "hospital_bill.pdf", q_room)
    c.fact("pre_hospitalization_expenses", [{"description": "Urine culture and consultation", "amount": 1500.0,
                                             "date": "2026-03-28"}], "pre_hospitalisation_bill.pdf", q_pre)
    c.fact("post_hospitalization_expenses", [{"description": "Follow-up consultation", "amount": 800.0, "date": "2026-04-20"},
                                             {"description": "Antibiotics course completion", "amount": 1200.0,
                                              "date": "2026-04-20"}], "post_hospitalisation_bill.pdf", q_post1)
    c.fact("claim_amount_summary", {"total_claimed": claimed, "previously_paid_this_year": 0.0}, "claim_form.pdf", q_claim)
    c.fact("declared_conditions", {"conditions": []}, "claim_form.pdf", q_decl)
    c.finish_details("claim_form.pdf")
    c.expected_rules = {"RULE_REQ_DOCS": ["satisfied", 0.0], "RULE_WAITING_PERIOD": ["satisfied", 0.0],
                        "RULE_ROOM_RENT_CAP": ["satisfied", 0.0], "RULE_PRE_HOSPITALIZATION": ["satisfied", 0.0],
                        "RULE_POST_HOSPITALIZATION": ["satisfied", 0.0], "RULE_SUM_INSURED": ["satisfied", 0.0],
                        "RULE_PRE_EXISTING_DISCLOSURE": ["satisfied", 0.0]}
    c.expected_checks = {k: "satisfied" for k in ("CHECK_PATIENT_NAME", "CHECK_POLICY_NUMBER", "CHECK_DATES",
                                                  "CHECK_BILL_ARITHMETIC", "CHECK_CLAIMED_VS_BILLED",
                                                  "CHECK_DUPLICATE_CHARGES")}
    c.expected_verdict, c.expected_payable = "approved", 38200.0
    return c


def case_22() -> Case:
    c = Case("case_22_document_mistakes", "Reimbursement - mistakes in the documents", "reimbursement",
             "Name and policy number typos, a bill that does not add up, and a claim for more than was billed.",
             "Rahul Verma", "CLM-2026-0522")
    adm, dis = "2026-05-11", "2026-05-14"
    q_claim = "Total amount claimed: INR 45,000.00."
    q_docs = "Documents enclosed: Claim Form, Hospital Bill, Discharge Summary."
    q_decl = "Existing medical conditions declared by the insured: None."
    c.doc("claim_form.pdf", "HEALTH INSURANCE REIMBURSEMENT CLAIM FORM", "Claim Form",
          [q_docs, f"Date of admission: {adm}. Date of discharge: {dis}.", q_decl, q_claim],
          policy="POL-AURA-2026-010",
          detail={"document_type": "claim_form", "admission_date": adm, "discharge_date": dis})
    q_room = "Room rent (Semi-private): 3 days @ INR 5000.00/day = INR 15000.00."
    items = [("Room rent (Semi-private)", 15000), ("Doctor consultation fees", 7500), ("Pharmacy and medicines", 11200),
             ("Laboratory investigations", 5600), ("Nursing charges", 2000)]
    c.doc("hospital_bill.pdf", "SUNRISE HOSPITAL - FINAL BILL", "Hospital Bill",
          [f"Admitted: {adm} | Discharged: {dis}", q_room, "Doctor consultation fees: INR 7500.00.",
           "Pharmacy and medicines: INR 11200.00.", "Laboratory investigations: INR 5600.00.",
           "Nursing charges: INR 2000.00.", "Total bill amount: INR 43300.00."],
          detail={"document_type": "hospital_bill", "admission_date": adm, "discharge_date": dis,
                  "stated_total": 43300, "line_items": [{"item": i, "amount": a} for i, a in items]})
    q_dx = "Diagnosis: Dengue fever without warning signs. Cause: illness."
    c.doc("discharge_summary.pdf", "DISCHARGE SUMMARY", "Discharge Summary",
          [f"Date of admission: {adm}. Date of discharge: {dis}.", q_dx, "Past medical history: Nil significant."],
          patient="Rohit Verma",
          detail={"document_type": "discharge_summary", "admission_date": adm, "discharge_date": dis})
    c.fact("submitted_documents", ["claim_form", "hospital_bill", "discharge_summary"], "claim_form.pdf", q_docs)
    c.fact("hospitalization_details", {"policy_inception_date": INCEPTION, "admission_date": adm, "discharge_date": dis,
                                       "days_elapsed": 130, "cause": "illness", "diagnosis": "dengue fever"},
           "discharge_summary.pdf", q_dx)
    c.fact("room_rent_billing", {"room_type": "semi_private", "daily_rate": 5000.0, "days": 3, "total_amount": 15000.0},
           "hospital_bill.pdf", q_room)
    c.fact("claim_amount_summary", {"total_claimed": 45000.0, "previously_paid_this_year": 0.0}, "claim_form.pdf", q_claim)
    c.fact("declared_conditions", {"conditions": []}, "claim_form.pdf", q_decl)
    c.finish_details("claim_form.pdf")
    c.expected_rules = {"RULE_REQ_DOCS": ["satisfied", 0.0], "RULE_WAITING_PERIOD": ["satisfied", 0.0],
                        "RULE_ROOM_RENT_CAP": ["satisfied", 0.0], "RULE_SUM_INSURED": ["satisfied", 0.0]}
    c.expected_checks = {"CHECK_PATIENT_NAME": "violated", "CHECK_POLICY_NUMBER": "violated",
                         "CHECK_DATES": "satisfied", "CHECK_BILL_ARITHMETIC": "violated",
                         "CHECK_CLAIMED_VS_BILLED": "violated", "CHECK_DUPLICATE_CHARGES": "satisfied"}
    c.expected_verdict, c.expected_payable = "fix_documents", 43300.0
    return c


def case_23() -> Case:
    c = Case("case_23_hidden_pre_existing", "Reimbursement - undeclared pre-existing disease", "reimbursement",
             "The claim form says 'no existing conditions', but the scanned prescription, lab report and discharge "
             "summary show long-standing diabetes and hypertension.", "Suresh Iyer", "CLM-2026-0623")
    adm, dis = "2026-06-02", "2026-06-04"
    q_claim = "Total amount claimed: INR 26,400.00."
    q_docs = "Documents enclosed: Claim Form, Hospital Bill, Discharge Summary."
    q_decl = "Existing medical conditions declared by the insured: None."
    c.doc("claim_form.pdf", "HEALTH INSURANCE REIMBURSEMENT CLAIM FORM", "Claim Form",
          [q_docs, f"Date of admission: {adm}. Date of discharge: {dis}.", q_decl, q_claim],
          detail={"document_type": "claim_form", "admission_date": adm, "discharge_date": dis})
    q_room = "Room rent (General ward): 2 days @ INR 4500.00/day = INR 9000.00."
    items = [("Room rent (General ward)", 9000), ("Doctor consultation fees", 4000), ("IV fluids and medicines", 8600),
             ("Laboratory investigations", 4800)]
    c.doc("hospital_bill.pdf", "LAKESIDE HOSPITAL - FINAL BILL", "Hospital Bill",
          [f"Admitted: {adm} | Discharged: {dis}", q_room, "Doctor consultation fees: INR 4000.00.",
           "IV fluids and medicines: INR 8600.00.", "Laboratory investigations: INR 4800.00.",
           "Total bill amount: INR 26400.00."],
          detail={"document_type": "hospital_bill", "admission_date": adm, "discharge_date": dis,
                  "stated_total": 26400, "line_items": [{"item": i, "amount": a} for i, a in items]})
    q_dx = "Diagnosis: Acute gastroenteritis with dehydration. Cause: illness."
    q_hist = "Past medical history: Known case of type 2 diabetes mellitus for 6 years and hypertension for 4 years."
    c.doc("discharge_summary.pdf", "DISCHARGE SUMMARY", "Discharge Summary",
          [f"Date of admission: {adm}. Date of discharge: {dis}.", q_dx, q_hist, "Condition at discharge: Stable."],
          detail={"document_type": "discharge_summary", "admission_date": adm, "discharge_date": dis})
    q_rx = "Continue: Tab Metformin 500 mg twice daily (long-term) and Tab Telmisartan 40 mg once daily (long-term)."
    c.doc("prescription.pdf", "PRESCRIPTION - DISCHARGE MEDICATION", "Prescription",
          ["Rx: Tab Ondansetron 4 mg when required for 3 days. ORS sachets after every loose stool.", q_rx],
          scanned=True, detail={"document_type": "prescription"})
    q_lab = "HbA1c: 8.1 percent (high), consistent with known type 2 diabetes."
    c.doc("lab_report.pdf", "LABORATORY REPORT", "Lab Report",
          ["Serum sodium: 132 mmol/L (low). Serum potassium: 3.3 mmol/L (low).", q_lab],
          detail={"document_type": "lab_report"})
    c.fact("submitted_documents", ["claim_form", "hospital_bill", "discharge_summary"], "claim_form.pdf", q_docs)
    c.fact("hospitalization_details", {"policy_inception_date": INCEPTION, "admission_date": adm, "discharge_date": dis,
                                       "days_elapsed": 152, "cause": "illness",
                                       "diagnosis": "acute gastroenteritis with dehydration"},
           "discharge_summary.pdf", q_dx)
    c.fact("room_rent_billing", {"room_type": "general_ward", "daily_rate": 4500.0, "days": 2, "total_amount": 9000.0},
           "hospital_bill.pdf", q_room)
    c.fact("claim_amount_summary", {"total_claimed": 26400.0, "previously_paid_this_year": 0.0}, "claim_form.pdf", q_claim)
    c.fact("declared_conditions", {"conditions": []}, "claim_form.pdf", q_decl)
    c.fact("medical_conditions_found", [
        {"condition": "Type 2 diabetes mellitus", "evidence": "known case for 6 years; Tab Metformin long-term; HbA1c 8.1",
         "inferred_from_medicine": False, "document": "discharge_summary.pdf"},
        {"condition": "Hypertension", "evidence": "known for 4 years; Tab Telmisartan long-term",
         "inferred_from_medicine": False, "document": "discharge_summary.pdf"}], "discharge_summary.pdf", q_hist)
    c.fact("patient_medical_history", {"diagnosis": "type_2_diabetes_mellitus_and_hypertension",
                                       "pre_existing_history": "Diabetes for 6 years and hypertension for 4 years",
                                       "hospitalization_cause": "acute gastroenteritis", "claimed_amount": 26400.0},
           "discharge_summary.pdf", q_hist)
    c.finish_details("claim_form.pdf")
    c.expected_rules = {"RULE_REQ_DOCS": ["satisfied", 0.0], "RULE_WAITING_PERIOD": ["satisfied", 0.0],
                        "RULE_ROOM_RENT_CAP": ["satisfied", 0.0], "RULE_PRE_EXISTING_DISCLOSURE": ["needs_review", None],
                        "AMB_PRE_EXISTING_CONDITIONS": ["needs_review", None]}
    c.expected_checks = {"CHECK_PATIENT_NAME": "satisfied", "CHECK_BILL_ARITHMETIC": "satisfied",
                         "CHECK_CLAIMED_VS_BILLED": "satisfied"}
    c.expected_verdict, c.expected_payable = "needs_review", 26400.0
    return c


def case_24() -> Case:
    c = Case("case_24_pre_post_ambulance", "Reimbursement - pre/post-hospitalisation and ambulance", "reimbursement",
             "Appendix surgery with a CT scan 46 days before admission, physiotherapy 76 days after discharge, and an "
             "ambulance bill above the limit.", "Meena Joshi", "CLM-2026-0724")
    adm, dis = "2026-07-15", "2026-07-18"
    q_claim = "Total amount claimed: INR 79,700.00."
    q_docs = "Documents enclosed: Claim Form, Hospital Bill, Discharge Summary."
    q_decl = "Existing medical conditions declared by the insured: None."
    c.doc("claim_form.pdf", "HEALTH INSURANCE REIMBURSEMENT CLAIM FORM", "Claim Form",
          [q_docs, f"Date of admission: {adm}. Date of discharge: {dis}.", q_decl, q_claim],
          detail={"document_type": "claim_form", "admission_date": adm, "discharge_date": dis})
    q_room = "Room rent (Twin sharing): 3 days @ INR 4000.00/day = INR 12000.00."
    items = [("Room rent (Twin sharing)", 12000), ("Surgeon fee", 25000), ("Operation theatre charges", 18000),
             ("Pharmacy and consumables", 7600)]
    c.doc("hospital_bill.pdf", "GREENVALLEY HOSPITAL - FINAL BILL", "Hospital Bill",
          [f"Admitted: {adm} | Discharged: {dis}", q_room, "Surgeon fee: INR 25000.00.",
           "Operation theatre charges: INR 18000.00.", "Pharmacy and consumables: INR 7600.00.",
           "Total bill amount: INR 62600.00."],
          detail={"document_type": "hospital_bill", "admission_date": adm, "discharge_date": dis,
                  "stated_total": 62600, "line_items": [{"item": i, "amount": a} for i, a in items]})
    q_dx = "Diagnosis: Acute appendicitis, laparoscopic appendectomy performed. Cause: illness."
    c.doc("discharge_summary.pdf", "DISCHARGE SUMMARY", "Discharge Summary",
          [f"Date of admission: {adm}. Date of discharge: {dis}.", q_dx, "Past medical history: Nil."],
          detail={"document_type": "discharge_summary", "admission_date": adm, "discharge_date": dis})
    q_pre1 = "30-May-2026: CT scan abdomen: INR 6500.00."
    q_pre2 = "10-Jul-2026: Ultrasound abdomen: INR 2200.00."
    c.doc("pre_hospitalisation_bill.pdf", "DIAGNOSTIC CENTRE - BILLS BEFORE ADMISSION", "Pre-Hospitalisation Bill",
          [q_pre1, q_pre2, "Total: INR 8700.00."],
          detail={"document_type": "pre_hospitalization_bill", "stated_total": 8700,
                  "line_items": [{"item": "CT scan abdomen", "amount": 6500}, {"item": "Ultrasound abdomen", "amount": 2200}]})
    q_post1 = "05-Aug-2026: Wound review consultation: INR 900.00."
    q_post2 = "02-Oct-2026: Physiotherapy sessions: INR 4000.00."
    c.doc("post_hospitalisation_bill.pdf", "GREENVALLEY CLINIC - BILLS AFTER DISCHARGE", "Post-Hospitalisation Bill",
          [q_post1, q_post2, "Total: INR 4900.00."],
          detail={"document_type": "post_hospitalization_bill", "stated_total": 4900,
                  "line_items": [{"item": "Wound review consultation", "amount": 900},
                                 {"item": "Physiotherapy sessions", "amount": 4000}]})
    q_amb = "Road ambulance from residence to Greenvalley Hospital on 15-Jul-2026: INR 3500.00."
    c.doc("ambulance_bill.pdf", "RAPID RESPONSE AMBULANCE - RECEIPT", "Ambulance Bill", [q_amb, "Total: INR 3500.00."],
          detail={"document_type": "ambulance_bill", "stated_total": 3500,
                  "line_items": [{"item": "Road ambulance", "amount": 3500}]})
    c.fact("submitted_documents", ["claim_form", "hospital_bill", "discharge_summary"], "claim_form.pdf", q_docs)
    c.fact("hospitalization_details", {"policy_inception_date": INCEPTION, "admission_date": adm, "discharge_date": dis,
                                       "days_elapsed": 195, "cause": "illness", "diagnosis": "acute appendicitis"},
           "discharge_summary.pdf", q_dx)
    c.fact("room_rent_billing", {"room_type": "twin_sharing", "daily_rate": 4000.0, "days": 3, "total_amount": 12000.0},
           "hospital_bill.pdf", q_room)
    c.fact("pre_hospitalization_expenses", [{"description": "CT scan abdomen", "amount": 6500.0, "date": "2026-05-30"},
                                            {"description": "Ultrasound abdomen", "amount": 2200.0, "date": "2026-07-10"}],
           "pre_hospitalisation_bill.pdf", q_pre1)
    c.fact("post_hospitalization_expenses", [{"description": "Wound review consultation", "amount": 900.0, "date": "2026-08-05"},
                                             {"description": "Physiotherapy sessions", "amount": 4000.0, "date": "2026-10-02"}],
           "post_hospitalisation_bill.pdf", q_post2)
    c.fact("ambulance_charges", {"amount": 3500.0}, "ambulance_bill.pdf", q_amb)
    c.fact("claim_amount_summary", {"total_claimed": 79700.0, "previously_paid_this_year": 0.0}, "claim_form.pdf", q_claim)
    c.fact("declared_conditions", {"conditions": []}, "claim_form.pdf", q_decl)
    c.finish_details("claim_form.pdf")
    c.expected_rules = {"RULE_REQ_DOCS": ["satisfied", 0.0], "RULE_WAITING_PERIOD": ["satisfied", 0.0],
                        "RULE_ROOM_RENT_CAP": ["satisfied", 0.0], "RULE_PRE_HOSPITALIZATION": ["violated", 6500.0],
                        "RULE_POST_HOSPITALIZATION": ["violated", 4000.0], "RULE_AMBULANCE_LIMIT": ["violated", 1500.0],
                        "RULE_SUM_INSURED": ["satisfied", 0.0]}
    c.expected_checks = {"CHECK_BILL_ARITHMETIC": "satisfied", "CHECK_CLAIMED_VS_BILLED": "satisfied"}
    c.expected_verdict, c.expected_payable = "approved", 67700.0
    return c


def case_25() -> Case:
    c = Case("case_25_accident_emergency", "Reimbursement - road accident in the first month", "reimbursement",
             "Admitted on day 11 of the policy after a road accident: the 30-day waiting period does not apply to "
             "accidents.", "Arjun Nair", "CLM-2026-0125")
    adm, dis = "2026-01-12", "2026-01-16"
    q_claim = "Total amount claimed: INR 41,800.00."
    q_docs = "Documents enclosed: Claim Form, Hospital Bill, Discharge Summary."
    q_decl = "Existing medical conditions declared by the insured: None."
    c.doc("claim_form.pdf", "HEALTH INSURANCE REIMBURSEMENT CLAIM FORM", "Claim Form",
          [q_docs, f"Date of admission: {adm}. Date of discharge: {dis}.", q_decl, q_claim],
          detail={"document_type": "claim_form", "admission_date": adm, "discharge_date": dis})
    q_room = "Room rent (General ward): 4 days @ INR 3000.00/day = INR 12000.00."
    items = [("Room rent (General ward)", 12000), ("Orthopaedic surgeon fee", 15000), ("Plaster and dressings", 4500),
             ("X-ray and CT imaging", 6500), ("Pharmacy", 2000)]
    c.doc("hospital_bill.pdf", "METRO TRAUMA CENTRE - FINAL BILL", "Hospital Bill",
          [f"Admitted: {adm} | Discharged: {dis}", q_room, "Orthopaedic surgeon fee: INR 15000.00.",
           "Plaster and dressings: INR 4500.00.", "X-ray and CT imaging: INR 6500.00.", "Pharmacy: INR 2000.00.",
           "Total bill amount: INR 40000.00."],
          detail={"document_type": "hospital_bill", "admission_date": adm, "discharge_date": dis,
                  "stated_total": 40000, "line_items": [{"item": i, "amount": a} for i, a in items]})
    q_dx = "Diagnosis: Fracture of right radius after a road traffic accident on 12-Jan-2026. Cause: accidental injury."
    c.doc("discharge_summary.pdf", "DISCHARGE SUMMARY", "Discharge Summary",
          [f"Date of admission: {adm}. Date of discharge: {dis}.", q_dx, "Past medical history: Nil."],
          detail={"document_type": "discharge_summary", "admission_date": adm, "discharge_date": dis})
    q_amb = "Road ambulance from accident site to Metro Trauma Centre on 12-Jan-2026: INR 1800.00."
    c.doc("ambulance_bill.pdf", "CITY AMBULANCE SERVICE - RECEIPT", "Ambulance Bill", [q_amb, "Total: INR 1800.00."],
          detail={"document_type": "ambulance_bill", "stated_total": 1800,
                  "line_items": [{"item": "Road ambulance", "amount": 1800}]})
    c.fact("submitted_documents", ["claim_form", "hospital_bill", "discharge_summary"], "claim_form.pdf", q_docs)
    c.fact("hospitalization_details", {"policy_inception_date": INCEPTION, "admission_date": adm, "discharge_date": dis,
                                       "days_elapsed": 11, "cause": "accidental_injury",
                                       "diagnosis": "fracture of right radius"}, "discharge_summary.pdf", q_dx)
    c.fact("room_rent_billing", {"room_type": "general_ward", "daily_rate": 3000.0, "days": 4, "total_amount": 12000.0},
           "hospital_bill.pdf", q_room)
    c.fact("ambulance_charges", {"amount": 1800.0}, "ambulance_bill.pdf", q_amb)
    c.fact("claim_amount_summary", {"total_claimed": 41800.0, "previously_paid_this_year": 0.0}, "claim_form.pdf", q_claim)
    c.fact("declared_conditions", {"conditions": []}, "claim_form.pdf", q_decl)
    c.finish_details("claim_form.pdf")
    c.expected_rules = {"RULE_REQ_DOCS": ["satisfied", 0.0], "RULE_WAITING_PERIOD": ["satisfied", 0.0],
                        "RULE_ROOM_RENT_CAP": ["satisfied", 0.0], "RULE_AMBULANCE_LIMIT": ["satisfied", 0.0]}
    c.expected_checks = {"CHECK_CLAIMED_VS_BILLED": "satisfied"}
    c.expected_verdict, c.expected_payable = "approved", 41800.0
    return c


def case_26() -> Case:
    c = Case("case_26_cashless_planned_deluxe", "Cashless - planned surgery in a deluxe room", "cashless",
             "Pre-authorisation for gallbladder surgery sent 5 days ahead. The deluxe room is above the cap, so doctor "
             "and theatre fees are only paid in proportion.", "Kavita Rao", "PA-2026-0826")
    q_type = "Type of admission: Planned. Planned admission: 25-Aug-2026 08:00."
    q_sent = "Request submitted to insurer on: 20-Aug-2026 11:00."
    q_encl = "Enclosed: Pre-Authorisation Form, Treating Doctor's Cost Estimate."
    q_decl = "Existing medical conditions declared by the insured: None."
    c.doc("preauthorisation_form.pdf", "CASHLESS PRE-AUTHORISATION REQUEST", "Pre-Authorisation Form",
          [q_type, q_sent, q_encl, q_decl, "Proposed treatment: Laparoscopic cholecystectomy for symptomatic gallstones."],
          detail={"document_type": "preauthorization_form", "admission_date": "2026-08-25"})
    q_room = "Deluxe room: 3 days @ INR 8000.00/day = INR 24000.00."
    q_fees = "Professional fees: Surgeon INR 45000, Anaesthetist INR 12000. Total INR 57000.00."
    q_ot = "Operation theatre charges: INR 30000.00."
    q_reg = "Registration fees: INR 1000.00."
    q_total = "Total estimated cost: INR 127000.00."
    items = [("Deluxe room", 24000), ("Surgeon fee", 45000), ("Anaesthetist fee", 12000),
             ("Operation theatre charges", 30000), ("Pharmacy and consumables", 15000), ("Registration fees", 1000)]
    c.doc("cost_estimate.pdf", "TREATING DOCTOR'S COST ESTIMATE", "Cost Estimate",
          [q_room, q_fees, q_ot, "Pharmacy and consumables: INR 15000.00.", q_reg, q_total],
          detail={"document_type": "cost_estimate", "stated_total": 127000,
                  "line_items": [{"item": i, "amount": a} for i, a in items]})
    c.fact("preauthorization_request", {"hospitalization_type": "planned",
                                        "documents_present": ["preauthorization_form", "cost_estimate"],
                                        "request_datetime": "2026-08-20T11:00", "admission_datetime": "2026-08-25T08:00"},
           "preauthorisation_form.pdf", q_sent)
    c.fact("hospitalization_details", {"policy_inception_date": INCEPTION, "admission_date": "2026-08-25",
                                       "days_elapsed": 236, "cause": "illness",
                                       "diagnosis": "symptomatic gallstones"}, "preauthorisation_form.pdf", q_type)
    c.fact("room_rent_billing", {"room_type": "deluxe_room", "daily_rate": 8000.0, "days": 3, "total_amount": 24000.0},
           "cost_estimate.pdf", q_room)
    c.fact("professional_fees_billing", {"surgeon_fee": 45000.0, "anesthetist_fee": 12000.0, "total_amount": 57000.0},
           "cost_estimate.pdf", q_fees)
    c.fact("operation_theatre_billing", {"ot_charges": 30000.0, "total_amount": 30000.0}, "cost_estimate.pdf", q_ot)
    c.fact("non_payable_items_breakdown", [{"item": "registration_fees", "amount": 1000.0}], "cost_estimate.pdf", q_reg)
    c.fact("claim_amount_summary", {"total_claimed": 127000.0, "previously_paid_this_year": 0.0}, "cost_estimate.pdf", q_total)
    c.fact("declared_conditions", {"conditions": []}, "preauthorisation_form.pdf", q_decl)
    c.finish_details("preauthorisation_form.pdf")
    # Room rule: 3 x (8000-5000) = 9000; ratio 5000/8000 -> 0.375 of fees (57000) and OT (30000) disallowed
    c.expected_rules = {"RULE_CASHLESS_PREAUTH": ["satisfied", None], "RULE_WAITING_PERIOD": ["satisfied", 0.0],
                        "RULE_ROOM_RENT_CAP": ["violated", 41625.0], "RULE_NON_PAYABLE_ITEMS": ["violated", 1000.0],
                        "RULE_SUM_INSURED": ["satisfied", 0.0], "RULE_PRE_EXISTING_DISCLOSURE": ["satisfied", 0.0]}
    c.expected_checks = {"CHECK_BILL_ARITHMETIC": "satisfied"}
    c.expected_verdict, c.expected_payable = "approved", 84375.0
    return c


def case_27() -> Case:
    c = Case("case_27_cashless_late_incomplete", "Cashless - late request without cost estimate", "cashless",
             "A planned hernia operation where the pre-authorisation was sent 17 hours before admission and the "
             "doctor's cost estimate is missing.", "Imran Khan", "PA-2026-0927")
    q_type = "Type of admission: Planned. Planned admission: 10-Sep-2026 09:00."
    q_sent = "Request submitted to insurer on: 09-Sep-2026 16:00."
    q_encl = "Enclosed: Pre-Authorisation Form."
    q_decl = "Existing medical conditions declared by the insured: None."
    q_room = "Requested room: Twin sharing, 2 days @ INR 3500.00/day = INR 7000.00."
    q_amt = "Estimated total cost stated by hospital: INR 60000.00."
    c.doc("preauthorisation_form.pdf", "CASHLESS PRE-AUTHORISATION REQUEST", "Pre-Authorisation Form",
          [q_type, q_sent, q_encl, q_decl, "Proposed treatment: Open inguinal hernia repair.", q_room, q_amt],
          detail={"document_type": "preauthorization_form", "admission_date": "2026-09-10"})
    c.fact("preauthorization_request", {"hospitalization_type": "planned", "documents_present": ["preauthorization_form"],
                                        "request_datetime": "2026-09-09T16:00", "admission_datetime": "2026-09-10T09:00"},
           "preauthorisation_form.pdf", q_sent)
    c.fact("hospitalization_details", {"policy_inception_date": INCEPTION, "admission_date": "2026-09-10",
                                       "days_elapsed": 252, "cause": "illness", "diagnosis": "inguinal hernia"},
           "preauthorisation_form.pdf", q_type)
    c.fact("room_rent_billing", {"room_type": "twin_sharing", "daily_rate": 3500.0, "days": 2, "total_amount": 7000.0},
           "preauthorisation_form.pdf", q_room)
    c.fact("claim_amount_summary", {"total_claimed": 60000.0, "previously_paid_this_year": 0.0},
           "preauthorisation_form.pdf", q_amt)
    c.fact("declared_conditions", {"conditions": []}, "preauthorisation_form.pdf", q_decl)
    c.finish_details("preauthorisation_form.pdf")
    c.expected_rules = {"RULE_CASHLESS_PREAUTH": ["violated", None], "RULE_ROOM_RENT_CAP": ["satisfied", 0.0]}
    c.expected_checks = {}
    c.expected_verdict, c.expected_payable = "incomplete", 60000.0
    return c


def case_28() -> Case:
    c = Case("case_28_full_reimbursement_all_documents", "Reimbursement - complete set of 10 documents", "reimbursement",
             "Gallbladder surgery with every document type: a deluxe room above the limit, gloves and masks on the "
             "pharmacy bill, and an undeclared thyroid condition that only shows up in the prescription.",
             "Lakshmi Menon", "CLM-2026-0828")
    adm, dis = "2026-08-02", "2026-08-06"
    q_docs = "Documents enclosed: Claim Form, Hospital Bill, Discharge Summary, Pharmacy Bill, Prescription, Reports."
    q_decl = "Existing medical conditions declared by the insured: Hypertension."
    q_claim = "Total amount claimed: INR 1,24,600.00."
    c.doc("claim_form.pdf", "HEALTH INSURANCE REIMBURSEMENT CLAIM FORM", "Claim Form",
          [q_docs, f"Date of admission: {adm}. Date of discharge: {dis}.", q_decl, q_claim,
           "Bank account for reimbursement: XXXX XXXX 4417 (fictional)."],
          detail={"document_type": "claim_form", "admission_date": adm, "discharge_date": dis})
    q_room = "Room rent (Deluxe room): 4 days @ INR 6250.00/day = INR 25000.00."
    q_fees = "Professional fees: Surgeon INR 40000, Anaesthetist INR 10000. Total INR 50000.00."
    q_ot = "Operation theatre charges: INR 25000.00."
    items = [("Room rent (Deluxe room)", 25000), ("Surgeon fee", 40000), ("Anaesthetist fee", 10000),
             ("Operation theatre charges", 25000), ("Nursing charges", 4000), ("Laboratory investigations", 3500)]
    c.doc("hospital_bill.pdf", "ST. ANNE'S HOSPITAL - FINAL ITEMISED BILL", "Hospital Bill",
          [f"Admitted: {adm} | Discharged: {dis}", q_room, q_fees, q_ot, "Nursing charges: INR 4000.00.",
           "Laboratory investigations: INR 3500.00.", "Total bill amount: INR 107500.00."],
          detail={"document_type": "hospital_bill", "admission_date": adm, "discharge_date": dis,
                  "stated_total": 107500, "line_items": [{"item": i, "amount": a} for i, a in items]})
    q_gloves = "Gloves (surgical, 2 boxes): INR 600.00."
    q_masks = "Face masks (N95, 10 pieces): INR 400.00."
    c.doc("pharmacy_bill.pdf", "ST. ANNE'S IN-HOUSE PHARMACY - BILL", "Pharmacy Bill",
          ["Medicines and IV fluids during stay: INR 9800.00.", q_gloves, q_masks, "Total: INR 10800.00."],
          detail={"document_type": "pharmacy_bill", "stated_total": 10800,
                  "line_items": [{"item": "Medicines and IV fluids", "amount": 9800},
                                 {"item": "Gloves", "amount": 600}, {"item": "Face masks", "amount": 400}]})
    q_rx = "Continue long-term: Tab Amlodipine 5 mg once daily and Tab Thyroxine 50 mcg once daily before breakfast."
    c.doc("prescription.pdf", "PRESCRIPTION - MEDICINES AT DISCHARGE", "Prescription",
          ["Rx: Tab Pantoprazole 40 mg once daily for 14 days. Tab Paracetamol 650 mg when required.", q_rx],
          detail={"document_type": "prescription"})
    c.doc("lab_report.pdf", "LABORATORY REPORT", "Lab Report",
          ["Liver function: total bilirubin 1.9 mg/dL (high), ALP 210 U/L (high).",
           "TSH: 6.2 mIU/L (on treatment)."], detail={"document_type": "lab_report"})
    q_usg = "Ultrasound abdomen (28-Jul-2026): multiple gallstones with a thickened gallbladder wall."
    c.doc("scan_report.pdf", "RADIOLOGY REPORT - ULTRASOUND", "Scan Report",
          [q_usg, "Impression: acute calculous cholecystitis."], detail={"document_type": "scan_report"})
    q_dx = "Diagnosis: Acute calculous cholecystitis, laparoscopic cholecystectomy performed. Cause: illness."
    q_hist = "Past medical history: Hypertension for 3 years on Amlodipine."
    c.doc("discharge_summary.pdf", "DISCHARGE SUMMARY", "Discharge Summary",
          [f"Date of admission: {adm}. Date of discharge: {dis}.", q_dx, q_hist, "Condition at discharge: Stable."],
          detail={"document_type": "discharge_summary", "admission_date": adm, "discharge_date": dis})
    q_pre1 = "27-Jul-2026: Surgeon consultation: INR 800.00."
    q_pre2 = "28-Jul-2026: Ultrasound abdomen: INR 2500.00."
    c.doc("pre_hospitalisation_bill.pdf", "BILLS BEFORE ADMISSION", "Pre-Hospitalisation Bill",
          [q_pre1, q_pre2, "Total: INR 3300.00."],
          detail={"document_type": "pre_hospitalization_bill", "stated_total": 3300,
                  "line_items": [{"item": "Surgeon consultation", "amount": 800}, {"item": "Ultrasound abdomen", "amount": 2500}]})
    q_post1 = "20-Aug-2026: Follow-up consultation: INR 900.00."
    q_post2 = "20-Aug-2026: Wound dressing: INR 600.00."
    c.doc("post_hospitalisation_bill.pdf", "BILLS AFTER DISCHARGE", "Post-Hospitalisation Bill",
          [q_post1, q_post2, "Total: INR 1500.00."],
          detail={"document_type": "post_hospitalization_bill", "stated_total": 1500,
                  "line_items": [{"item": "Follow-up consultation", "amount": 900}, {"item": "Wound dressing", "amount": 600}]})
    q_amb = "Road ambulance from residence to St. Anne's Hospital on 02-Aug-2026: INR 1500.00."
    c.doc("ambulance_bill.pdf", "CITY AMBULANCE SERVICE - RECEIPT", "Ambulance Bill", [q_amb, "Total: INR 1500.00."],
          detail={"document_type": "ambulance_bill", "stated_total": 1500,
                  "line_items": [{"item": "Road ambulance", "amount": 1500}]})
    c.fact("submitted_documents", ["claim_form", "hospital_bill", "discharge_summary"], "claim_form.pdf", q_docs)
    c.fact("hospitalization_details", {"policy_inception_date": INCEPTION, "admission_date": adm, "discharge_date": dis,
                                       "days_elapsed": 213, "cause": "illness",
                                       "diagnosis": "acute calculous cholecystitis"}, "discharge_summary.pdf", q_dx)
    c.fact("room_rent_billing", {"room_type": "deluxe_room", "daily_rate": 6250.0, "days": 4, "total_amount": 25000.0},
           "hospital_bill.pdf", q_room)
    c.fact("professional_fees_billing", {"surgeon_fee": 40000.0, "anesthetist_fee": 10000.0, "total_amount": 50000.0},
           "hospital_bill.pdf", q_fees)
    c.fact("operation_theatre_billing", {"ot_charges": 25000.0, "total_amount": 25000.0}, "hospital_bill.pdf", q_ot)
    c.fact("non_payable_items_breakdown", [{"item": "gloves", "amount": 600.0}, {"item": "face_masks", "amount": 400.0}],
           "pharmacy_bill.pdf", q_gloves)
    c.fact("pre_hospitalization_expenses", [{"description": "Surgeon consultation", "amount": 800.0, "date": "2026-07-27"},
                                            {"description": "Ultrasound abdomen", "amount": 2500.0, "date": "2026-07-28"}],
           "pre_hospitalisation_bill.pdf", q_pre2)
    c.fact("post_hospitalization_expenses", [{"description": "Follow-up consultation", "amount": 900.0, "date": "2026-08-20"},
                                             {"description": "Wound dressing", "amount": 600.0, "date": "2026-08-20"}],
           "post_hospitalisation_bill.pdf", q_post1)
    c.fact("ambulance_charges", {"amount": 1500.0}, "ambulance_bill.pdf", q_amb)
    c.fact("claim_amount_summary", {"total_claimed": 124600.0, "previously_paid_this_year": 0.0}, "claim_form.pdf", q_claim)
    c.fact("declared_conditions", {"conditions": ["Hypertension"]}, "claim_form.pdf", q_decl)
    c.fact("medical_conditions_found", [
        {"condition": "Hypertension", "evidence": "Hypertension for 3 years on Amlodipine",
         "inferred_from_medicine": False, "document": "discharge_summary.pdf"},
        {"condition": "Hypothyroidism", "evidence": "Tab Thyroxine 50 mcg long-term; TSH on treatment",
         "inferred_from_medicine": True, "document": "prescription.pdf"}], "prescription.pdf", q_rx)
    c.fact("patient_medical_history", {"diagnosis": "hypertension", "pre_existing_history": "Hypertension for 3 years",
                                       "hospitalization_cause": "acute calculous cholecystitis", "claimed_amount": 124600.0},
           "discharge_summary.pdf", q_hist)
    c.finish_details("claim_form.pdf")
    # Room: 4 x (6250-5000) = 5000; ratio 5000/6250 = 0.8 -> 20% of fees (50000) and OT (25000) disallowed
    c.expected_rules = {"RULE_REQ_DOCS": ["satisfied", 0.0], "RULE_WAITING_PERIOD": ["satisfied", 0.0],
                        "RULE_ROOM_RENT_CAP": ["violated", 20000.0], "RULE_NON_PAYABLE_ITEMS": ["violated", 1000.0],
                        "RULE_PRE_HOSPITALIZATION": ["satisfied", 0.0], "RULE_POST_HOSPITALIZATION": ["satisfied", 0.0],
                        "RULE_AMBULANCE_LIMIT": ["satisfied", 0.0], "RULE_SUM_INSURED": ["satisfied", 0.0],
                        "RULE_PRE_EXISTING_DISCLOSURE": ["needs_review", None],
                        "AMB_PRE_EXISTING_CONDITIONS": ["needs_review", None]}
    c.expected_checks = {k: "satisfied" for k in ("CHECK_PATIENT_NAME", "CHECK_POLICY_NUMBER", "CHECK_DATES",
                                                  "CHECK_BILL_ARITHMETIC", "CHECK_CLAIMED_VS_BILLED",
                                                  "CHECK_DUPLICATE_CHARGES")}
    c.expected_verdict, c.expected_payable = "needs_review", 103600.0
    return c


def case_29() -> Case:
    c = Case("case_29_full_cashless_emergency_accident", "Cashless - emergency accident, complete set of 6 documents",
             "cashless",
             "Road accident on day 20 of the policy: emergency cashless request sent the next morning, with reports. "
             "INR 3.8 lakh was already paid this year, so the approval is limited by the remaining sum insured.",
             "Vikram Singh", "PA-2026-0129")
    q_type = "Type of admission: Emergency. Admitted: 21-Jan-2026 22:30."
    q_sent = "Request submitted to insurer on: 22-Jan-2026 09:00."
    q_encl = "Enclosed: Pre-Authorisation Form, Treating Doctor's Cost Estimate, X-ray Report, Lab Report."
    q_decl = "Existing medical conditions declared by the insured: None."
    q_paid = "Claims already paid under this policy this year: INR 380000.00."
    c.doc("preauthorisation_form.pdf", "CASHLESS PRE-AUTHORISATION REQUEST", "Pre-Authorisation Form",
          [q_type, q_sent, q_encl, q_decl, q_paid,
           "Proposed treatment: Open reduction and internal fixation of left femur fracture."],
          detail={"document_type": "preauthorization_form", "admission_date": "2026-01-21"})
    q_room = "General ward: 5 days @ INR 4500.00/day = INR 22500.00."
    q_fees = "Professional fees: Orthopaedic surgeon INR 35000, Anaesthetist INR 9000. Total INR 44000.00."
    q_ot = "Operation theatre charges: INR 28000.00."
    q_np = "Registration fees: INR 500.00. Thermometer charges: INR 300.00."
    q_amb_est = "Ambulance from accident site: INR 2500.00."
    q_total = "Total estimated cost: INR 151800.00."
    items = [("General ward", 22500), ("Orthopaedic surgeon fee", 35000), ("Anaesthetist fee", 9000),
             ("Operation theatre charges", 28000), ("Implant (plate and screws)", 42000),
             ("Pharmacy and consumables", 12000), ("Registration fees", 500), ("Thermometer charges", 300),
             ("Ambulance from accident site", 2500)]
    c.doc("cost_estimate.pdf", "TREATING DOCTOR'S COST ESTIMATE", "Cost Estimate",
          [q_room, q_fees, q_ot, "Implant (plate and screws): INR 42000.00.", "Pharmacy and consumables: INR 12000.00.",
           q_np, q_amb_est, q_total],
          detail={"document_type": "cost_estimate", "stated_total": 151800,
                  "line_items": [{"item": i, "amount": a} for i, a in items]})
    q_dx = "Diagnosis: Closed fracture of left femur after a road traffic accident on 21-Jan-2026. Cause: accidental injury."
    c.doc("doctor_referral_letter.pdf", "EMERGENCY DEPARTMENT - REFERRAL FOR SURGERY", "Doctor's Letter",
          [q_dx, "Advised: urgent surgical fixation within 24 hours. No known chronic illness."],
          detail={"document_type": "other"})
    c.doc("xray_report.pdf", "RADIOLOGY REPORT - X-RAY", "Scan Report",
          ["X-ray left thigh (21-Jan-2026): displaced mid-shaft fracture of the left femur."],
          detail={"document_type": "scan_report"})
    c.doc("lab_report.pdf", "LABORATORY REPORT", "Lab Report",
          ["Haemoglobin 11.2 g/dL. Blood sugar (random) 104 mg/dL. Coagulation profile normal."],
          detail={"document_type": "lab_report"})
    q_amb = "Road ambulance from accident site to Highway Trauma Hospital on 21-Jan-2026: INR 2500.00."
    c.doc("ambulance_bill.pdf", "HIGHWAY AMBULANCE - RECEIPT", "Ambulance Bill", [q_amb],
          detail={"document_type": "other"})
    c.fact("preauthorization_request", {"hospitalization_type": "emergency",
                                        "documents_present": ["preauthorization_form", "cost_estimate", "scan_report",
                                                              "lab_report"],
                                        "request_datetime": "2026-01-22T09:00", "admission_datetime": "2026-01-21T22:30"},
           "preauthorisation_form.pdf", q_sent)
    c.fact("hospitalization_details", {"policy_inception_date": INCEPTION, "admission_date": "2026-01-21",
                                       "days_elapsed": 20, "cause": "accidental_injury",
                                       "diagnosis": "closed fracture of left femur"}, "doctor_referral_letter.pdf", q_dx)
    c.fact("room_rent_billing", {"room_type": "general_ward", "daily_rate": 4500.0, "days": 5, "total_amount": 22500.0},
           "cost_estimate.pdf", q_room)
    c.fact("professional_fees_billing", {"surgeon_fee": 35000.0, "anesthetist_fee": 9000.0, "total_amount": 44000.0},
           "cost_estimate.pdf", q_fees)
    c.fact("operation_theatre_billing", {"ot_charges": 28000.0, "total_amount": 28000.0}, "cost_estimate.pdf", q_ot)
    c.fact("non_payable_items_breakdown", [{"item": "registration_fees", "amount": 500.0},
                                           {"item": "thermometer_charges", "amount": 300.0}], "cost_estimate.pdf", q_np)
    c.fact("ambulance_charges", {"amount": 2500.0}, "ambulance_bill.pdf", q_amb)
    c.fact("claim_amount_summary", {"total_claimed": 151800.0, "previously_paid_this_year": 380000.0},
           "preauthorisation_form.pdf", q_paid)
    c.fact("declared_conditions", {"conditions": []}, "preauthorisation_form.pdf", q_decl)
    c.finish_details("preauthorisation_form.pdf")
    # Deductions 800 (non-payable) + 500 (ambulance over 2000) -> 150500; remaining sum insured 120000 caps it.
    c.expected_rules = {"RULE_CASHLESS_PREAUTH": ["satisfied", None], "RULE_WAITING_PERIOD": ["satisfied", 0.0],
                        "RULE_ROOM_RENT_CAP": ["satisfied", 0.0], "RULE_NON_PAYABLE_ITEMS": ["violated", 800.0],
                        "RULE_AMBULANCE_LIMIT": ["violated", 500.0], "RULE_SUM_INSURED": ["violated", 31800.0],
                        "RULE_PRE_EXISTING_DISCLOSURE": ["satisfied", 0.0]}
    c.expected_checks = {"CHECK_BILL_ARITHMETIC": "satisfied"}
    c.expected_verdict, c.expected_payable = "approved", 120000.0
    return c


ALL_CASES = [case_21, case_22, case_23, case_24, case_25, case_26, case_27, case_28, case_29]


def write_case(c: Case, out_dir: Path = DEMO_DIR) -> Path:
    folder = out_dir / c.case_id
    folder.mkdir(parents=True, exist_ok=True)
    spec = PacketSpec(packet_id=c.case_id, scenario_id=c.case_id, description=c.story, patient_name=c.patient,
                      claim_number=c.claim_ref, policy_number=POLICY, documents={}, claim_facts=[],
                      applicable_rule_ids=[])
    for i, (filename, d) in enumerate(sorted(c.docs.items())):
        content = PacketDocumentContent(title=d["title"], doc_type=d["doc_type"], header_info={},
                                        body_paragraphs=d["paragraphs"],
                                        footer_text=f"Fictional ClaimCheck demo document | {c.case_id} | {c.claim_ref}")
        data = (_render_rasterised_pdf_bytes(content, spec, seed=2026 + i) if d["scanned"]
                else _render_digital_pdf_bytes(content, spec))
        (folder / filename).write_bytes(data)
    (folder / "ground_truth_facts.json").write_text(json.dumps(c.facts, indent=2) + "\n", encoding="utf-8")
    (folder / "expected.json").write_text(json.dumps({
        "case_id": c.case_id, "label": c.label, "claim_type": c.claim_type, "story": c.story,
        "expected_rules": c.expected_rules, "expected_checks": c.expected_checks,
        "expected_verdict": c.expected_verdict, "expected_payable": c.expected_payable,
        "scanned_documents": [f for f, d in c.docs.items() if d["scanned"]],
    }, indent=2) + "\n", encoding="utf-8")
    return folder


def main():
    for build in ALL_CASES:
        folder = write_case(build())
        print(f"Wrote {folder.relative_to(ROOT_DIR)}")


if __name__ == "__main__":
    main()

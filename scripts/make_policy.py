"""Script to generate a synthetic health insurance policy PDF.

Uses ReportLab to create `data/synthetic_policy.pdf` with readable,
extractable text, clear page numbers, and five operative policy clauses
positioned on deterministic pages.
"""

from __future__ import annotations

import os
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    PageBreak,
    Table,
    TableStyle,
    HRFlowable,
)
from reportlab.pdfgen import canvas


class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas to dynamically compute and draw exact total page numbers."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, total_pages: int):
        self.saveState()
        self.setFont("Helvetica-Bold", 8)
        self.setFillColor(colors.HexColor("#666666"))

        # Running header on pages 2+
        if self._pageNumber > 1:
            self.drawString(
                54,
                750,
                "APEX CARE GENERAL INSURANCE LTD. | AURA HEALTH ASSURE POLICY (PLAN B) - SYNTHETIC TEST CONTRACT",
            )
            self.setStrokeColor(colors.HexColor("#D0D5DD"))
            self.setLineWidth(0.5)
            self.line(54, 744, 612 - 54, 744)

        # Running footer on all pages
        self.setFont("Helvetica", 8)
        self.drawString(
            54,
            36,
            "FICTIONAL POLICY FOR LOCAL AUTOMATED TESTING ONLY - NOT A REAL INSURANCE CONTRACT",
        )
        self.drawRightString(
            612 - 54,
            36,
            f"Page {self._pageNumber} of {total_pages}",
        )
        self.setStrokeColor(colors.HexColor("#D0D5DD"))
        self.setLineWidth(0.5)
        self.line(54, 48, 612 - 54, 48)

        self.restoreState()


def build_synthetic_policy_pdf(output_path: str | Path) -> Path:
    """Build the synthetic 4-page policy PDF document.

    Args:
        output_path: Target path for the generated PDF.

    Returns:
        Path: Absolute path of the generated PDF file.
    """
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    doc = SimpleDocTemplate(
        str(out_file),
        pagesize=letter,
        leftMargin=54,
        rightMargin=54,
        topMargin=54,
        bottomMargin=54,
    )

    base_styles = getSampleStyleSheet()

    # Custom styles
    title_style = ParagraphStyle(
        "DocTitle",
        parent=base_styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#0F2942"),
        alignment=0,
        spaceAfter=6,
    )

    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=base_styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=colors.HexColor("#1A56DB"),
        spaceAfter=12,
    )

    h1_style = ParagraphStyle(
        "Heading1_Custom",
        parent=base_styles["Heading1"],
        fontName="Helvetica-Bold",
        fontSize=13,
        leading=17,
        textColor=colors.HexColor("#0F2942"),
        spaceBefore=10,
        spaceAfter=6,
    )

    body_style = ParagraphStyle(
        "Body_Custom",
        parent=base_styles["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=13.5,
        textColor=colors.HexColor("#1F2A37"),
        spaceAfter=8,
    )

    clause_style = ParagraphStyle(
        "Clause_Custom",
        parent=base_styles["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=13.5,
        textColor=colors.HexColor("#0B192C"),
        spaceAfter=10,
    )

    disclaimer_style = ParagraphStyle(
        "Disclaimer_Custom",
        parent=base_styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#991B1B"),
        spaceAfter=8,
    )

    story = []

    # =========================================================================
    # PAGE 1: Title, Disclaimer, Schedule & Operative Clause 1 (Required Docs)
    # =========================================================================
    story.append(Paragraph("APEX CARE GENERAL INSURANCE LTD.", title_style))
    story.append(Paragraph("AURA HEALTH ASSURE POLICY (PLAN B) -- POLICY TERMS", subtitle_style))

    story.append(
        Paragraph(
            "SYNTHETIC TEST DOCUMENT NOTICE: THIS IS A STRICTLY FICTIONAL POLICY DOCUMENT "
            "GENERATED EXCLUSIVELY FOR LOCAL AUTOMATED TESTING OF THE CLAIMCHECK 2.0 SYSTEM. "
            "IT DOES NOT CONSTITUTE A REAL CONTRACT OF INSURANCE, NOR DOES IT REPRESENT ANY REAL ENTITY.",
            disclaimer_style,
        )
    )
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#D0D5DD"), spaceAfter=10))

    story.append(Paragraph("Policy Schedule Summary", h1_style))
    schedule_data = [
        ["Policy Number:", "POL-AURA-2026-001", "Plan Name:", "Aura Health Assure (Plan B)"],
        ["Named Insured:", "Synthetic Insured Person", "Sum Insured:", "INR 500000"],
        ["Policy Inception Date:", "01-Jan-2026", "Policy Expiry Date:", "31-Dec-2026"],
        ["Fictional Underwriter:", "Apex Care General Insurance Ltd.", "Jurisdiction:", "Test Jurisdiction Only"],
    ]
    t = Table(schedule_data, colWidths=[120, 132, 110, 142])
    t.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#1F2A37")),
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F8FAFC")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(t)
    story.append(Spacer(1, 12))

    story.append(Paragraph("Section 1: Claim Filing and Documentation Requirements", h1_style))
    story.append(
        Paragraph(
            "Claims for medical expense reimbursement must adhere to the procedural guidelines, "
            "documentary standards, and verification protocols set forth in this section.",
            body_style,
        )
    )

    # Operative Clause 1: Required Documents
    clause_1_text = (
        "Section 1.1 (Mandatory Claim Documents): Every reimbursement claim submitted under this policy "
        "must include all three of the following documents: (1) a completed and signed Claim Form, "
        "(2) an itemized original Hospital Bill with receipt, and (3) the official Hospital Discharge Summary. "
        "If any of these three required documents is missing, the claim shall be considered incomplete and "
        "cannot be processed for reimbursement."
    )
    story.append(Paragraph(clause_1_text, clause_style))
    story.append(
        Paragraph(
            "Note: Document submissions must be legible and furnished in clear digital or physical copy. "
            "Supplemental records such as pharmacy vouchers and diagnostic reports may be requested "
            "during claim adjudication but do not substitute for the three primary mandatory documents.",
            body_style,
        )
    )

    # End of Page 1
    story.append(PageBreak())

    # =========================================================================
    # PAGE 2: Operative Clause 2 (Waiting Period) & Operative Clause 3 (Room Rent)
    # =========================================================================
    story.append(Spacer(1, 14))
    story.append(Paragraph("Section 2: Waiting Periods and Accommodation Benefit Limits", h1_style))
    story.append(
        Paragraph(
            "This section defines the policy timeframes governing coverage commencement "
            "and sets the monetary ceilings applicable to hospital room occupancy.",
            body_style,
        )
    )

    # Operative Clause 2: Waiting Period
    clause_2_text = (
        "Section 2.1 (Initial Waiting Period): A mandatory waiting period of 30 days shall apply from "
        "the policy inception date. Any hospitalization or medical expenses incurred for treatment of illness "
        "during the first 30 days of coverage are strictly excluded from reimbursement, except where "
        "hospitalization is necessitated solely by an accidental injury sustained after the policy inception date."
    )
    story.append(Paragraph(clause_2_text, clause_style))

    story.append(
        Paragraph(
            "The initial waiting period begins at 00:00 hours on the policy inception date (01-Jan-2026) "
            "and terminates at 23:59 hours on the 30th consecutive day following inception. "
            "Hospital admissions occurring on or after day 31 are eligible for illness coverage subject "
            "to other terms and conditions.",
            body_style,
        )
    )
    story.append(Spacer(1, 10))

    # Operative Clause 3: Room Rent Cap and Proportional Deduction
    clause_3_text = (
        "Section 2.2 (Room Rent Daily Limit and Proportional Deduction): The maximum eligible room and "
        "boarding expense covered by the insurer is INR 5000 per day of hospitalization. Any room rent "
        "charges incurred in excess of INR 5000 per day are non-payable by the insurer and shall be borne "
        "entirely by the insured person. If the actual daily room rent exceeds INR 5000, then professional "
        "fees and operation theatre charges are payable only in the ratio 5000 / actual daily rate, with "
        "the remaining balance of such charges being non-payable."
    )
    story.append(Paragraph(clause_3_text, clause_style))

    story.append(
        Paragraph(
            "Proportional Deduction Application: The proportional deduction applies strictly to associated "
            "professional fees (such as surgeon, anesthetist, and medical practitioner fees) and operation "
            "theatre charges. Other hospital charges such as medicines, diagnostic tests, and consumables "
            "are not subject to proportional deduction.",
            body_style,
        )
    )

    # End of Page 2
    story.append(PageBreak())

    # =========================================================================
    # PAGE 3: Operative Clause 4 (Sub-Limit) & Operative Clause 5 (Non-Payable)
    # =========================================================================
    story.append(Spacer(1, 14))
    story.append(Paragraph("Section 3: Treatment Sub-Limits and Non-Payable Expense Schedule", h1_style))
    story.append(
        Paragraph(
            "Specific medical interventions and hospital charge categories are subject to fixed monetary "
            "ceilings or statutory exclusions as detailed below.",
            body_style,
        )
    )

    # Operative Clause 4: Sub-Limit
    clause_4_text = (
        "Section 3.1 (Sub-Limit on Cataract Surgery): Reimbursement for cataract surgery shall be subject "
        "to a strict monetary sub-limit of INR 40000 per eye, inclusive of surgeon fees, operating room charges, "
        "and intraocular lens costs. Any expenses incurred for cataract surgery in excess of INR 40000 per eye "
        "are non-payable by the insurer."
    )
    story.append(Paragraph(clause_4_text, clause_style))

    story.append(
        Paragraph(
            "Where bilateral cataract surgery is performed across both eyes during separate surgical sessions "
            "or within a single hospitalization, each eye shall be evaluated independently against the INR 40000 "
            "monetary sub-limit.",
            body_style,
        )
    )
    story.append(Spacer(1, 10))

    # Operative Clause 5: Non-Payable Items
    clause_5_text = (
        "Section 3.2 (Non-Payable Items Schedule): The following non-medical and administrative items "
        "are non-payable and strictly excluded from reimbursement: registration fees, admission processing charges, "
        "gloves, face masks, sanitizers, thermometer charges, and luxury patient amenity kits."
    )
    story.append(Paragraph(clause_5_text, clause_style))

    story.append(
        Paragraph(
            "Hospitals must itemize bill components clearly. Invoiced line items matching any of the aforementioned "
            "non-payable items will be disallowed in full from claim settlement computations.",
            body_style,
        )
    )

    # End of Page 3
    story.append(PageBreak())

    # =========================================================================
    # PAGE 4: General Policy Exclusions and Administrative Provisions
    # =========================================================================
    story.append(Spacer(1, 14))
    story.append(Paragraph("Section 4: General Policy Exclusions and Administrative Provisions", h1_style))

    # Section 4.1: Pre-Existing Conditions Exclusion (Ambiguity)
    section_4_1_text = (
        "Section 4.1 (Pre-Existing Conditions Exclusion): Any medical condition, ailment, or injury that "
        "constitutes a Pre-Existing Condition, as defined under applicable regulations, shall be excluded "
        "from coverage until the expiration of the statutory waiting period. Claims arising from or "
        "attributable to Pre-Existing Conditions shall not be indemnified."
    )
    story.append(Paragraph(section_4_1_text, clause_style))

    # Section 4.2: General Definitions (Harmless filler, no limits or amounts)
    section_4_2_text = (
        "Section 4.2 (General Definitions): 'Hospital' means any institution established for in-patient care "
        "and medical treatment of sickness and injuries, possessing qualified medical staff and nursing services. "
        "'Day Care Treatment' refers to medical treatment or surgical procedures undertaken under general or "
        "local anesthesia in a hospital where the patient is discharged within the same calendar day."
    )
    story.append(Paragraph(section_4_2_text, body_style))

    # Section 4.3: Claim Procedure (Harmless filler, no limits or amounts)
    section_4_3_text = (
        "Section 4.3 (Claim Submission Procedure): Notice of claim and complete documentary submissions "
        "must be provided to the insurer following hospital discharge. The insurer may seek clarification "
        "or additional clinical records where appropriate to verify adherence to policy terms."
    )
    story.append(Paragraph(section_4_3_text, body_style))

    # Section 4.4: Ground truth verification declarations (Harmless filler)
    section_4_4_text = (
        "Section 4.4 (Verification Declarations for ClaimCheck 2.0): This synthetic contract serves as the "
        "canonical reference document for local automated extraction, deterministic rule compilation, and "
        "ground-truth scenario benchmarking. Documented clauses contain explicit formulas and verifiable terms."
    )
    story.append(Paragraph(section_4_4_text, body_style))
    story.append(Spacer(1, 8))

    # Summary table of operative rules on Page 4 for reference
    ref_table_data = [
        ["Rule / Section", "Target Section", "Governing Limit / Requirement", "Policy Page"],
        ["Required Documents", "Section 1.1", "Claim Form, Hospital Bill, Discharge Summary", "Page 1"],
        ["Initial Waiting Period", "Section 2.1", "30 Days from Inception (Accident Exempt)", "Page 2"],
        ["Room Rent & Proportional", "Section 2.2", "INR 5000/day; Proportional 5000/rate on Fees & OT", "Page 2"],
        ["Surgical Sub-Limit", "Section 3.1", "INR 40000 per eye for Cataract Surgery", "Page 3"],
        ["Non-Payable Items", "Section 3.2", "7 specified administrative & consumable items", "Page 3"],
        ["Pre-Existing Conditions", "Section 4.1", "Excluded as defined under applicable regulations", "Page 4"],
    ]
    t2 = Table(ref_table_data, colWidths=[120, 90, 220, 74])
    t2.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#1F2A37")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E2E8F0")),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F8FAFC")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(t2)
    story.append(Spacer(1, 10))

    story.append(
        Paragraph(
            "Authorized Signatory: Dr. Rajeshwar Varma, Head of Underwriting (Fictional)<br/>"
            "Apex Care General Insurance Ltd. - ClaimCheck 2.0 Test Suite",
            disclaimer_style,
        )
    )

    # =========================================================================
    # PAGE 5: Hospitalisation Benefits, Ambulance, Sum Insured, Cashless, Disclosure
    # (Appended in ClaimCheck v3. Pages 1-4 are unchanged so existing citations hold.)
    # =========================================================================
    story.append(PageBreak())
    story.append(Spacer(1, 14))
    story.append(Paragraph("Section 5: Hospitalisation Benefits, Cashless Facility and Disclosure", h1_style))
    story.append(
        Paragraph(
            "This section sets the time windows for expenses connected to a hospitalisation, the limits on "
            "ambulance charges and total payouts, the conditions for cashless treatment, and the duty to "
            "disclose existing medical conditions.",
            body_style,
        )
    )
    section_5_texts = [
        "Section 5.1 (Pre-Hospitalisation Expenses): Medical expenses incurred during the 30 days immediately "
        "before the date of admission, for the same illness or injury for which the hospitalisation claim is "
        "admissible, are payable. Pre-hospitalisation expenses incurred more than 30 days before the date of "
        "admission are non-payable.",
        "Section 5.2 (Post-Hospitalisation Expenses): Medical expenses incurred during the 60 days immediately "
        "after the date of discharge, for the same illness or injury for which the hospitalisation claim is "
        "admissible, are payable. Post-hospitalisation expenses incurred more than 60 days after the date of "
        "discharge are non-payable.",
        "Section 5.3 (Ambulance Charges): Road ambulance charges for transfer to the hospital are payable up to "
        "a maximum of INR 2000 per hospitalisation. Any ambulance charges in excess of INR 2000 per "
        "hospitalisation are non-payable.",
        "Section 5.4 (Sum Insured): The total amount payable for all claims in a policy year shall not exceed "
        "the Sum Insured of INR 500000. Any amount claimed in excess of the Sum Insured is non-payable.",
        "Section 5.5 (Cashless Pre-Authorisation): Cashless treatment at a network hospital requires a "
        "pre-authorisation request containing both of the following documents: (1) a completed "
        "Pre-Authorisation Form and (2) the treating doctor's Cost Estimate. For planned hospitalisation the "
        "request must reach the insurer at least 72 hours before admission. For emergency hospitalisation the "
        "request must reach the insurer within 24 hours of admission. Cashless approval is limited to amounts "
        "payable under this policy; any non-payable amount must be paid by the insured person to the hospital.",
        "Section 5.6 (Disclosure of Existing Conditions): The insured person must declare every existing medical "
        "condition in the claim form or pre-authorisation form. Any medical condition found in the medical "
        "records that was not declared shall be referred for medical review before the claim is settled.",
    ]
    for text in section_5_texts:
        story.append(Paragraph(text, clause_style))

    doc.build(story, canvasmaker=NumberedCanvas)
    return out_file.resolve()


def main():
    """CLI entrypoint to generate the synthetic policy PDF."""
    root_dir = Path(__file__).resolve().parent.parent
    target_path = root_dir / "data" / "synthetic_policy.pdf"
    generated_path = build_synthetic_policy_pdf(target_path)
    print(f"Successfully generated synthetic policy PDF at: {generated_path}")


if __name__ == "__main__":
    main()

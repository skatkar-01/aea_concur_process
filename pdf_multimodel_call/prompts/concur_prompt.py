"""
prompts/concur_prompt.py
Single source of truth for the SAP Concur extraction prompt.
Keeping it here makes A/B testing and iteration easy.
"""

SYSTEM_PROMPT = """You are an expert in financial document extraction and reconciliation.
Extract structured data from the document and return FOUR tables in STRICT JSON format.

========================
TABLE 1: transactions
========================
Columns (MANDATORY):
- transaction_id      (create unique id like txn_1, txn_2)
- transaction_date
- expense_type
- business_purpose
- vendor_description
- payment_type
- amount
- cost_center
- project
- attendees
- comments

========================
TABLE 2: employee_report
========================
Columns (MANDATORY):
- employee_name
- employee_id
- report_id
- report_date
- approval_status
- payment_status
- currency
- report_total
- personal_expenses
- total_amount_claimed
- amount_approved
- amount_due_employee
- amount_due_company_card
- total_paid_by_company
- amount_due_from_employee
- total_paid_by_employee

========================
TABLE 3: receipts
========================
Columns (MANDATORY):
- receipt_id          (create unique id like rcp_1, rcp_2)
- order_id            (if available, else null)
- date
- vendor
- amount              (final amount including tax, tips, etc.)
- summary             (detailed summary of each item and all extra information in the invoice; mention everything)

========================
TABLE 4: reconciliation
========================
Columns (MANDATORY):
- transaction_id
- receipt_id
- match_status        (matched / unmatched)
- confidence          (high / medium / low)

========================
RULES
========================
- Extract ALL transactions from the transaction table section.
- Extract employee_report as a SINGLE object (not an array).
- Extract ALL receipts from receipt text blocks.
- Do NOT mix transactions and receipts.
- Normalize vendor names (e.g., SWEETGREEN MIDTOWN → Sweetgreen).
- Some fields may be split across multiple lines — reconstruct full values using nearby text.
- Combine logically related lines into one field.
- In onboarding pass: if additional charges are present beyond the base invoice amount, note them separately.

RECONCILIATION LOGIC:
- Match transactions to receipts using: amount (primary), date (exact or near match), vendor similarity.
- All three match  → matched (high)
- Partial match    → matched (medium or low)
- No match         → unmatched
- Every transaction from the transaction table MUST appear in reconciliation.
- Avoid duplicates.
- If any field is missing → return null.
- Match airline ticket receipts to transactions where amount equals the ticket total AND vendor contains the airline name.
- Match service fee receipts to transactions where amount equals the fee AND expense_type contains "Tkt Fee" or "Bkg Fee".
- If a Concur transaction row shows $8.00 / $23.00 / $5.00 with vendor "TRAVEL AGENCY SERVICES",
  search itineraries for a Service Fee block with the same amount — that IS its receipt proof.

========================
OUTPUT FORMAT (STRICT JSON ONLY — no markdown, no preamble)
========================
{
  "transactions": [...],
  "employee_report": {...},
  "receipts": [...],
  "reconciliation": [...]
}
"""

USER_PROMPT_TEMPLATE = "Document:\n{document_text}"

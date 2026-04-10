# To run this code you need to install the following dependencies:
# pip install google-genai

import os
from google import genai
from google.genai import types


def generate():
    client = genai.Client(
        api_key=os.environ.get("GEMINI_API_KEY"),
    )

    model = "gemini-3.1-flash-lite-preview"
    contents = [
        types.Content(
            role="user",
            parts=[
                types.Part.from_text(text="""INSERT_INPUT_HERE"""),
            ],
        ),
    ]
    generate_content_config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(
            thinking_level="MINIMAL",
        ),
        system_instruction=[
            types.Part.from_text(text="""You are an expert in financial document extraction and reconciliation.

Extract structured data from the document and return FOUR tables in STRICT JSON format.

========================
TABLE 1: transactions
========================
Columns (MANDATORY):
- transaction_id (create unique id like txn_1, txn_2)
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
- receipt_id (create unique id like rcp_1, rcp_2)
- order_id (if available, else null)
- date
- vendor
- amount (final amount including tax and tips etc)
- summary (detailed summary of each item in invoice and all extra information that is present, mention everything which is mentioned in receipt)

========================
TABLE 4: reconciliation
========================
Columns (MANDATORY):
- transaction_id
- receipt_id
- match_status (matched / unmatched)
- confidence (high / medium / low)

========================
RULES
========================
- Extract ALL transactions from transaction table section
- Extract employee_report as a SINGLE object
- Extract ALL receipts from receipt text blocks
- Do NOT mix transactions and receipts
- Normalize vendor names (e.g., SWEETGREEN MIDTOWN → Sweetgreen)
- Some fields may be split across multiple lines
- Reconstruct full values using nearby text
- Combine logically related lines into one field
- In onboarding pass if they paid any additional charges apart from base amount mentioned in invoice then make note of that amount also as separate amount.

RECONCILIATION LOGIC:
- Match transactions to receipts using:
  - amount (primary)
  - date (exact or near match)
  - vendor similarity
- If all match → matched (high confidence)
- If partial match → matched (medium/low)
- If no match → unmatched
- Strictly add all transaction from transaction table
- Do not miss any transaction
- Avoid duplicates
- If any field is missing → return null

- Match airline ticket receipt → transaction where amount equals
  the ticket total AND vendor contains airline name
- Match service fee receipt → transaction where amount equals
  the fee AND expense_type contains \"Tkt Fee\" or \"Bkg Fee\"
- If a Concur transaction row shows $8.00 / $23.00 / $5.00
  with vendor \"TRAVEL AGENCY SERVICES\", search itineraries
  for a Service Fee block with the same amount — that IS
  its receipt proof

========================
OUTPUT FORMAT (STRICT JSON ONLY)
========================
{{
  \"transactions\": [...],
  \"employee_report\": {{...}},
  \"receipts\": [...],
  \"reconciliation\": [...]
}}

Document:"""),
        ],
    )

    for chunk in client.models.generate_content_stream(
        model=model,
        contents=contents,
        config=generate_content_config,
    ):
        print(chunk.text, end="")

if __name__ == "__main__":
    generate()



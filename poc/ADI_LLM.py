import json
from openai import AzureOpenAI

# =========================
# CONFIG
# =========================

DEPLOYMENT_NAME = "gpt-5-mini"  # or your deployment name
API_VERSION = "2024-12-01-preview"


ADI_JSON_PATH = "Brown $875.18.pdf.json"  # your file
OUTPUT_JSON_PATH = "Brown_extracted_output.json"

# Pricing (⚠️ update if needed based on Azure pricing)
INPUT_COST_PER_1K = 0.002   # example for gpt-5-mini
OUTPUT_COST_PER_1K = 0.006

# =========================
# LOAD ADI OUTPUT
# =========================
with open(ADI_JSON_PATH, "r", encoding="utf-8") as f:
    adi_data = json.load(f)

content = adi_data["analyzeResult"]["content"]

# =========================
# LLM CLIENT
# =========================
client = AzureOpenAI(
    api_key=AZURE_OPENAI_KEY,
    api_version=API_VERSION,
    azure_endpoint=AZURE_OPENAI_ENDPOINT
)

# =========================
# PROMPT
# =========================
prompt = f"""
You are an expert in financial document extraction and reconciliation.

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
- description
- date
- vendor
- amount (final amount including tax and tips etc)

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

RECONCILIATION LOGIC:
- Match transactions to receipts using:
  - amount (primary)
  - date (exact or near match)
  - vendor similarity
- If all match → matched (high confidence)
- If partial match → matched (medium/low)
- If no match → unmatched

- Avoid duplicates
- If any field is missing → return null

========================
OUTPUT FORMAT (STRICT JSON ONLY)
========================
{{
  "transactions": [...],
  "employee_report": {{...}},
  "receipts": [...],
  "reconciliation": [...]
}}

Document:
{content}
"""
# ===# =========================
# LLM CALL
# =========================
response = client.chat.completions.create(
    model=DEPLOYMENT_NAME,
    messages=[
        {"role": "system", "content": "You extract structured financial data."},
        {"role": "user", "content": prompt}
    ],
    # temperature=0,
    response_format={"type": "json_object"}  # ensures valid JSON
)

# =========================
# OUTPUT PARSE
# =========================
result_text = response.choices[0].message.content

try:
    result_json = json.loads(result_text)
except:
    result_json = {"raw_output": result_text}

# =========================
# SAVE OUTPUT
# =========================
with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as f:
    json.dump(result_json, f, indent=2)

print("\n✅ Output saved to:", OUTPUT_JSON_PATH)

# =========================
# TOKEN USAGE
# =========================
usage = response.usage

prompt_tokens = usage.prompt_tokens
completion_tokens = usage.completion_tokens
total_tokens = usage.total_tokens

# =========================
# COST CALCULATION
# =========================
input_cost = (prompt_tokens / 1000) * INPUT_COST_PER_1K
output_cost = (completion_tokens / 1000) * OUTPUT_COST_PER_1K
total_cost = input_cost + output_cost

# =========================
# PRINT METRICS
# =========================
print("\n===== TOKEN USAGE =====")
print(f"Prompt Tokens     : {prompt_tokens}")
print(f"Completion Tokens : {completion_tokens}")
print(f"Total Tokens      : {total_tokens}")

print("\n===== COST ESTIMATE =====")
print(f"Input Cost  : ${input_cost:.6f}")
print(f"Output Cost : ${output_cost:.6f}")
print(f"Total Cost  : ${total_cost:.6f}")
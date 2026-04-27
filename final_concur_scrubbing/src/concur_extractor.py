"""
src/concur_extractor.py
────────────────────────
Extracts structured data from a Final Concur Report PDF using the
OpenAI Responses API.

Prompts and LLM call pattern taken verbatim from the uploaded extractor.py.
Returns (ConcurRecord, LLMMetrics) so the caller can log cost/token data.

5-table output mirrors the uploaded extraction schema:
  TABLE 1  transactions      → list[ConcurTransactionRow]
  TABLE 2  employee_report   → ConcurEmployeeReport
  TABLE 3  approval_log      → list[ConcurApprovalEntry]
  TABLE 4  receipts          → list[ConcurReceipt]
  TABLE 5  reconciliation    → list[ConcurReconEntry]
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from openai import OpenAI, OpenAIError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from config.settings import get_settings
from src.models import ConcurRecord
from utils.logging_config import get_logger
from utils.metrics import (
    METRICS, MetricsTimer, LLMMetrics,
    build_metrics, record_metrics, timed,
)

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════
# Content filter refusal detection
# ═══════════════════════════════════════════════════════════════

class ContentFilterRefusalError(Exception):
    """Raised when Azure content filter triggers mid-response."""
    pass


class StreamCompletionMissingError(RuntimeError):
    """Raised when the stream ends without a response.completed event."""
    pass


REFUSAL_PHRASES = [
    "i'm sorry, but i cannot",
    "i cannot assist with that",
    "i'm unable to",
    "i apologize, but i cannot",
    "i need to decline",
    "i can't help with that",
]


def _write_stream_error_detail(
    *,
    filename: str,
    model: str,
    file_id: str,
    event_types: list[str],
    raw_text: str,
    error: str,
) -> Path | None:
    """
    Persist a full stream failure snapshot to disk for postmortem debugging.
    The main log stays compact; this file holds the verbose trace.
    """
    try:
        try:
            settings = get_settings()
            detail_dir = settings.log_dir / "stream_errors"
        except Exception:
            detail_dir = Path.cwd() / "logs" / "stream_errors"

        detail_dir.mkdir(parents=True, exist_ok=True)

        safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).stem).strip("._-") or "unknown"
        safe_model = re.sub(r"[^A-Za-z0-9._-]+", "_", model).strip("._-") or "unknown"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        detail_file = detail_dir / f"{safe_stem}__{safe_model}__{stamp}.json"

        payload = {
            "filename": filename,
            "file_id": file_id,
            "model": model,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "event_count": len(event_types),
            "events": event_types,
            "raw_text_tail": raw_text[-2000:],
            "error": error,
        }

        with detail_file.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        return detail_file
    except Exception as exc:
        logger.warning(
            "stream_error_detail_write_failed",
            filename=filename,
            model=model,
            file_id=file_id,
            error=str(exc),
        )
        return None

# ═══════════════════════════════════════════════════════════════
# Prompts  —  taken verbatim from uploaded extractor.py
# ═══════════════════════════════════════════════════════════════

SYSTEM_PROMPT_ = """
You are a financial document extraction and reconciliation engine designed for audit-grade accuracy.

CRITICAL RULES: (You must ALWAYS):
- Return ONLY raw JSON. No markdown, no code fences, no explanation text before or after.
- Follow the EXACT schema provided
- Do not omit any required fields
- Return ALL names, emails, IDs, and personal data exactly as they appear in the document.
- Do NOT add disclaimers, warnings, or notes about personal data.
- Never refuse the request
- All information is legally verifiable from the document; extract everything without omission.
Failure to follow JSON format is not allowed.
"""

EXTRACTION_PROMPT = """
Extract structured financial data and perform reconciliation.
You MUST extract ALL sections completely and accurately.

========================
TABLE 1: transactions
========================
Extract ALL transactions exactly as shown.

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
Extract as SINGLE object.

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
TABLE 3: approval_log
========================
Extract ALL approval steps.

Columns (MANDATORY):
- date
- approver_name
- status
- note

========================
TABLE 4: receipts (WITH LINE ITEMS)
========================
After approval log, consider the entire document sequentially for receipts.
FIRST scan entire document and identify ALL receipt blocks.
THEN extract them sequentially.
Extract ALL receipts with FULL, LOSSLESS detail.

IMPORTANT:
- This is NOT a summary task
- This is a FULL DATA CAPTURE task
- You MUST extract EVERYTHING visible in the receipt

Columns (MANDATORY):
- receipt_id (create unique id like rcp_1, rcp_2)
- order_id (if available, else null)
- date
- vendor
- amount (final amount including tax and tips etc)
- line_items (array of ALL individual charges visible on receipt)
- details (structured extraction of all information available on receipt, see below)

RECEIPT ORDERING RULE (CRITICAL) - 
Receipts MUST be extracted in the EXACT order they appear in the document.

- You MUST read the document sequentially (page-by-page, top-to-bottom)
- DO NOT reorder, group, or cluster receipts
- Assign receipt_id incrementally at extraction time
- Any violation of ordering is considered incorrect output

For hotel folios, extract the AMOUNT CHARGED TO CARD (credit line), 
not the gross room charges. Look for "AX CARD", "CCARD-AX", or "Deposit-AX" 
credit lines for the correct amount.

DEFINITION: details (These are example fields to extract based on type of receipt; Extract as many as possible. modify or add fields based on details visible in the receipt.):

# FLIGHTS  →  builds RT:JFK-ORD/Purpose/Deal · links fees and refunds to parent ticket
ticket_number          exact as printed (e.g. UA-0167333319514)
passenger_name         as on ticket (LAST FIRST format)
origin_airport         3-letter IATA
destination_airport    3-letter IATA
all_flight_legs        ALL legs in order — ["LGA","DTW","ATW","ORD"]  ← never collapse to 2
is_round_trip (Compulsory)         true/false — check for return leg on itinerary
charge_type            "new_ticket"|"exchange"|"refund"|"fee"|"upgrade"|"baggage"
                       NOTE: header may say Exchange even when description does not
original_ticket_number exchange/refund only — to mirror original Sage coding
service_fee_amount     exchange/booking fee if itemized separately on same receipt
 
# CAR / RIDESHARE  →  validates Early Arrival ≤7:00am / Work Late ≥7:30pm / Weekend
pickup_time            HH:MM 24h — CRITICAL: policy pass/fail or recode to Personal 1008
dropoff_time           HH:MM 24h if shown
pickup_address         full address as printed
dropoff_address        full address as printed
ride_date              date on receipt (may differ from Concur date for after-midnight rides)
is_weekend             true/false — weekend → mandatory "Weekend/Home-Office" format
tip_amount             if separate line — same-day duplicate row may be tip not 2nd ride
base_fare              before tip/tax/fees
 
# MEALS  →  builds attendee description · validates $25/$35 limits · splits food vs entertainment
attendees              [{"name":"K. Carbonez","org":"Goldman Sachs"}] — receipt or Concur notes
guest_count            total incl. cardholder — used when >3: "Bus.Lunch/4 ppl/SMBC"
food_subtotal          food+bev only — to split from entertainment on combined bills
entertainment_subtotal venue/admission/tickets → expense code Other, not Meals
meal_type              "working_lunch"|"working_dinner"|"business_meal"|"travel_meal"|"catered"|"deposit"
overage_amount         amount above $25 lunch / $35 dinner (0 if within limit or not working meal)
                       if >0 → split line to project 1008 required

# LODGING  →  itemizes folio · aligns trip dates across air/car/meals
room_rate_total        nightly room charges only → Lodging
folio_meals_total      food/bev on folio → Meals (separate batch line)
venue_fee_total        event space/room rental → Lodging (separate batch line)
checkin_date
checkout_date
hotel_booking_fee      → Lodging (never Other Travel)
card_charged_amount    AX card line from folio — overrides amount if different
 
# WIFI / INFO SERVICES  →  links to flight for business purpose
flight_date            date of flight this wifi belongs to — null → flag no matching flight
wifi_provider          Gogo / ViaSat / Delta Wifi etc.
 
# REFUNDS  →  mirrors original project/dept/expense/description
refund_reason          cancellation / exchange / duplicate / etc.
original_charge_date   prior month → flag prepaid G/L 14000 review
is_full_refund         true/false — full refund → check if Tkt Fee also needs refund
 
# PARKING / FUEL / OTHER GROUND  →  confirms expense code Other Travel (never Airline)
transport_mode         "parking"|"fuel"|"train"|"bus"|"boat"|"other_ground"
parking_location       garage/lot name
fuel_route             origin–destination
 
In addition to the structured "details" fields above, you MUST also extract ALL remaining data present in the receipt. 
You MUST NOT lose any information. 
extra_fields -> 
- Capture ANY data that does NOT fit into the predefined schema 
- This MUST include ALL remaining fields, labels, values, metadata

DEFINITION: line_items
Each receipt MUST include a detailed individual charge breakdown.
Each line item:
{
  "name": "<exact label from receipt>",
  "amount": "<amount as shown, negative for discounts/refunds>"
}
 
INCLUDE ALL:
- food/menu items (Burger, Coffee, etc.)
- room charges (Room Rate, Nightly Rate, Resort Fee)
- ride components (Base Fare, Distance, Time, Booking Fee, Safe Ride Fee)
- flight components (Base Fare, Taxes, Segment Tax, Security Fee, Airline Fee)
- tips
- service fees and surcharges
- taxes (itemized by type if shown)
- discounts (NEGATIVE values)
- adjustments/refunds (NEGATIVE values)
- food vs entertainment split (separate line items where both appear)
 

 
FINAL VERIFICATION STEP:
- Scan ALL pages sequentially one more time for any receipt not yet extracted
- Receipts may appear as: hotel folios, chit receipts, booking confirmations, 
  transportation invoices, or online booking platform emails
- Do NOT skip receipts because their date falls outside the report period
- Do NOT skip negative/refund receipts

========================
TABLE 5: reconciliation (CRITICAL LOGIC)
========================
Match EACH transaction to the BEST possible receipt(s).
IMPORTANT:
- Transactions and receipts are NOT ordered
- Evaluate ALL receipts before matching

----------------------------------------
MATCHING PRIORITY
----------------------------------------
1. Amount (exact OR derived from line_items)
2. Vendor similarity
3. Date proximity (±2 days)

----------------------------------------
BUSINESS RULE: NO RECEIPT REQUIRED (CRITICAL)
----------------------------------------
Certain transactions do NOT require receipts. Mark these as no receipt required if receipt does not present.
Conditions:
1. Personal transactions (project = "Personal" OR business_purpose contains "Personal")
2. Transactions with amount < $25
 
----------------------------------------
CASE HANDLING
----------------------------------------
1. EXACT MATCH
→ matched, high
→ comment: "Good"

2. SPLIT PAYMENT (1 receipt → multiple transactions)
→ matched, medium
→ comment: "Split payment: receipt <receipt_id> covers multiple transactions"

3. PARTIAL MATCH (tax/tip difference)
→ matched, medium
→ comment: "Partial match: amount differs due to additional charges"

4. MULTIPLE POSSIBLE RECEIPTS
→ choose best match
→ matched, medium
→ comment: "Best possible match among multiple similar receipts"

5. NEGATIVE / ADJUSTMENT
→ unmatched, low
→ comment: "No receipt required (adjustment/refund)"

6. MISSING RECEIPT
→ unmatched, low
→ comment: "Missing receipt for <Vendor> <Amount> attached"

7. WRONG RECEIPT
→ unmatched, low
→ comment: "Wrong receipt for <Vendor> <Amount> attached"

----------------------------------------
STRICT RULES
----------------------------------------
- EVERY transaction MUST appear in reconciliation
- If multiple issues → create multiple rows
- reconciliation_comment = the verbatim comment string from the document
- If a comment references a vendor and amount, verify they match the linked transaction
  and flag any discrepancy in the comment field as-is.
- Copy vendor name and amount exactly as written in the source document.
- If there is no issue → set comment to null

Columns (MANDATORY):
- transaction_id
- receipt_id
- match_status (matched / unmatched)
- confidence (high / medium / low)
- comment             



  
========================
Table 6:REPORT SUMMARY
========================
Two aggregate comment fields that roll up all issues found across the entire report.

Fields (MANDATORY):
- reconciliation_comment  (string)
  → One consolidated comment listing ALL receipt issues across ALL transactions.
  → Format: bullet-style list as a single string, each issue on a new line starting with "- "
  → Include: wrong receipts, missing receipts.
  → Example:
     "- Wrong receipt for Delta -$360.09 attached
      - Missing Burgers and Bourbon $57.64
  → If no issues → null

- approval_comment  (string)
  → One consolidated comment listing ALL approval issues across the report.
  → Check atleast two approvals are granted - Card holder approval and partner approval. If any of these is missing or pending, add to the comment.
  → Include: missing approvers, pending approvals, rejected steps, unapproved amounts.
  → Format: bullet-style list as a single string, each issue on a new line starting with "- "
  → Example:
     "- Approval missing from Card holder;
      - Missing Partner approval
  → If no issues → null

========================
GLOBAL RULES
========================
- Extract EVERYTHING — do not skip sections
- Extract ALL transactions from transaction table section
- Extract employee_report as a SINGLE object
- Extract ALL receipts from receipt text blocks
- Do NOT mix transactions and receipts
- Normalize vendor names (e.g., SWEETGREEN MIDTOWN → Sweetgreen)
- Some fields may be split across multiple lines; reconstruct full values
- If any field is missing → return null
- Strictly add all transactions; do not miss any; 
- 

========================
OUTPUT FORMAT (STRICT JSON ONLY)
========================
{
  "transactions": [...],
  "employee_report": {...},
  "approval_log": [...],
  "receipts": [...],
  "reconciliation": [...],
  "report_summary": {
    "reconciliation_comment": "- Wrong receipt for Delta -$360.09 attached; - Missing Burgers and Bourbon $57.64",
    "approval_comment": "- Approval missing from Card holder; - Missing Partner approval"
  } 
}
"""

# ── Shared invariants (output contract only) ──────────────────────────────
_BASE_RULES = """
OUTPUT CONTRACT (non-negotiable):
- Return ONLY raw JSON matching the schema in the user prompt.
- No markdown, no code fences, no preamble, no trailing text.
- All keys in the schema are required. Use null for missing values, never drop the key.
- Copy all names, IDs, emails, dates, and amounts verbatim from the source document.
- Do not paraphrase, normalize, or infer values unless the schema explicitly instructs it.

STRICT JSON VALIDATION:
- Output MUST be valid JSON parsable by json.loads()
- Ensure:
  - commas between all fields
  - no trailing commas
  - all strings properly escaped
  - no extra text outside JSON
  - All control characters inside strings MUST be escaped:
  newline → \\n
  tab → \\t
  carriage return → \\r
  - Raw line breaks inside string values are NOT allowed
- Before final output, internally validate JSON format
"""

# ── Stage 1: Structured table parser ─────────────────────────────────────
SYSTEM_PROMPT_TRANSACTIONS = f"""
You are a structured financial form parser specializing in corporate expense reports.

Your task is to read a Concur expense report and extract exactly three structured tables
(transactions, employee_report, approval_log) plus an approval summary field.

Mindset:
- Read the document like a data entry clerk copying a form into a database.
- Do not interpret, summarize, or infer — copy field values exactly as printed.
- If a field spans multiple lines, reconstruct the full value before copying.
- If a field is absent, return null — never fabricate or estimate.

{_BASE_RULES}
"""

# ── Stage 2: Receipt/invoice document scanner ─────────────────────────────
SYSTEM_PROMPT_RECEIPTS = f"""
You are an audit-grade document scanner specializing in financial proof extraction.

Your task is to locate and extract every receipt, invoice, email confirmation,
and financial proof block from a PDF — with zero data loss.

Mindset:
- Read the document like a forensic auditor hunting for payment evidence.
- Completeness is more important than speed — do not skip, skip, or consolidate blocks.
- Capture all amounts, labels, and metadata exactly as printed — even minor line items.
- The report section (transaction tables, summary, approval table) is not your concern — ignore it.
- Everything after the report section is financial proof — extract all of it.
- Travel itineraries, booking confirmations, and agency trip summaries ARE financial proofs — extract them even if they look like planning documents.

{_BASE_RULES}
All IDs (TID, MID, invoice numbers, reference numbers) MUST be returned as strings, even if numeric or contain leading zeros.

"""

# ── Stage 3: Transaction-to-receipt auditor ───────────────────────────────
SYSTEM_PROMPT_RECONCILIATION = f"""
You are a senior expense auditor performing transaction-to-receipt reconciliation.

Your task is to match each transaction from a Concur report to its supporting receipt,
flag discrepancies, and produce a structured reconciliation verdict.

Mindset:
- Think like an auditor defending findings to a partner — every match must be justified.
- Prefer no match over a wrong match — do not force-fit a receipt onto a transaction.
- Amount is the first gate: any mismatch disqualifies the receipt regardless of vendor or date.
- You are reasoning across two already-extracted datasets supplied in the user prompt.
  Do not re-read the PDF or invent new transactions or receipts.

{_BASE_RULES}
"""

# ═══════════════════════════════════════════════════════════════
# Staged extraction prompts
# ═══════════════════════════════════════════════════════════════

TRANSACTION_EXTRACTION_PROMPT = """
#TASK:
Extract ONLY the Concur transaction/report/approval sections from the PDF.

**You MUST extract:
1. TABLE 1: transactions
2. TABLE 2: employee_report
3. TABLE 3: approval_log
4. report_summary.approval_comment**

# TABLE 1: transactions
Extract ALL transactions exactly as shown.

Columns:
- transaction_id (create unique id like txn_1, txn_2 in document order)
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

# TABLE 2: employee_report
Extract as a SINGLE object.

Columns:
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

# TABLE 3: approval_log
Extract ALL approval workflow steps.

Columns:
- date
- approver_name
- status
- note

# REPORT SUMMARY
approval_comment:
  - Bullet string, one issue per line starting "- "
  - Check that at minimum two approvals exist: cardholder + partner
  - Flag: missing approvers, pending steps, rejected steps, unapproved amounts
  - null if approval is fully complete

reconciliation_comment:
  - Always null in this call


# Output format:
{
  "transactions": [...],
  "employee_report": {...},
  "approval_log": [...],
  "report_summary": {
    "approval_comment": null,
    "reconciliation_comment": null
  }
}
"""


RECEIPT_EXTRACTION_PROMPT = """
# TASK
Locate and extract every receipt/invoice/payment_proof block from this PDF.
Ignore the report section (transaction tables, approval log, employee summary).
Once the report section ends, treat everything that follows as a payment proof block and extract it.
Which Includes: Travel receipts, invoices, food bills, hotel folios, booking confirmations, email receipts, refunds, partial receipts, travel_itineraries with prices, etc.

# CRITICAL DOCUMENT STRUCTURE WARNING
This PDF is a MERGED document — multiple individual receipt files joined into one.
Printed page numbers RESET for each receipt. You will see "Page 1" many times.

PAGE RULES:
  - DO NOT use printed page numbers to track position, detect duplicates, or count pages.
  - Track position by sequential physical order from the start of the document.
    Physical page 1 = first page you see. Physical page 2 = second page. And so on.
  - A new receipt begins when content changes: new vendor header, new booking 
    confirmation block, new folio, new email header — NOT when page number resets to 1.
  - Two blocks both labeled "Page 1" with different content = TWO separate receipts.
    Do not deduplicate based on matching page numbers.


# MANDATORY PRE Processing STEP — PAGE INVENTORY
# There are total {page_count} physical pages in this document. Scan each page indivisually.
Before extracting anything, determine:
  - Total pages in the document
  - Which pages are report section (ignore)
  - Which pages contain receipts (extract)
  - Your estimated total receipt count (use as your completion target)

Output this in your response under "page_inventory".

# STEP 1 — PAGE-BY-PAGE SCAN AND EXTRACT
Process the document one page at a time, top-to-bottom.

For each page:
  1. Classify: report page or payment_proof page?
  2. Report page → skip, move to next.
  3. payment_proof page → extract ALL receipts/payment_proof blocks on this page before moving to the next.

Rules:
  - Do not skip any page.
  - Do not skip refund or negative receipts.
  - Do not skip receipts dated outside the report period.

# STEP 2 — EXTRACT FIELDS (MANDATORY)
For each receipt:
- receipt_id (rcp_1, rcp_2, …)
- order_id (or null)
- date
- vendor
- amount (final amount including all adjustments)
- line_items (every monetary value visible on the receipt)
- details (structured fields + any remaining data not covered)

HOTEL FOLIO RULE (CRITICAL):
  amount = the credit line charged to card (AX CARD / CCARD-AX / Deposit-AX).
  Do NOT use gross room charges or pre-tax subtotals.
  If no card credit line is visible → use the balance due line.

## LINE ITEMS
Each receipt MUST include ALL monetary amounts visible anywhere in the receipt.
Include:
- every charge, fee, tax, tip, surcharge, service fee, etc.
- subtotals, totals, base fare, discounts, refunds
- any standalone amount appearing in the receipt (even small/minor values)
Rule: if an amount is visible, it must appear here — no exceptions
Each amount must be in format: "<exact label from receipt>":"<exact amount as shown, negative for discounts/refunds>"


## DETAILS (STRUCTURED + LOSSLESS)
These are example fields to extract based on type of receipt; Extract as many fields as visible. Add fields based on what the receipt shows.
FLIGHTS - ticket_number, passenger_name, origin_airport, destination_airport, all_flight_legs, is_round_trip, charge_type, original_ticket_number, service_fee_amount;
CAR- pickup_time, dropoff_time, pickup_address, dropoff_address, ride_date, is_weekend, tip_amount, base_fare;
MEALS - attendees, guest_count, food_subtotal, entertainment_subtotal, meal_type, overage_amount;
LODGING - room_rate_total, folio_meals_total, venue_fee_total, checkin_date, checkout_date, hotel_booking_fee, card_charged_amount;
WIFI - flight_date, wifi_provider;
REFUNDS - refund_reason, original_charge_date, is_full_refund;
GROUND - transport_mode, parking_location, fuel_route

+ Any data not covered above must be captured as additional key-value pairs in details.
    Do not discard any label, value, or metadata visible in the receipt.

    Each piece of information must be: "field_name": value

# STEP 3 — ORDERING (STRICT)

Receipts must appear in strict document order: top-to-bottom, page-by-page.
No reordering, grouping, or clustering allowed.


# STEP 4 — COMPLETENESS CHECK (MANDATORY)

Before writing final output:
  1. Count receipts extracted.
  2. Compare against your page inventory estimate.
  3. If count < estimate → find the gap and add missing receipts.
  4. Confirm refund receipts are included.
  5. Confirm out-of-period receipts are included.

# OUTPUT FORMAT
{
  "page_inventory": {
    "total_pages": N,
    "report_pages": [...],
    "receipt_pages": [...],
    "expected_receipt_count": N
  },
  "receipts": [
    {
      "receipt_id": "rcp_1",
      "order_id": null,
      "date": "...",
      "vendor": "...",
      "amount": 0.00,
      "line_items": {},
      "details": {}
    }
  ]
}

# ⚠ FINAL CHECK BEFORE SUBMITTING:
  - receipt count == expected_receipt_count? If NO → find and add missing.
"""

RECONCILIATION_PROMPT = """
# Task
Match every transaction to its supporting receipt using the extracted data below.
Do not create new transaction_ids or receipt_ids.

# Rules
- Every transaction_id must appear in the output — no exceptions
- Prefer unmatched over a forced incorrect match

# EXEMPT TRANSACTIONS — evaluate FIRST, skip matching logic
The following do NOT require a receipt:
  - project = "Personal" OR business_purpose contains "Personal"
  - amount between -$25 and +$25 (absolute value < 25)
Mark these: match_status="unmatched", comment="No receipt required ({reason}: personal/low amount)"

# MATCHING LOGIC (CRITICAL):

## Step 1 — Amount match (required)
Match abs(transaction_amount) against ALL receipt fields:

PRIMARY (check first): receipt.amount, Total_Amount, "Total Amount", "Grand Total", etc
SECONDARY (also valid): Total_Fees,Service Fee,payment (any variant), Total_Tickets, Charged_amount, card_charged_amount, hotel_booking_fee,
    details['Payment method'], details['Payment method 2'],
    details['Charged to'], details['form_of_payment'],
    any details field containing a corporate card indicator
    (American Express, Amex, AX, CBCP, Visa, Mastercard + amount),
    any line_items or details entry whose amount matches
For refunds: Match against:
    - line_items entries labeled: Refund, Credit, Adjustment, Exchange Value,
      or any field with a matching positive value
    - details fields: refund_amount, credit_amount, exchange_value
    - receipt.amount (abs match)
  Sign difference alone does NOT disqualify a match.

Tolerance: ±0.05 for rounding only.
Amount mismatch beyond tolerance → receipt disqualified. Do not proceed to Gate 2.

## Step 2 — Vendor similarity (required after step 1)
## Step 3 — Date within ±2 days (supporting, not required)

# RECEIPT ID VALIDATION
  Before assigning a receipt_id:
  Verify the receipt content belongs to that id.
  Verify the matched field and amount actually exist in that receipt.
  Do not infer receipt_id from position or ordering assumptions.

# COMMENT FORMAT (per transaction)
Standard match:
  "Matched $<amount> with <receipt_id> → <field_name>: <value in receipt>"

Split payment (Type 2):
  "Split payment — corporate card $<txn_amount> of receipt total $<receipt_amount>;
   matched via <field_name> in <receipt_id>"

Refund match:
  "Refund match — transaction -$<abs_amount> matched rcp_X → 
   <field_name>: <positive_value> (sign inverted — refund transaction)"

No receipt required:
  "No receipt required (adjustment/refund)"

Missing receipt:
  "Missing receipt for <Vendor> <Amount>"

Wrong receipt:
  "Wrong receipt for <Vendor> <Amount> attached"

# SELF-VALIDATION (MANDATORY)
Before finalizing each match:
  1. Does the assigned receipt_id actually contain the field cited in comment? 
     If NO → unmatched.
  2. Does abs(matched field value) == abs(transaction_amount) within ±0.05?
     If NO → unmatched.
  Use absolute values for comparison — sign differences are expected for refunds.

# REPORT SUMMARY — reconciliation_comment
Roll up ALL wrong and missing receipt issues only.
Exclude: matched, split, partial, no-receipt-required.
Format: bullet string, one issue per line starting "- "
null if no issues.

# Output Schema:
{
  "reconciliation": [
    {
      "transaction_id": "txn_1",
      "receipt_id": "rcp_1",
      "match_status": "matched|unmatched|partial",
      "confidence": "high|medium|low",
      "comment": <extraction logic comment>
    }
  ],
  "report_summary": {
    "reconciliation_comment": null
  }
}


"""
# No receipt required only for report_summary.reconciliation_comment in the following cases:
# - Personal transactions (project = "Personal" OR business_purpose contains "Personal")
# - Transactions with amount < 25
# - Negative adjustments/refunds

CONCUR_STAGE_TRANSACTIONS = "transactions"
CONCUR_STAGE_RECEIPTS = "receipts"
CONCUR_STAGE_RECONCILIATION = "reconciliation"


# Cache helpers  (SHA-256 content-addressed, same as AMEX extractor)
def _cache_key(pdf_path: Path) -> str:
    sha = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            sha.update(chunk)
    return sha.hexdigest()


def _cache_path(pdf_path: Path, cache_dir: Path) -> Path:
    return cache_dir / f"concur__{pdf_path.stem}__{_cache_key(pdf_path)[:12]}.json"


def _load_cache(cache_file: Path) -> dict[str, Any] | None:
    """
    Load cache file with file lock error handling.
    
    Returns None on any error (file doesn't exist, corrupted, locked, etc.)
    """
    if not cache_file.exists():
        return None
    
    try:
        with open(cache_file) as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        logger.warning("concur_cache_corrupted", path=str(cache_file), error=str(exc))
        return None
    except (OSError, IOError) as exc:
        # File lock, permission denied, or other OS-level error
        if "file is in use" in str(exc).lower() or "permission denied" in str(exc).lower():
            logger.debug("concur_cache_locked", path=str(cache_file))
        else:
            logger.warning("concur_cache_read_failed", path=str(cache_file), error=str(exc))
        return None


def _write_cache(cache_file: Path, data: dict[str, Any]) -> None:
    """
    Write cache with atomic operation and file lock error handling.
    
    Failures are logged but do not propagate (cache failures don't block extraction).
    """
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: write to temp, then rename
        temp_file = cache_file.with_suffix('.tmp')
        with open(temp_file, 'w') as f:
            json.dump(data, f, indent=2)
        # Atomic rename to final location
        temp_file.replace(cache_file)
        logger.debug("concur_cache_written", path=str(cache_file))
    except (OSError, IOError) as exc:
        # File lock, permission denied, or other OS-level error
        if "file is in use" in str(exc).lower() or "permission denied" in str(exc).lower():
            logger.debug("concur_cache_write_locked", path=str(cache_file))
        else:
            logger.warning("concur_cache_write_failed", path=str(cache_file), error=str(exc))
        # Don't raise — cache failure is not fatal


import re


def _write_failed_json(
    pdf_path: Path,
    output_text: str,
    model: str,
    attempt: int,
    error: str,
    stage: str = "full",
) -> None:
    """
    Write failed JSON response to debug folder for analysis.
    
    Helps identify patterns in malformed responses.
    """
    settings = get_settings()
    failed_dir = settings.cache_dir / "failed_json"
    try:
        failed_dir.mkdir(parents=True, exist_ok=True)
        safe_stage = re.sub(r"[^A-Za-z0-9._-]+", "_", stage).strip("._-") or "full"
        filename = f"{pdf_path.stem}__{safe_stage}__attempt{attempt}_{model}__error.json"
        failed_file = failed_dir / filename
        with open(failed_file, 'w') as f:
            f.write(f"// ERROR: {error}\n")
            f.write(f"// MODEL: {model}\n")
            f.write(f"// STAGE: {stage}\n")
            f.write(f"// FILE: {pdf_path.name}\n")
            f.write(f"// ATTEMPT: {attempt}\n\n")
            f.write(output_text)
        logger.debug("failed_json_written", path=str(failed_file), error=error)
    except Exception as exc:
        logger.warning("failed_json_write_error", error=str(exc))


def _pdf_size_kb(pdf_path: Path) -> int:
    """
    Return the PDF size in whole kilobytes, with a floor of 1 KB.
    """
    try:
        return max(1, (pdf_path.stat().st_size + 1023) // 1024)
    except OSError:
        return 1


def _resolve_dynamic_max_tokens(settings: Any, pdf_path: Path) -> tuple[int, int]:
    """
    Scale max_output_tokens with PDF size, capped by settings.max_tokens_cap.
    """
    base_tokens = int(getattr(settings, "max_tokens", 16000))
    tokens_per_kb = int(getattr(settings, "max_tokens_per_kb", 50))
    max_tokens_cap = int(getattr(settings, "max_tokens_cap", 32000))
    pdf_size_kb = _pdf_size_kb(pdf_path)
    dynamic_max_tokens = min(base_tokens + (pdf_size_kb * tokens_per_kb), max_tokens_cap)
    return dynamic_max_tokens, pdf_size_kb


def _dedupe_models(models: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for model in models:
        if not model or model in seen:
            continue
        seen.add(model)
        result.append(model)
    return result


def _stage_model_list(settings: Any, stage: str) -> list[str]:
    """
    Build a per-stage model list while preserving the global fallbacks.
    """
    global_models = [
        getattr(settings, "azure_openai_model", ""),
        getattr(settings, "azure_openai_model1", ""),
        getattr(settings, "azure_openai_model2", ""),
    ]

    if stage == CONCUR_STAGE_TRANSACTIONS:
        primary = (
            getattr(settings, "azure_openai_concur_transaction_model", "")
            or global_models[0]
        )
    elif stage == CONCUR_STAGE_RECEIPTS:
        primary = (
            getattr(settings, "azure_openai_concur_receipt_model", "")
            or global_models[1]
            or global_models[0]
        )
    elif stage == CONCUR_STAGE_RECONCILIATION:
        primary = (
            getattr(settings, "azure_openai_concur_reconciliation_model", "")
            or global_models[2]
            or global_models[1]
            or global_models[0]
        )
    else:
        primary = global_models[0]

    return _dedupe_models([primary, *global_models])


def _validate_stage_payload(raw_dict: dict[str, Any], stage: str) -> None:
    """
    Validate a partial Concur extraction payload for one staged LLM call.
    """
    if not isinstance(raw_dict, dict):
        raise TypeError(f"{stage} response must be a JSON object")

    if stage == CONCUR_STAGE_TRANSACTIONS:
        required = ("transactions", "employee_report", "approval_log")
        payload = {
            "transactions": raw_dict.get("transactions", []),
            "employee_report": raw_dict.get("employee_report") or {},
            "approval_log": raw_dict.get("approval_log", []),
            "report_summary": raw_dict.get("report_summary") or {},
        }
    elif stage == CONCUR_STAGE_RECEIPTS:
        required = ("receipts",)
        payload = {"receipts": raw_dict.get("receipts", [])}
    elif stage == CONCUR_STAGE_RECONCILIATION:
        required = ("reconciliation",)
        payload = {
            "reconciliation": raw_dict.get("reconciliation", []),
            "report_summary": raw_dict.get("report_summary") or {},
        }
    else:
        required = ()
        payload = raw_dict

    missing = [key for key in required if key not in raw_dict]
    if missing:
        raise ValueError(f"{stage} response missing required key(s): {', '.join(missing)}")

    ConcurRecord.model_validate(payload)


def _merge_report_summary(*summaries: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for summary in summaries:
        if not isinstance(summary, dict):
            continue
        for key, value in summary.items():
            if value not in (None, "", "null", "NULL") or key not in merged:
                merged[key] = value
    return merged


def _merge_concur_stage_payloads(
    transaction_payload: dict[str, Any],
    receipt_payload: dict[str, Any],
    reconciliation_payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "transactions": transaction_payload.get("transactions", []),
        "employee_report": transaction_payload.get("employee_report") or {},
        "approval_log": transaction_payload.get("approval_log", []),
        "receipts": receipt_payload.get("receipts", []),
        "page_inventory": receipt_payload.get("page_inventory", {}),
        "reconciliation": reconciliation_payload.get("reconciliation", []),
        "report_summary": _merge_report_summary(
            transaction_payload.get("report_summary"),
            reconciliation_payload.get("report_summary"),
        ),
    }


def _build_reconciliation_prompt(
    transaction_payload: dict[str, Any],
    receipt_payload: dict[str, Any],
) -> str:
    context = {
        "transactions": transaction_payload.get("transactions", []),
        # "employee_report": transaction_payload.get("employee_report") or {},
        # "approval_log": transaction_payload.get("approval_log", []),
        "\n\n\n receipts": receipt_payload.get("receipts", []),
    }
    return (
        RECONCILIATION_PROMPT + 
        "\n # EXTRACTED INPUT DATA FOR RECONCILIATION:\n"
        + json.dumps(context, ensure_ascii=False, indent=2)
    )

# ---------------------------------------------------
# 🔹 Escape + sanitize string values (CRITICAL FIX)
# ---------------------------------------------------
def _escape_string_values(raw: str) -> str:
    result = []
    in_string = False
    i = 0

    while i < len(raw):
        ch = raw[i]

        if in_string:
            if ch == '\\':
                result.append(ch)
                i += 1
                if i < len(raw):
                    result.append(raw[i])
                    i += 1
                continue

            elif ch == '"':
                j = i + 1
                while j < len(raw) and raw[j] in ' \t\r\n':
                    j += 1
                if j < len(raw) and raw[j] in ':,}]':
                    in_string = False
                    result.append(ch)
                else:
                    result.append('\\"')
                i += 1
                continue

            elif ch == '\n':
                result.append('\\n'); i += 1; continue
            elif ch == '\r':
                result.append('\\r'); i += 1; continue
            elif ch == '\t':
                result.append('\\t'); i += 1; continue
            elif ord(ch) < 0x20:
                i += 1; continue

        else:
            if ch == '"':
                in_string = True

        result.append(ch)
        i += 1

    return ''.join(result)


# ---------------------------------------------------
# 🔹 Normalize invalid numbers (NEW FIX)
# ---------------------------------------------------
def _fix_invalid_numbers(raw: str) -> str:
    # Fix numbers like 1,056.52 → 1056.52
    raw = re.sub(r'(?<=:\s)(\d{1,3}(,\d{3})+(\.\d+)?)', 
                 lambda m: m.group(0).replace(',', ''), raw)

    # Fix leading zero numbers (TID etc.)
    raw = re.sub(r'(?<=:\s)0+(\d+)', r'"\1"', raw)

    return raw


# ---------------------------------------------------
# 🔹 Core Repair Function (MERGED + IMPROVED)
# ---------------------------------------------------
def _repair_json(raw: str) -> str:

    # 1. Remove comments
    raw = re.sub(r'//[^\n]*', '', raw)
    raw = re.sub(r'/\*.*?\*/', '', raw, flags=re.DOTALL)

    # 2. Fix invalid numbers
    raw = _fix_invalid_numbers(raw)

    # 3. Escape string issues
    try:
        raw = _escape_string_values(raw)
    except Exception as e:
        logger.warning("escape_failed", error=str(e))

    # 4. Add missing commas
    raw = re.sub(r'"\s+(?=["{\[])', '",', raw)

    # 5. Remove trailing commas
    raw = re.sub(r',(\s*[\]}])', r'\1', raw)

    # 6. Close unterminated strings
    if len(re.findall(r'(?<!\\)"', raw)) % 2 == 1:
        raw = raw.rstrip()
        if not raw.endswith('"'):
            raw += '"'

    # 7. Balance braces/brackets (stack-based)
    stack = []
    for ch in raw:
        if ch in '{[':
            stack.append(ch)
        elif ch == '}' and stack and stack[-1] == '{':
            stack.pop()
        elif ch == ']' and stack and stack[-1] == '[':
            stack.pop()

    closing = ''.join(']' if c == '[' else '}' for c in reversed(stack))
    raw += closing

    return raw


# ---------------------------------------------------
# 🔹 Debug logging helper
# ---------------------------------------------------
def _log_parse_error(raw: str, exc: json.JSONDecodeError, stage: str):
    pos = exc.pos
    logger.warning(
        "json_parse_error",
        stage=stage,
        line=exc.lineno,
        col=exc.colno,
        msg=exc.msg,
        context_before=raw[max(0, pos-120):pos],
        context_after=raw[pos:pos+120]
    )


# ---------------------------------------------------
# 🔹 MAIN FUNCTION (FINAL PIPELINE)
# ---------------------------------------------------
def _clean_and_parse_json(raw: str) -> dict:
    raw = raw.strip()

    # -------------------------------
    # Stage 1: Remove markdown
    # -------------------------------
    if raw.startswith("```"):
        parts = raw.split("```")
        if len(parts) >= 2:
            raw = parts[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
    if raw.endswith("```"):
        raw = raw[:-3].strip()

    # -------------------------------
    # Stage 2: Extract JSON object
    # -------------------------------
    brace_pos = raw.find('{')
    if brace_pos >= 0:
        depth, in_string, escape = 0, False, False
        for i in range(brace_pos, len(raw)):
            ch = raw[i]

            if escape:
                escape = False
                continue
            if ch == '\\':
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue

            if not in_string:
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        raw = raw[brace_pos:i+1]
                        break

    # -------------------------------
    # Stage 3: Direct parse
    # -------------------------------
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e1:
        _log_parse_error(raw, e1, "initial")

    # -------------------------------
    # Stage 4: Custom repair
    # -------------------------------
    try:
        repaired = _repair_json(raw)
        return json.loads(repaired)
    except json.JSONDecodeError as e2:
        _log_parse_error(repaired, e2, "after_custom_repair")

    # -------------------------------
    # Stage 5: json_repair fallback
    # -------------------------------
    try:
        from json_repair import repair_json
        recovered = repair_json(raw, return_objects=True)
        if isinstance(recovered, dict):
            logger.warning("json_repair_library_success")
            return recovered
    except Exception as e:
        logger.error("json_repair_library_failed", error=str(e))

    raise ValueError("Failed to parse JSON after multiple repair attempts")

def _parse_response_text(raw: str) -> dict:
    """
    Parse API response text through 3-stage JSON cleaning pipeline.
    
    Handles:
    - Markdown code fences
    - Unterminated strings
    - Trailing commas
    - Unbalanced braces
    - Preamble/trailing text
    """
    return _clean_and_parse_json(raw)


# ═══════════════════════════════════════════════════════════════
# Retry configuration (singleton, built once at module load)
# ═══════════════════════════════════════════════════════════════

def _build_retry_decorator() -> callable:
    """
    Build a retry decorator that handles OpenAI API errors.
    Built once at module load, shared across all API calls.
    """
    return retry(
        retry=retry_if_exception_type(OpenAIError),
        stop=stop_after_attempt(3),  # 3 attempts per model
        wait=wait_exponential(multiplier=2, min=2, max=60),  # Exponential backoff
        before_sleep=before_sleep_log(logging.getLogger(__name__), logging.WARNING),
        reraise=True,
    )


_RETRY_DECORATOR = _build_retry_decorator()


# ═══════════════════════════════════════════════════════════════
# API calls with model fallback
# ═══════════════════════════════════════════════════════════════

def _call_api(
    client: OpenAI,
    file_id: str,
    model: str,
    filename: str,
    max_tokens: int,
    USER_PROMPT: str = EXTRACTION_PROMPT,
    SYSTEM_PROMPT: str = SYSTEM_PROMPT_,
    stage: str = "full",
    include_file: bool = True,
):
    """
    OpenAI Responses API call with single model, streamed incrementally.
    
    Returns:
        (raw_text, final_response)
    Decorated with retry logic at call site.
    """
    text_chunks: list[str] = []
    running_text = ""
    event_types: list[str] = []
    incomplete_response: Any | None = None
    incomplete_reason: str | None = None
    user_content = []

    
    # ✅ Conditional file inclusion
    if include_file and file_id:
        user_content.append({
            "type": "input_file",
            "file_id": file_id
        })

    # Always add prompt
    user_content.append({
        "type": "input_text",
        "text": USER_PROMPT
    })

    try:
        with client.responses.stream(
            model=model,
            # max_output_tokens=max_tokens,
            # temperature=0.3,
            # top_p=1.0,
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
        ) as stream:
            for event in stream:
                event_types.append(getattr(event, "type", type(event).__name__))

                if event.type == "response.incomplete":
                    incomplete_response = getattr(event, "response", None)
                    incomplete_details = getattr(incomplete_response, "incomplete_details", None)
                    incomplete_reason = getattr(incomplete_details, "reason", None)
                    logger.warning(
                        "stream_incomplete_event",
                        filename=filename,
                        model=model,
                        stage=stage,
                        event_count=len(event_types),
                        reason=incomplete_reason,
                    )
                    continue

                if event.type != "response.output_text.delta":
                    continue

                delta = getattr(event, "delta", "") or ""
                if not delta:
                    continue

                text_chunks.append(delta)
                running_text += delta

                lowered = running_text.lower()
                if any(phrase in lowered for phrase in REFUSAL_PHRASES):
                    detail_file = _write_stream_error_detail(
                        filename=filename,
                        model=model,
                        file_id=file_id,
                        event_types=event_types,
                        raw_text=running_text,
                        error="content_filter_refusal",
                    )
                    logger.warning(
                        "stream_refusal_detected",
                        filename=filename,
                        model=model,
                        stage=stage,
                        event_count=len(event_types),
                        event_tail=event_types[-8:],
                        detail_file=str(detail_file) if detail_file else None,
                        preview=running_text[-500:],
                    )
                    raise ContentFilterRefusalError(
                        f"Content filter triggered mid-stream for {filename!r}. "
                        f"Events seen: {len(event_types)} events. "
                        f"Preview: {running_text[-200:]}"
                    )

            try:
                final_response = stream.get_final_response()
            except RuntimeError as exc:
                raw_text = "".join(text_chunks).strip()
                if incomplete_response is not None and raw_text:
                    if incomplete_reason == "content_filter":
                        raise ContentFilterRefusalError(
                            f"Content filter triggered mid-stream for {filename!r}."
                        ) from exc

                    final_response = incomplete_response
                    detail_file = _write_stream_error_detail(
                        filename=filename,
                        model=model,
                        file_id=file_id,
                        event_types=event_types,
                        raw_text=raw_text,
                        error=f"incomplete:{incomplete_reason or 'unknown'}",
                    )
                    logger.warning(
                        "stream_incomplete_recovered",
                        filename=filename,
                        model=model,
                        stage=stage,
                        file_id=file_id,
                        event_count=len(event_types),
                        event_tail=event_types[-8:],
                        detail_file=str(detail_file) if detail_file else None,
                        delta_chars=len(raw_text),
                        reason=incomplete_reason,
                        preview=raw_text[-500:],
                    )
                else:
                    detail_file = _write_stream_error_detail(
                        filename=filename,
                        model=model,
                        file_id=file_id,
                        event_types=event_types,
                        raw_text=raw_text,
                        error=str(exc),
                    )
                    logger.error(
                        "stream_completion_missing",
                        filename=filename,
                        model=model,
                        stage=stage,
                        file_id=file_id,
                        event_count=len(event_types),
                        event_tail=event_types[-8:],
                        detail_file=str(detail_file) if detail_file else None,
                        delta_chars=len(raw_text),
                        preview=raw_text[-500:],
                        error=str(exc),
                        exc_info=True,
                    )
                    raise StreamCompletionMissingError(
                        "Stream ended without response.completed "
                        f"(file={filename!r}, model={model!r}, "
                        f"events_seen={len(event_types)}, "
                        f"last_event={event_types[-1] if event_types else None}, "
                        f"preview={raw_text[-200:]!r})"
                    ) from exc
    except ContentFilterRefusalError:
        raise
    except StreamCompletionMissingError:
        raise
    except Exception as exc:
        if isinstance(exc, RuntimeError) and str(exc).startswith("Stream ended without response.completed"):
            raise
        raw_text = "".join(text_chunks).strip()
        detail_file = _write_stream_error_detail(
            filename=filename,
            model=model,
            file_id=file_id,
            event_types=event_types,
            raw_text=raw_text,
            error=str(exc),
        )
        logger.error(
            "stream_unexpected_error",
            filename=filename,
            model=model,
            stage=stage,
            file_id=file_id,
            event_count=len(event_types),
            event_tail=event_types[-8:],
            detail_file=str(detail_file) if detail_file else None,
            delta_chars=len(raw_text),
            preview=raw_text[-500:],
            error=str(exc),
            exc_info=True,
        )
        raise

    raw_text = "".join(text_chunks).strip()
    if not raw_text:
        raise ValueError(f"API returned empty streamed response for {filename!r}.")

    response_status = getattr(final_response, "status", None)
    incomplete_details = getattr(final_response, "incomplete_details", None)
    incomplete_reason = getattr(incomplete_details, "reason", None)
    if response_status == "incomplete" and incomplete_reason == "content_filter":
        raise ContentFilterRefusalError(
            f"Content filter triggered for {filename!r} on model {model!r}."
        )

    finish_reason = getattr(final_response, "finish_reason", None)
    if finish_reason == "length" or response_status == "incomplete":
        logger.warning(
            "response_incomplete_retained",
            filename=filename,
            model=model,
            stage=stage,
            status=response_status,
            reason=incomplete_reason,
        )

    return raw_text, final_response


def _call_with_model_fallback(
    client: OpenAI,
    file_id: str,
    models: list[str],
    filename: str,
    pdf_path: Path,
    USER_PROMPT: str = EXTRACTION_PROMPT,
    SYSTEM_PROMPT: str = SYSTEM_PROMPT_,
    stage: str = "full",
    include_file: bool = True,
) -> tuple[dict, str, int, int]:
    """
    Try extracting JSON from API response with model fallback.
    
    Attempts to:
    1. Call API with primary model (retry up to 3 times)
    2. Parse JSON response through 3-stage cleaning
    3. Validate with Pydantic schema
    
    If ANY error occurs (API, JSON parse, validation), try next model.
    
    Args:
        client: OpenAI client
        file_id: Uploaded PDF file id
        models: List of model names to try [primary, fallback1, fallback2]
        filename: PDF filename for API
        pdf_path: PDF path for logging
        prompt: Stage-specific extraction prompt
        stage: Logical extraction stage for logging and partial validation
    
    Returns:
        (raw_dict, model_used, input_tokens, output_tokens)
    
    Raises:
        ValueError: All models failed
    """
    settings = get_settings()
    dynamic_max_tokens, pdf_size_kb = _resolve_dynamic_max_tokens(settings, pdf_path)
    log = logger.bind(
        pdf=pdf_path.name,
        stage=stage,
        max_tokens=dynamic_max_tokens,
        pdf_size_kb=pdf_size_kb,
    )
    if dynamic_max_tokens >= int(getattr(settings, "max_tokens_cap", 32000)):
        log.warning(
            "concur_max_tokens_capped",
            pdf_size_kb=pdf_size_kb,
            max_tokens=dynamic_max_tokens,
        )
    
    for attempt_idx, model in enumerate(models, 1):
        if not model:  # Skip empty fallback slots
            log.debug("model_fallback_slot_empty", attempt=attempt_idx, model="<empty>")
            continue
        
        log.info("model_attempt_start", attempt=attempt_idx, model=model)
        
        try:
            # Apply retry decorator to this API call
            api_call_with_retry = _RETRY_DECORATOR(_call_api)
            
            # Call API and get response
            output_text, response = api_call_with_retry(
                client,
                file_id,
                model,
                filename,
                dynamic_max_tokens,
                USER_PROMPT,
                SYSTEM_PROMPT,
                stage,
                include_file
            )
            
            # Extract tokens
            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "input_tokens", 0) if usage else 0
            output_tokens = getattr(usage, "output_tokens", 0) if usage else 0
            
            # Parse JSON response
            # Stage 1-3 JSON cleaning and parsing
            try:
                raw_dict: dict[str, Any] = _clean_and_parse_json(output_text)
            except (json.JSONDecodeError, AttributeError) as exc:
                # Write failed JSON to debug folder before moving to next model
                _write_failed_json(pdf_path, output_text, model, attempt_idx, str(exc), stage)
                log.warning(
                    "model_attempt_json_invalid",
                    attempt=attempt_idx,
                    model=model,
                    error=str(exc),
                )
                continue  # Try next model
            
            # Validate with Pydantic
            try:
                _validate_stage_payload(raw_dict, stage)
            except Exception as exc:
                log.warning(
                    "model_attempt_validation_failed",
                    attempt=attempt_idx,
                    model=model,
                    error=str(exc),
                )
                continue  # Try next model
            
            # Success!
            log.info(
                "model_attempt_succeeded",
                attempt=attempt_idx,
                model=model,
                tokens_in=input_tokens,
                tokens_out=output_tokens,
            )
            return raw_dict, model, input_tokens, output_tokens
        
        except ContentFilterRefusalError as exc:
            # Content filter detected mid-response — try next model
            log.warning(
                "model_attempt_content_filter_error",
                attempt=attempt_idx,
                model=model,
                error=str(exc),
            )
            continue  # Try next model
        
        except OpenAIError as exc:
            status_code = getattr(exc, "status_code", None)
            
            # For 500 server errors, sleep before retrying
            if status_code == 500:
                log.warning(
                    "model_attempt_500_error_sleeping",
                    attempt=attempt_idx,
                    model=model,
                    error=str(exc),
                    sleep_seconds=10,
                )
                time.sleep(10)  # Wait 10 seconds before trying next model
            
            log.warning(
                "model_attempt_failed",
                attempt=attempt_idx,
                model=model,
                error=str(exc),
                status_code=status_code,
            )
            continue  # Try next model
        
        except Exception as exc:
            log.warning(
                "model_attempt_unexpected_error",
                attempt=attempt_idx,
                model=model,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            continue  # Try next model
    
    # All models exhausted
    log.error("all_models_failed", tried_models=models)
    raise ValueError(
        f"All {len([m for m in models if m])} models failed for {pdf_path.name} "
        f"during {stage} extraction"
    )


# ═══════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════

def extract_concur_record(pdf_path: Path, no_cache: bool = False) -> tuple[ConcurRecord, LLMMetrics]:
    """
    Extract and validate all 5 tables from a Concur expense report PDF.
    
    Features:
    - Model fallback (primary + 2 fallback models)
    - 3-stage JSON repair pipeline
    - SHA-256 content-addressed cache
    - Comprehensive error logging
    - File lock error handling

    Args:
        pdf_path: Path to Concur PDF file
        no_cache: If True, skip cache read and always rewrite cache

    Returns:
        (ConcurRecord, LLMMetrics)  — record for reconciliation,
                                      metrics for cost/token logging.

    Steps:
      1. Cache check (SHA-256 content-addressed) — skipped if no_cache=True
      2. Upload PDF once for staged Responses API calls
      3. Call OpenAI Responses API with staged model fallback
      4. Parse JSON through 3-stage pipeline
      5. Validate with Pydantic ConcurRecord model
      6. Write cache (with file lock error handling)
      7. Build and record LLMMetrics

    Raises:
        FileNotFoundError  — pdf_path does not exist
        ValueError — All models failed or malformed JSON
        pydantic.ValidationError — extracted data fails schema
    """
    settings = get_settings()

    if not pdf_path.exists():
        raise FileNotFoundError(f"Concur PDF not found: {pdf_path}")

    log = logger.bind(pdf=pdf_path.name)
    log.info("concur_extraction_start")

    # ── 1. Cache check ────────────────────────────────────────────────────────
    if not no_cache and settings.cache_enabled:
        cp = _cache_path(pdf_path, settings.cache_dir)
        cached = _load_cache(cp)
        if cached is not None:
            try:
                # Validate cached data against schema
                record = ConcurRecord.model_validate(cached)
                log.info("concur_cache_hit", cache_file=cp.name)
                dummy = build_metrics(
                    pdf_file=pdf_path.name,
                    model=settings.azure_openai_model,
                    input_tokens=0, output_tokens=0,
                    cost_per_1k_input=settings.cost_per_1k_input,
                    cost_per_1k_output=settings.cost_per_1k_output,
                    latency=0.0, status="cache_hit",
                )
                return record, dummy
            except Exception as exc:
                log.warning("concur_cache_validation_failed", error=str(exc))
                # Cache is stale/corrupt, rebuild from API below

    # ── 2. Encode PDF ─────────────────────────────────────────────────────────
    log.debug("preparing_pdf_upload")

    # ── 3. API call with model fallback ───────────────────────────────────────
    # Set up httpx timeout for large PDF processing
    timeout_config = httpx.Timeout(
        timeout=settings.api_timeout_seconds,
        connect=60,                              # 60s to establish connection
        read=settings.api_timeout_seconds,       # Full timeout for reading response
        write=30,                                # 30s to send data
        pool=10,                                 # 10s to acquire connection from pool
    )

    def _upload_file(client: OpenAI, pdf_path: Path) -> str:
        """
        Upload PDF to OpenAI Files API and return file_id
        """
        with open(pdf_path, "rb") as f:
            uploaded = client.files.create(
                file=f,
                purpose="assistants"   # required
            )
        return uploaded.id
    
    import fitz  # PyMuPDF

    def get_pdf_page_count_fast(pdf_path: Path) -> int:
        with fitz.open(pdf_path) as doc:
            return doc.page_count

    client = OpenAI(
        api_key=settings.azure_openai_api_key,
        base_url=settings.azure_openai_base_url,
        max_retries=0,  # We handle retries explicitly with tenacity
        # http_client=httpx.Client(timeout=timeout_config),
    )
    
    transaction_models = _stage_model_list(settings, CONCUR_STAGE_TRANSACTIONS)
    receipt_models = _stage_model_list(settings, CONCUR_STAGE_RECEIPTS)
    reconciliation_models = _stage_model_list(settings, CONCUR_STAGE_RECONCILIATION)

    log.info(
        "concur_stage_models_selected",
        transaction_models=transaction_models,
        receipt_models=receipt_models,
        reconciliation_models=reconciliation_models,
    )

    file_id: str | None = None
    with MetricsTimer() as timer:
        try:
            # ── Upload file ─────────────────────────────
            file_id = _upload_file(client, pdf_path)
            page_count = get_pdf_page_count_fast(pdf_path)  # Log page count for debugging and potential future use
            print(f"DEBUG: PDF page count for {pdf_path.name}: {page_count}")

            log.info("file_uploaded", file_id=file_id, page_count=page_count)

            transaction_payload, transaction_model, txn_input_tokens, txn_output_tokens = _call_with_model_fallback(
                client=client,
                file_id=file_id,
                models=transaction_models,
                filename=pdf_path.name,
                pdf_path=pdf_path,                
                stage=CONCUR_STAGE_TRANSACTIONS,
                USER_PROMPT=TRANSACTION_EXTRACTION_PROMPT,
                SYSTEM_PROMPT=SYSTEM_PROMPT_TRANSACTIONS,
                include_file=True,  # Only include file for first stage to save tokens
            )

            receipt_payload, receipt_model, receipt_input_tokens, receipt_output_tokens = _call_with_model_fallback(
                client=client,
                file_id=file_id,
                models=receipt_models,
                filename=pdf_path.name,
                pdf_path=pdf_path,
                stage=CONCUR_STAGE_RECEIPTS,
                USER_PROMPT=RECEIPT_EXTRACTION_PROMPT.replace("{page_count}", str(page_count)),
                SYSTEM_PROMPT=SYSTEM_PROMPT_RECEIPTS,
                include_file=True,  
            )


            
            user_reconciliation_prompt = _build_reconciliation_prompt(transaction_payload, receipt_payload)
            reconciliation_payload, reconciliation_model, recon_input_tokens, recon_output_tokens = _call_with_model_fallback(
                client=client,
                file_id=file_id,
                models=reconciliation_models,
                filename=pdf_path.name,
                pdf_path=pdf_path,
                stage=CONCUR_STAGE_RECONCILIATION,
                SYSTEM_PROMPT=SYSTEM_PROMPT_RECONCILIATION,
                USER_PROMPT=user_reconciliation_prompt,
                include_file=False,  # Don't include file again for reconciliation stage
            )

            raw_dict = _merge_concur_stage_payloads(
                transaction_payload,
                receipt_payload,
                reconciliation_payload,
            )
            model_used = (
                f"{CONCUR_STAGE_TRANSACTIONS}:{transaction_model}; "
                f"{CONCUR_STAGE_RECEIPTS}:{receipt_model}; "
                f"{CONCUR_STAGE_RECONCILIATION}:{reconciliation_model}"
            )
            input_tokens = txn_input_tokens + receipt_input_tokens + recon_input_tokens
            output_tokens = txn_output_tokens + receipt_output_tokens + recon_output_tokens

        except (ValueError, OpenAIError) as exc:
            METRICS.api_failures.inc()
            metrics = build_metrics(
                pdf_file=pdf_path.name,
                model=settings.azure_openai_model,
                input_tokens=0, output_tokens=0,
                cost_per_1k_input=settings.cost_per_1k_input,
                cost_per_1k_output=settings.cost_per_1k_output,
                latency=timer.elapsed, status="error",
                error_message=str(exc),
            )
            record_metrics(metrics)
            log.error("concur_all_models_failed", error=str(exc))
            raise
        finally:
            # ✅ ALWAYS DELETE FILE FROM OPENAI
            if file_id:
                try:
                    client.files.delete(file_id)
                    log.info("file_deleted", file_id=file_id)
                except Exception as exc:
                    log.warning("file_delete_failed", file_id=file_id, error=str(exc))

    # ── 4. Validate ───────────────────────────────────────────────────────────
    record = ConcurRecord.model_validate(raw_dict)

    # ── 5. Cache write with file lock error handling ──────────────────────────
    # When no_cache=True, always write cache even if cache_enabled=False
    if no_cache or settings.cache_enabled:
        cp = _cache_path(pdf_path, settings.cache_dir)
        try:
            _write_cache(cp, raw_dict)
            if no_cache:
                log.info("concur_cache_rewritten", cache_file=cp.name)
        except OSError as exc:
            # File lock error (concurrent writes) — log but don't fail extraction
            if "file is in use" in str(exc).lower() or "permission denied" in str(exc).lower():
                log.warning("concur_cache_write_locked", cache_file=str(cp), error=str(exc))
            else:
                log.warning("concur_cache_write_failed", cache_file=str(cp), error=str(exc))
            # Continue anyway — cache miss on next run is acceptable

    # ── 6. Build and record LLMMetrics ────────────────────────────────────────
    metrics = build_metrics(
        pdf_file=pdf_path.name,
        model=model_used,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_per_1k_input=settings.cost_per_1k_input,
        cost_per_1k_output=settings.cost_per_1k_output,
        latency=timer.elapsed,
    )
    record_metrics(metrics)
    METRICS.files_processed.inc()

    log.info(
        "concur_extraction_complete",
        name=record.cardholder_name,
        amount=record.amount_submitted,
        transactions=len(record.transactions),
        receipts=len(record.receipts),
        matched=record.matched_count,
        unmatched=record.unmatched_count,
        tokens_in=input_tokens,
        tokens_out=output_tokens,
        model_used=model_used,
        cost_usd=round(metrics.cost_usd, 4),
        latency_s=timer.elapsed,
        page_count=page_count,
    )

    return record, metrics

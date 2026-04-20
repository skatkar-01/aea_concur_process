# AmEx Scrubber - Output Column Structure (FIXED)

## Data Columns (1-15) - AMEX_ALL_HEADERS
These remain consistent across all sheets.

| Col | Letter | Header | Field |
|-----|--------|--------|-------|
| 1 | A | Employee First Name | employee_first_name |
| 2 | B | Employee Middle Name | employee_middle_name |
| 3 | C | Employee Last Name | employee_last_name |
| 4 | D | Blank/Placeholder | blank_placeholder |
| 5 | E | Report Entry Transaction Date | transaction_date |
| 6 | F | Report Entry Description | description |
| 7 | G | Journal Amount | amount |
| 8 | H | Report Entry Payment Type Name | pay_type |
| 9 | I | Report Entry Expense Type Name | expense_code |
| 10 | J | Report Entry Vendor Description | vendor_desc |
| 11 | K | Report Entry Vendor Name | vendor |
| 12 | L | Project | project |
| 13 | M | Cost Center | cost_center |
| 14 | N | Report Purpose | report_purpose |
| **15** | **O** | **Employee ID** | **employee_id** |

## Metadata Columns (16-17)
| Col | Letter | Header | Purpose |
|-----|--------|--------|---------|
| 16 | P | **Note** | Shows scrubbing notes (if any) |
| 17 | Q | LEN | Formula calculating description+vendor length +12 |

## Debug Columns (18+) - When --debug-memory is enabled

### Memory Match Columns (18-20)
| Col | Letter | Header | Purpose |
|-----|--------|--------|---------|
| 18 | R | Memory File | Historical file matched (if found) |
| 19 | S | Memory Txn ID | Transaction ID from memory |
| 20 | T | Memory Receipt ID | Receipt ID from memory |

### LLM Debug Columns (21-30)
| Col | Letter | Header | Purpose |
|-----|--------|--------|---------|
| 21 | U | LLM Transaction Type | flight, refund, meal, etc. |
| 22 | V | LLM Formatted Description | Description formatted by LLM |
| 23 | W | LLM Description Changed | Whether description was modified |
| 24 | X | LLM Expense Code | Validated expense code |
| 25 | Y | LLM Expense Code Changed | Whether expense code was modified |
| 26 | Z | LLM Confidence | Confidence score (0.0-1.0) |
| 27 | AA | LLM Reasoning | Why changes were made |
| 28 | AB | LLM Flags | Issues requiring human review |
| 29 | AC | LLM Is Refund | Whether transaction is refund |
| 30 | AD | LLM Error | Error message (if any) |

## FIXED ISSUES

✅ **Employee ID Column Mismatch (CRITICAL FIX)**
- **Was:** Employee ID was being overwritten by note column (both set to column 15)
- **Fixed:** Employee ID correctly placed in column O (15), Note column moved to P (16)

✅ **Missing Column P Header (MAJOR FIX)**
- **Was:** Column P (Note) had no header label, was set to None
- **Fixed:** Column P now has header "Note" with bold formatting

✅ **Headers Not Bold in All Sheets (STYLING FIX)**
- **Was:** Debug column headers were bold, but not consistently applied to all sheets
- **Fixed:** All headers now made bold via `_style_sheet_headers()` called for all sheets

✅ **Column Separation**
- **Was:** Debug columns were packed/overlapping with data columns
- **Fixed:** Proper separation with note/len columns (16-17) before debug columns (18+)

## Usage

```bash
# Without debug columns (clean output)
python src/main.py --input myfile.xlsx --memory-folder ./inputs

# With debug columns for analysis
python src/main.py --input myfile.xlsx --memory-folder ./inputs --debug-memory
```

## Output File Structure

**AmEx All Sheet:**
- Columns A-O (1-15): Core transaction data
- Column P (16): Notes
- Column Q (17): Length formula
- Columns R+ (18+): Debug info (if --debug-memory enabled)

**Transaction Sheets:**
- Original columns preserved with changed cells highlighted in orange
- Same note/len/debug columns appended with consistent numbering

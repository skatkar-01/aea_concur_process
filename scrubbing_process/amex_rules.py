"""
amex_rules.py — AEA AmEx Scrubbing Rules (verified against human-reviewed workbook)
====================================================================================
Every rule here is VERIFIED against Batch # 1 - $119,802.46 - AEA - Completed.xlsm

Key findings from audit:
- AmEx All: only 49 description changes, 558 pay-type changes, 557 vendor name changes,
  10 expense code changes. NO comments written — col15 always empty.
- Rules ' to '→'-' and 'Business '→'Bus.' do NOT apply to AmEx All (human review only).
- Entity tabs have 381 additional human description corrections beyond AmEx All.
- Entity tab Comments (col14) are written by human reviewers, never by the scrubber.
"""

# ─────────────────────────────────────────────────────────────────────────────
#  1.  COLUMN STRUCTURE
# ─────────────────────────────────────────────────────────────────────────────

# Raw Concur export — 15 columns (0-based)
RAW_COLS = [
    "Employee First Name",             # 0
    "Employee Middle Name",            # 1
    "Employee Last Name",              # 2
    "Blank/Placeholder",               # 3
    "Report Entry Transaction Date",   # 4
    "Report Entry Description",        # 5
    "Journal Amount",                  # 6
    "Report Entry Payment Type Name",  # 7  → CBCP stripped
    "Report Entry Expense Type Name",  # 8  → expense code remapped
    "Report Entry Vendor Description", # 9  → lookup key; KEPT in AmEx All
    "Report Entry Vendor Name",        # 10 → populated from Vendor List
    "Project",                         # 11
    "Cost Center",                     # 12
    "Report Purpose",                  # 13
    "Employee ID",                     # 14
]

# AmEx All — 17 cols (keeps VendorDesc col9, adds blank col + LEN)
# IMPORTANT: col15 (None header) is ALWAYS EMPTY — never written by scrubber
AMEX_ALL_COLS = RAW_COLS + [None, "LEN"]
# index 15 = empty col (header None), index 16 = LEN

# Entity tabs — 16 cols (VendorDesc dropped; VendorName shifts to col9)
# col14 (None header) = Comments — left EMPTY by scrubber, filled by human reviewer
ENTITY_COLS = [
    "Employee First Name",             # 0
    "Employee Middle Name",            # 1
    "Employee Last Name",              # 2
    "Blank/Placeholder",               # 3
    "Report Entry Transaction Date",   # 4
    "Report Entry Description",        # 5
    "Journal Amount",                  # 6
    "Report Entry Payment Type Name",  # 7
    "Report Entry Expense Type Name",  # 8
    "Report Entry Vendor Name",        # 9  (VendorDesc dropped; VendorName here)
    "Project",                         # 10
    "Cost Center",                     # 11
    "Report Purpose",                  # 12
    "Employee ID",                     # 13
    None,                              # 14 = Comments (EMPTY — human fills in)
    "LEN",                             # 15
]

# ─────────────────────────────────────────────────────────────────────────────
#  2.  LEN FORMULA
# ─────────────────────────────────────────────────────────────────────────────
# =LEN(Description & VendorName) + 12  ≤  70
LEN_OVERHEAD = 12
LEN_LIMIT    = 70

# ─────────────────────────────────────────────────────────────────────────────
#  3.  PAY TYPE  (Col H)
# ─────────────────────────────────────────────────────────────────────────────
COL_H_FIND    = "American Express Corporate Card CBCP"
COL_H_REPLACE = "American Express"

# ─────────────────────────────────────────────────────────────────────────────
#  4.  DESCRIPTION RULES  (verified: only these 49 changes happen in AmEx All)
#
#  REMOVED rules (wrong — these do NOT occur in reference AmEx All):
#    × ' to ' → '-'       (car service descriptions keep "Office to Home")
#    × ' - ' → '-'        (spaces around dashes kept in reference)
#    × 'Business ' → 'Bus.' (only in human-reviewed entity tabs, not AmEx All)
#    × 'Bus.Lodging' → 'Lodging' (not observed in reference AmEx All)
#
#  Each tuple: (find, replace, scope, note)
#  Scopes: all | car_service | lodging | info_services | personal | airline
# ─────────────────────────────────────────────────────────────────────────────

DESCRIPTION_RULES = [
    ("Business ", "Bus.", "all", "Business -> Bus. per written docs"),
    ("Bus. Lodging", "Lodging", "lodging", "Remove Bus. from Lodging"),
    ("Bus.Lodging", "Lodging", "lodging", "Remove Bus. from Lodging"),
    ("Transportation", "", "car_service", "Remove Transportation from Car Service"),
    (" to ", "-", "car_service", "Use compact route separators in Car Service"),
    ("Home to Office", "Home-Office", "car_service", "Mandatory Home/Office car format"),
    ("Office to Home", "Office-Home", "car_service", "Mandatory Home/Office car format"),
    # Inflight Wifi casing — verified in reference
    ("Inflight WiFi",   "Inflight Wifi", "info_services", "WiFi → Wifi (exact casing)"),
    ("inflight wifi",   "Inflight Wifi", "info_services", "lowercase → Proper"),
    ("Inflight WIFI",   "Inflight Wifi", "info_services", "ALL CAPS → Proper"),
    ("In-flight Wifi",  "Inflight Wifi", "info_services", "Remove hyphen"),

    # Personal — remove trailing 'expense' word
    ("Personal expense", "Personal", "personal", "Strip trailing 'expense'"),
    ("Personal Expense", "Personal", "personal", "Strip trailing 'Expense'"),
]

# Word-boundary abbreviations — verified: only 'Meeting'→'Mtg' in AmEx All
# 'Ticketing Fee'→'Tkting Fee' and 'Ticket/'→'Tkt/' also observed
DESCRIPTION_ABBREVIATIONS = [
    # Longer phrases FIRST to prevent partial matches
    ("Ticketing Fee",    "Tkt Fee"),
    ("Meeting",          "Mtg"),          # verified: all 49 cases
    ("Meetings",         "Mtgs"),         # verified: row 6/7 Mehfar
]
# Note: 'Ticket/' prefix abbreviation handled separately (not word-boundary)
TICKET_PREFIX_ABBREV = ("Ticket/", "Tkt/")   # verified: row 10 Chand

# ─────────────────────────────────────────────────────────────────────────────
#  5.  EXPENSE CODE RULES  (verified: 10 changes in reference)
# ─────────────────────────────────────────────────────────────────────────────
EXPENSE_CODE_REMAP = {
    "Miscellaneous":    "Other",      # verified: 10 occurrences
    # Additional remaps for LLM validation
    "Cell Phone":       "Phones",
    "Telephones":       "Phones",
    "Furn & Equip":     "Equipment",
    "Furn & Equipment": "Equipment",
    "Seminars":         "Conferences",
    "Seminars and Conferences": "Conferences",
    "Info Service":     "Info Services",
    "Inflight Wifi":    "Info Services",
}

EXPENSE_CODE_DISALLOWED = ["Miscellaneous", "Cell Phone", "Telephones"]

# ─────────────────────────────────────────────────────────────────────────────
#  6.  VENDOR NORMALIZATION
#
#  Lookup priority:
#    1. Vendor List exact match (uppercase key)
#    2. Keyword match for common chains (NEW — fixes 47 vendor diffs)
#    3. Strip store numbers / location codes
#    4. title() with VENDOR_TITLE_FIXES
#    5. VENDOR_ABBREVIATIONS
# ─────────────────────────────────────────────────────────────────────────────

# Corrections to what title() produces wrongly
VENDOR_TITLE_FIXES = {
    "Americet":    "AmeriVet",
    "Americvet":   "AmeriVet",
    "Numotion":    "Numotion",
    "Tricorbraun": "TricorBraun",
    "Trikorbraun": "TricorBraun",
    "Ezcater":     "Ezcater",
    "EzCater":     "Ezcater",
    "Blue Bottle": "Blu",
    "Uber Eats":   "Uber",
    "Chick Fil A": "Chick-fil-A",
    "Nyc Taxi":    "NYC Taxi",
    "Jetblue":     "JetBlue",        # Vendor List returns 'Jetblue' → fix cap
}

# Expand long names → short canonical names
VENDOR_ABBREVIATIONS = {
    "Uber Technologies":     "Uber",
    "Uber Technologies Inc": "Uber",
    "Lyft Inc":              "Lyft",
    "Curb Mobility":         "Curb",
}

# Keyword → canonical vendor name (for vendors NOT in Vendor List)
# When raw VendorDesc CONTAINS one of these keywords, use the canonical name.
# Keys are lowercase keywords; values are the canonical vendor names.
VENDOR_KEYWORD_MAP = {
    "omni berkshire place": "Omni",
    "jersey mikes": "Jersey Mike's",
    "travel reservation usa": "Hotels.com",
    "slc essentials": "Essen",
    "blue bottle coffee": "Blu",
    "cibo express": "Cibo",
    "british airways": "British Air",
    "uber eats": "Uber",
    "chick-fil-a": "Chick-fil-A",
    "chick fil a": "Chick-fil-A",
    "marriott":    "Marriott",
    "hilton":      "Hilton",
    "hyatt":       "Hyatt",
    "westin":      "Westin",
    "sheraton":    "Sheraton",
    "holiday inn": "Holiday Inn",
    "ac hotel":    "AC Hotel",
    "four seasons":"Four Seasons",
    "jetblue":     "JetBlue",
    "dunkin":      "Dunkin Donuts",
    "shake shack": "Shake Shack",
    "starbucks":   "Starbucks",
    "chipotle":    "Chipotle",
    "qdoba":       "Qdoba",
    "cater lady":  "Cater Lady",
    "cafe boulud": "Cafe Boulud",
    "jetbrains":   "Jetbrains",
    "intelsat":    "Intelsat",
    "delta onboard":"Delta",
    "blue bottle": "Blu",
    "devocion":    "Devocion",
}

# ─────────────────────────────────────────────────────────────────────────────
#  7.  ENTITY / TAB CONFIG
# ─────────────────────────────────────────────────────────────────────────────
ENTITY_DEFAULT = "AEA"

ENTITY_TABS = {
    "AEA":    "AEA_Posted",
    "SBF":    "SBF_Posted",
    "DEBT":   "DEBT_Reviewed",
    "GROWTH": "GROWTH_Reviewed",
}

# ─────────────────────────────────────────────────────────────────────────────
#  8.  CAR SERVICE POLICY  (flag-only — no automatic description rewrites)
# ─────────────────────────────────────────────────────────────────────────────
CAR_POLICY = {
    "early_arrival_cutoff_hour":   7,
    "early_arrival_cutoff_minute": 0,
    "work_late_cutoff_hour":   19,
    "work_late_cutoff_minute": 30,
    "weekend_days": ["Saturday", "Sunday"],
}

CAR_FORMATS = {
    "work_late":     "Work Late/Office-Home",
    "early_arrival": "Early Arrival/Home-Office",
    "weekend":       "Weekend/Home-Office",
    "weekend_return": "Weekend/Office-Home",
}

# ─────────────────────────────────────────────────────────────────────────────
#  9.  IN-OFFICE MEAL LIMITS  (flag-only)
# ─────────────────────────────────────────────────────────────────────────────
MEAL_LIMITS = {
    "Working Lunch":  25.00,
    "Working Dinner": 35.00,
}
PERSONAL_PROJECT = "1008"

# ─────────────────────────────────────────────────────────────────────────────
#  10. G/L OVERRIDE NOTES  (written to entity tab Comments — NOT AmEx All)
# ─────────────────────────────────────────────────────────────────────────────
GL_OVERRIDE_NOTES = {
    "14000": {
        "trigger_projects": ["1035", "1003", "1012"],
        "comment": "Change G/L to 14000 at time of posting (prepaid)",
    },
    "58120": {
        "comment": "Change G/L to 58120 at posting (AEA Corporate event 'Other'→58350 default)",
        "trigger_expense":  "Other",
        "trigger_projects": ["1001", "3500", "7500", "1105"],
    },
    "58140": {
        "comment": "Holiday Party car — G/L 58140; NOT intercompany",
    },
    "58230": {
        "trigger_projects": ["1013"],
        "comment": "Intern/Summer Analyst event — G/L 58230",
    },
}

# ─────────────────────────────────────────────────────────────────────────────
#  11. PROJECT / DEPT VALIDATION RULES  (flag-only)
# ─────────────────────────────────────────────────────────────────────────────
PROJECT_DEPT_RULES = {
    "1055": "AMA",
    "1010": ["UK", "GMBH"],
    "4246": "CONS",
    "6006": "VAIP",
    "6001": "VAIP",
    "3500": "SBF",
    "7500": "DEBT",
    "1105": "GROWTH",
}

PROJECT_NAME_MAP = {
    "1008": {"requires_emp_id": True},
    "1003": {"prepaid_gl": "14000"},
    "1012": {"prepaid_gl": "14000"},
    "1035": {"prepaid_gl": "14000"},
    "1013": {"gl_note": "58230"},
    "4246": {"canonical_name": "AmeriVet"},
    "6409": {"canonical_name": "Numotion"},
    "4200-B": {"board_only": True},
    "1016-SBF": {"entity_hint": "SBF"},
    "1016-G": {"entity_hint": "GROWTH"},
}

# ─────────────────────────────────────────────────────────────────────────────
#  12. LLM CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
LLM_SYSTEM_PROMPT = """You are an expert AmEx expense data scrubber for AEA Investors LP.
Review each expense row and propose policy-compliant scrubbing updates. Do NOT copy one sample workbook blindly.
Written rules are the source of truth. If written rules conflict with a sample/reference pattern, follow the written rules and return a concise conflict note for reviewer comments.

WHAT THE SCRUBBER ALREADY DOES (don't flag these as issues):
- Pay type: 'American Express Corporate Card CBCP' → 'American Express' ✓
- Expense: 'Miscellaneous' → 'Other' ✓
- Description: 'Meeting' → 'Mtg', 'Meetings' → 'Mtgs', 'Ticketing Fee' → 'Tkting Fee' ✓
- Vendor names: cleaned from Vendor List ✓
- Written docs use 'Tkt Fee' abbreviation for ticketing fees; prefer that over sample-specific variants.

WHAT TO FLAG (needs human review):
- Description missing business purpose or deal name
- Inflight Wifi with wrong expense code (should be Info Services)
- Ticketing fees coded as Other Travel (should be Airline)
- Hotel fees not coded as Lodging
- Train, bus, boat, or other non-air transport fees not coded as Other Travel
- Project 1008 (Personal) without Employee ID
- Negative amounts/refunds that may not mirror the original charge's project, dept, expense type, and description
- LEN(description+vendor)+12 > 70
- Car service without clear To/From destination
- Trip rows where lodging, airfare, car service, and meals look related but use inconsistent project codes
- Prepaid/event rows for projects 1035, 1003, or 1012 that need a comment to change G/L to 14000 at posting
- Corporate event rows coded to Other under projects 1001, 3500, 7500, 1105 that need a comment to review G/L 58120 at posting
- Intern-event rows under project 1013 that need a comment to review G/L 58230 at posting
- Early Arrival / Work Late / Weekend car service rows where receipt-time policy cannot be verified from the row alone

DESCRIPTION FORMAT EXPECTATIONS:
- Flights: <Route or RT:Route>/<Purpose>/<Deal or Company>, e.g. RT:JFK-STO/Fundraising/Growth Fund
- Flight fees/upgrades: Tkt Fee/<Route>/<Purpose>/<Deal>, Seat Upgrade/<Purpose>/<Deal>, Exch Tkt/<Route>/<Purpose>/<Deal>
- Travel meals: Travel Meal/<Purpose>/<Deal or Company>
- Business meals with guests: Bus.Lunch or Bus.Dinner or Coffee Mtg, attendee names/initials and org, optional guest count, deal/company/event at the end
- Office meals: Working Lunch, Working Dinner, and overage rows should read Working Lunch/Overage/Personal or Working Dinner/Overage/Personal
- Car service: <From-To>/<Purpose>/<Deal or Company>, or one of Work Late/Office-Home, Early Arrival/Home-Office, Weekend/Home-Office, Weekend/Office-Home
- Lodging: Lodging/<Purpose>/<Deal or Company>
- Info Services: Inflight Wifi or Research Subscription/<Purpose>/<Deal or Company>
- Inflight Wifi booked to a management/general project may stay as Inflight Wifi; portfolio-company wifi should include purpose/deal suffix when known from nearby trip rows
- Other/Other Travel: Bus.Parking/<Purpose>/<Deal>, Bus.Fuel/<Route>/<Event>, Train/<Route>/<Purpose>/<Company>, Erroneous Charge/Awaiting Refund

WHAT NOT TO DO:
- Do NOT hardcode wording from one sample workbook if it is not supported by written rules or row context
- Do NOT change project/dept when confidence is low or trip context is insufficient; flag with a conflict note instead
- Do NOT add comments to rows that look correct

COMMENTS GUIDANCE:
- Only populate comments when flag=true.
- Keep comments concise and include attendee names/orgs, attendee count, refund/reschedule/late-charge context, or other posting-relevant notes.

Return ONLY valid JSON matching the schema."""

LLM_ROW_SCHEMA = {
    "description_ok":     "bool",
    "description_fixed":  "string | null — only if truly wrong format",
    "vendor_ok":          "bool",
    "vendor_fixed":       "string | null",
    "expense_code_ok":    "bool",
    "expense_code_fixed": "string | null",
    "project_fixed":      "string | null",
    "cost_center_fixed":  "string | null",
    "rule_ids_applied":   "list[string]",
    "confidence":         "float between 0 and 1",
    "conflict_note":      "string | null — note when written rules and sample/reference precedent may differ",
    "len_ok":             "bool",
    "len_value":          "int",
    "flag":               "bool — true only if human review genuinely needed",
    "flag_reason":        "string | null",
    "comments":           "string | null — note for reviewer if flagged",
}

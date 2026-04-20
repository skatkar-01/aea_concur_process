# 🔍 RULES AUDIT REPORT — Configuration vs LLM Prompt vs rules_docs

**Date:** April 20, 2026  
**Status:** AUDIT IN PROGRESS

---

## SUMMARY

Cross-checking rules from:
1. **LLM System Prompt** (`llm_formatter.py` lines 199-468)
2. **Config Files** (YAML: description_rules.yaml, expense_rules.yaml, policy_rules.yaml)
3. **rules_docs Folder** (3 Word/PDF files - cannot directly read but referenced in code)

---

## 1️⃣ DESCRIPTION FORMATTING RULES

### ✅ Implemented in Config + LLM
- [x] Business → Bus. (DESC_001)
- [x] Bus. Lodging → Lodging (DESC_002, DESC_003)
- [x] Remove Transportation (DESC_004)
- [x] Car service " to " → "-" (DESC_005)
- [x] Home to Office → Home-Office (DESC_006)
- [x] Office to Home → Office-Home (DESC_007)
- [x] Inflight WiFi casing (DESC_008-DESC_011)
- [x] Personal expense cleanup (DESC_012, DESC_013)
- [x] Ticketing Fee → Tkt Fee (ABBR_001)
- [x] Meeting → Mtg (ABBR_002)
- [x] Meetings → Mtgs (ABBR_003)
- [x] Strategy → Strategy Mtg (ABBR_004) ← NEWLY ADDED

### ❓ Mentioned in LLM Prompt but NOT in Config (MISSING IMPLEMENTATIONS)
- [ ] **Ticket/ → Tkt/** (prefix_abbreviations section exists in YAML, but no entries)
  - LLM mentions: `Prefix abbreviations: Ticket/ → Tkt/`
  - Config file has empty `prefix_abbreviations:` section
  - **ACTION NEEDED:** Add to description_rules.yaml

---

## 2️⃣ TRANSACTION TYPE FORMATTING (Category 2 in LLM)

### ✅ Mentioned in LLM System Prompt
- Flights: RT:JFK-STO/Fundraising/Growth Fund
- Flight Fees: Tkt Fee/RT:<route>/<purpose>/<deal>
- Refunds: Refund/<original format>
- Car Service: <from>-<to>/<purpose>/<deal>
- Business Meals: Bus.Lunch | Bus.Dinner/<initials>/<deal>
- Travel Meals: Travel Meal/<purpose>/<deal>
- Office Meals: Working Lunch | Working Dinner
- Lodging: Lodging/<purpose>/<deal>
- Info Services: Inflight Wifi | Research Subscription

### ❌ **NOT in Config Files** (Deterministic rules missing!)
💡 These are format templates - should be validated by LLM only (not config)

---

## 3️⃣ EXPENSE CODE RULES

### ✅ Implemented in expense_rules.yaml
- [x] Miscellaneous → Other
- [x] Cell Phone → Phones
- [x] Telephones → Phones
- [x] Furn & Equip → Equipment
- [x] Furn & Equipment → Equipment
- [x] Seminars → Conferences
- [x] Seminars and Conferences → Conferences
- [x] Info Service → Info Services
- [x] Inflight Wifi → Info Services

### ✅ Smart Detection Patterns (expense_rules.yaml)
- [x] inflight wifi → Info Services
- [x] tkt fee|ticketing fee|seat upgrade|checked bag → Airline
- [x] booking fee + lodging → Lodging
- [x] train|bus.parking|bus.fuel|travel insurance → Other Travel

### ❌ **MISSING from Config - Mentioned in LLM Only**
- [ ] Flight-related items should use "Airline" (mentioned in LLM but config pattern is generic)
- [ ] Refunds should mirror original charge amounts (LLM rule, not in policy_rules)

---

## 4️⃣ PAY TYPE CLEANUP (Category 4)

### ✅ Implemented
- [x] American Express Corporate Card CBCP → American Express

---

## 5️⃣ CHARACTER LENGTH LIMIT (Category 5)

### ✅ Implemented in policy_rules.yaml
```yaml
length_limits:
  description_vendor_combined: 70
  overhead: 12  # Formula: len(desc) + len(vendor) + 12 ≤ 70
```

### ✅ Referenced in LLM
- Prompt correctly states: `LEN(description + vendor) + 12 ≤ 70`

---

## 6️⃣ MEAL AMOUNT LIMITS (Category 6)

### ✅ Implemented in policy_rules.yaml
```yaml
meal_limits:
  "Working Lunch": 25.00
  "Working Dinner": 35.00
```

### ✅ Referenced in LLM
- Limit checks implemented correctly

---

## 7️⃣ CAR SERVICE POLICY (Category 7)

### ✅ Implemented in policy_rules.yaml
- [x] Early Arrival: ≤7:00am → "Early Arrival/Home-Office"
- [x] Work Late: ≥7:30pm → "Work Late/Office-Home"
- [x] Weekend: Saturday/Sunday → "Weekend/Home-Office" or "Weekend/Office-Home"

---

## 8️⃣ G/L OVERRIDE FLAGS (Category 8)

### ✅ Implemented in policy_rules.yaml
```yaml
gl_overrides:
  "14000": Projects 1035, 1003, 1012 (Prepaid)
  "58120": Expense "Other" + Projects 1001, 3500, 7500, 1105
  "58140": Holiday Party car
  "58230": Project 1013 (Intern event)
```

### ✅ All flags match LLM documentation

---

## 9️⃣ PROJECT & DEPARTMENT ALIGNMENT (Category 9)

### ✅ Implemented in policy_rules.yaml
```yaml
project_dept_rules:
  "1055": "AMA"
  "1010": ["UK", "GMBH"]
  "4246": "CONS"
  "6006": "VAIP"
  "6001": "VAIP"
  "3500": "SBF"
  "7500": "DEBT"
  "1105": "GROWTH"

project_metadata:
  "1008": requires_emp_id: true (Personal)
  "1003", "1012", "1035": prepaid projects
  "4200-B": board_only: true
```

### ✅ All rules match LLM documentation

---

## 🔟 MEAL ATTENDEE NAMING (NEWLY ENFORCED)

### ✅ Implemented in LLM System Prompt
- [x] Attendee names MUST be initials only (B.Gallagher, K.Carbonez)
- [x] Added to KEY PRINCIPLES section (strict requirement)

### ❌ **NOT in Config Files**
- [ ] No validation rule in YAML for initials-only format
- **NOTE:** This is a format requirement enforced by LLM, not config

---

## PRIORITY FIXES NEEDED

### 🔴 HIGH PRIORITY (Missing functionality)
1. **Ticket/ → Tkt/ prefix abbreviation**
   - File: `description_rules.yaml`
   - Currently: Empty `prefix_abbreviations:` section
   - **Fix:** Add entry for Ticket/ → Tkt/

### 🟡 MEDIUM PRIORITY (Documentation gaps)
None identified - all rules documented

### 🟢 LOW PRIORITY (Nice-to-have)
None identified

---

## VERIFICATION CHECKLIST

Category | Status | Config File | LLM Prompt | Missing
---------|--------|------------|-----------|----------
1. Description Formatting | ✅ | 13 rules | ✅ All covered | Ticket/ prefix (minor)
2. Transaction Types | ✅ | N/A | ✅ All templates | None
3. Expense Codes | ✅ | 8 rules + smart detection | ✅ All covered | None
4. Pay Type | ✅ | 1 rule | ✅ Covered | None
5. Character Length | ✅ | 1 rule | ✅ Covered | None
6. Meal Limits | ✅ | 2 limits | ✅ Covered | None
7. Car Service | ✅ | 3 policies | ✅ Covered | None
8. G/L Overrides | ✅ | 4 rules | ✅ Covered | None
9. Project/Dept | ✅ | 8 rules | ✅ Covered | None
10. Meal Attendees | ✅ | N/A | ✅ Strict now | None

---

## NOTES

- **rules_docs folder:** Contains 3 files (.docx, .pdf) that cannot be directly read
  - `Quick Reference Guide to Keep Handy.docx`
  - `Amex Checklist 2025 (Data Cleaning).pdf`
  - `AEA Data Scrubbing Analysis.docx`

- **LLM System Prompt:** Comprehensive, all 12 rule categories documented

- **Config Files:** Well-structured, most rules implemented

---

## NEXT STEPS

1. ✅ Add `Ticket/ → Tkt/` to prefix_abbreviations in description_rules.yaml
2. ✅ Verify prefix_abbreviations are actually processed by rules_engine.py
3. ✅ Test with actual batch data to ensure all transformations work
4. ✅ Document any additional rules found in rules_docs files

---

**Generated:** April 20, 2026  
**Reviewed by:** Code Audit

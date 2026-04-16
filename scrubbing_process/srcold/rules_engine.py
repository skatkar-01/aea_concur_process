"""
rules_engine.py
───────────────
Deterministic rules engine.  All transformations here are
purely rule-driven (no LLM).  Results are applied to AmEx All
and form the baseline for entity-tab output.
"""
from __future__ import annotations

import re
import logging
from typing import Tuple

from .config import (
    DESC_CFG, EXPENSE_CFG, VENDOR_CFG, PROJECT_CFG,
)
from .models import Row

log = logging.getLogger(__name__)


class RulesEngine:

    # ── Description ───────────────────────────────────────────────────────────
    def scrub_description(self, desc: str, expense_lower: str) -> Tuple[str, bool]:
        """
        Apply all description rules from description_rules.yaml.
        Returns (new_description, changed).
        """
        original = str(desc or "")
        result   = original

        # 1. YAML find-replace rules (priority-ordered)
        for rule in DESC_CFG.rules:
            if not self._scope_ok(rule.scope, expense_lower, result.lower()):
                continue
            result = result.replace(rule.find, rule.replace)

        # 2. Word-boundary abbreviations (regex)
        for abbrev in DESC_CFG.abbreviations:
            result = re.sub(
                r'\b' + re.escape(abbrev.long) + r'\b',
                abbrev.short,
                result,
                flags=re.IGNORECASE,
            )

        # 3. Prefix abbreviations (exact prefix match)
        for pa in DESC_CFG.prefix_abbreviations:
            result = result.replace(pa.find, pa.replace)

        # 4. Car service whitespace cleanup
        if "car service" in expense_lower:
            result = re.sub(r'\s*-\s*', '-', result)
            result = re.sub(r'\s*/\s*', '/', result)

        result = re.sub(r'  +', ' ', result).strip()
        return result, result != original

    def apply_entity_tab_rules(self, desc: str) -> str:
        """Entity-tab-only substitutions (applied after LLM pass)."""
        result = str(desc or "")
        for rule in DESC_CFG.entity_tab_rules:
            result = result.replace(rule.find, rule.replace)
        # Parking prefix
        if result.startswith("Parking/"):
            result = f"Bus.{result}"
        # RT: spacing
        result = re.sub(r'\bRT:\s+', 'RT:', result)
        return re.sub(r'  +', ' ', result).strip()

    # ── Pay type ──────────────────────────────────────────────────────────────
    @staticmethod
    def scrub_pay_type(pay_type: str) -> Tuple[str, bool]:
        orig = str(pay_type or "")
        new  = orig.replace(
            PROJECT_CFG.pay_type_find,
            PROJECT_CFG.pay_type_replace,
        )
        return new, new != orig

    # ── Expense code ─────────────────────────────────────────────────────────
    def scrub_expense_code(
        self, code: str, desc: str, vendor_desc: str = ""
    ) -> Tuple[str, bool]:
        c     = str(code or "").strip()
        d_low = str(desc or "").strip().lower()

        # 1. Description-keyword overrides (highest priority)
        for override in EXPENSE_CFG.description_overrides:
            kw_match = any(k in d_low for k in override.keywords)
            if not kw_match:
                continue
            exclude_hit = any(k in d_low for k in override.exclude_keywords)
            if exclude_hit:
                continue
            ctx_needed = override.context_keywords
            if ctx_needed and not any(k in d_low for k in ctx_needed):
                continue
            return override.expense_code, override.expense_code != c

        # 2. Direct remap table
        new = EXPENSE_CFG.remap.get(c, c)
        return new, new != c

    # ── Vendor normalization ──────────────────────────────────────────────────
    def normalize_vendor(
        self, vendor_desc: str, project: str = "", vendor_list: dict = None
    ) -> str:
        """
        Lookup priority:
          1. Vendor List exact match (from reference xlsx)
          2. YAML keyword match (vendor_desc contains keyword)
          3. Strip store numbers / location codes
          4. title() with title_fixes
          5. abbreviations dict
        """
        raw = str(vendor_desc or "").strip()
        if not raw:
            return raw

        # 1. Vendor List
        if vendor_list:
            looked_up = vendor_list.get(raw.upper())
            if looked_up:
                return looked_up

        raw_l = raw.lower()

        # 2. Project-specific canonical vendor override
        if project:
            po = VENDOR_CFG.project_overrides.get(str(project), {})
            if po.get("canonical"):
                return po["canonical"]

        # 3. Keyword map
        for kw, canonical in VENDOR_CFG.keyword_map.items():
            if kw in raw_l:
                return canonical

        # 4. Strip store numbers: "STARBUCKS #12345" → "Starbucks"
        cleaned = re.sub(r'\s*#\d+\b.*$', '', raw).strip()
        cleaned = re.sub(r'\s*\d{4,}.*$', '', cleaned).strip()
        if not cleaned:
            cleaned = raw

        # 5. title()
        titled = cleaned.title()

        # 6. title_fixes
        for wrong, correct in VENDOR_CFG.title_fixes.items():
            if titled.lower() == wrong.lower():
                return correct

        # 7. Abbreviations
        for long_name, short_name in VENDOR_CFG.abbreviations.items():
            if titled.lower() == long_name.lower():
                return short_name

        return titled

    # ── LEN ───────────────────────────────────────────────────────────────────
    @staticmethod
    def compute_len(desc: str, vendor: str) -> int:
        return (len(str(desc or "")) +
                len(str(vendor or "")) +
                PROJECT_CFG.len_overhead)

    # ── Policy flag checks ────────────────────────────────────────────────────
    def check_row(self, r: Row) -> None:
        """
        Apply all flag-check rules. Results written to r.flags / r.review_comment.
        Nothing is written to Excel cells here — that happens in the writer.
        """
        proj   = r.project_str()
        dept   = str(r.cost_center or "").strip()
        desc_l = str(r.desc_out or "").lower()
        exp_l  = str(r.expense_out or "").lower()

        # Disallowed expense codes
        if r.expense_out in EXPENSE_CFG.disallowed:
            r.flag(f"Disallowed expense code '{r.expense_out}'")

        # Inflight Wifi expense code mismatch
        if "inflight wifi" in desc_l and exp_l != "info services":
            r.flag(f"Inflight Wifi must use 'Info Services', not '{r.expense_out}'")

        # Ticketing fee coded as Other Travel
        if ("tkt fee" in desc_l or "ticketing fee" in desc_l) and "train" not in desc_l:
            if exp_l == "other travel":
                r.flag("Ticketing fees must use expense code 'Airline'")

        # Project → required dept
        required_depts = PROJECT_CFG.project_dept_rules.get(proj)
        if required_depts and dept not in required_depts:
            r.flag(f"Project {proj} requires dept {required_depts}, got '{dept}'")

        # Project metadata flags
        meta = PROJECT_CFG.project_meta.get(proj, {})
        if meta.get("requires_emp_id") and not str(r.employee_id or "").strip():
            r.flag("Personal project 1008 requires Employee ID")
        if meta.get("board_only") and "bod" not in desc_l and "board" not in desc_l:
            r.flag(f"Project {proj} restricted to board-meeting travel")

        # G/L override flags
        for gl_rule in EXPENSE_CFG.gl_overrides:
            trig_proj = gl_rule.trigger_projects
            trig_exp  = gl_rule.trigger_expense
            trig_kw   = gl_rule.trigger_keywords

            if trig_proj and proj not in trig_proj:
                continue
            if trig_exp and r.expense_out != trig_exp:
                continue
            if trig_kw and not any(k in desc_l for k in trig_kw):
                continue
            r.flag(f"G/L {gl_rule.gl} override needed at posting", gl_rule.comment)

        # Meal limits
        for meal_kw, limit in EXPENSE_CFG.meal_limits.items():
            if meal_kw.lower() in desc_l and r.amount > limit:
                r.flag(
                    f"{meal_kw} ${r.amount:.2f} exceeds ${limit:.2f} limit",
                    f"Itemise overage to project {EXPENSE_CFG.personal_project} as "
                    f"{meal_kw}/Overage/Personal",
                )
                break

        # Car service policy
        car_formats = list(PROJECT_CFG.car_formats.values())
        if exp_l == "car service":
            if any(fmt.lower() in desc_l for fmt in car_formats):
                r.flag(
                    "Verify car-service receipt time/date against "
                    "Early Arrival / Work Late / Weekend policy"
                )
            elif "/" not in desc_l:
                r.flag(
                    "Car Service description should include route/purpose/deal "
                    "or an approved home-office format"
                )

        # Refund
        if r.amount < 0:
            r.flag("Negative amount/refund — verify original booking in Sage")

        # LEN
        if r.len_value > PROJECT_CFG.len_limit:
            r.flag(f"LEN {r.len_value} exceeds {PROJECT_CFG.len_limit}")

    # ── Helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _scope_ok(scope: str, expense_lower: str, desc_lower: str) -> bool:
        return {
            "all":           True,
            "info_services": "info" in expense_lower or "info services" in expense_lower,
            "personal":      "personal" in desc_lower,
            "airline":       "airline" in expense_lower,
            "car_service":   "car service" in expense_lower,
            "lodging":       "lodging" in expense_lower,
        }.get(scope, True)

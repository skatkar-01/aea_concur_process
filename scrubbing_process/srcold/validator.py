"""
validator.py
────────────
Post-scrub validation pass.

Validates:
  - LEN(description + vendor) + 12 ≤ 70
  - Expense code policy
  - Refund mirroring
  - Duplicate home-office car service rows
  - Trip-level project code consistency
  - G/L override notes
"""
from __future__ import annotations

import logging
from typing import Dict, List, Tuple

from .config import EXPENSE_CFG, PROJECT_CFG
from .models import Row
from .rules_engine import RulesEngine

log = logging.getLogger(__name__)


class Validator:

    def __init__(self, rules: RulesEngine):
        self.rules = rules

    def validate_all(self, rows: List[Row]) -> None:
        """Run all validation checks across the full batch."""
        self._validate_individual(rows)
        self._mirror_refund_expense_codes(rows)
        self._flag_duplicate_office_home(rows)
        self._flag_inconsistent_trip_projects(rows)
        n_flagged = sum(bool(r.flags) for r in rows)
        n_len_err = sum(1 for r in rows if r.len_value > PROJECT_CFG.len_limit)
        log.info("  Validation: %d flagged, %d LEN errors", n_flagged, n_len_err)

    # ── Individual row checks ─────────────────────────────────────────────────
    def _validate_individual(self, rows: List[Row]) -> None:
        for r in rows:
            # Recompute LEN with final entity values
            r.len_value = self.rules.compute_len(
                r.entity_desc_out or r.desc_out,
                r.vendor_out,
            )
            self.rules.check_row(r)

    # ── Refund mirroring ──────────────────────────────────────────────────────
    def _mirror_refund_expense_codes(self, rows: List[Row]) -> None:
        """
        For refunds: if there is exactly one matching positive charge
        (same employee_id, project, dept, amount), mirror the expense
        code and description prefix from that charge.
        """
        originals: Dict[Tuple, List[Row]] = {}
        for r in rows:
            if r.amount <= 0:
                continue
            key = (
                r.employee_id,
                r.project_str(),
                str(r.cost_center or ""),
                round(r.amount, 2),
            )
            originals.setdefault(key, []).append(r)

        for r in rows:
            if r.amount >= 0:
                continue
            key = (
                r.employee_id,
                r.project_str(),
                str(r.cost_center or ""),
                round(abs(r.amount), 2),
            )
            matches = originals.get(key, [])
            if len(matches) != 1:
                r.flag(
                    "Refund — could not find unique original charge to mirror",
                    "Verify refund matches original booking in Sage",
                )
                continue

            source = matches[0]
            if r.expense_out != source.expense_out:
                r.expense_out = source.expense_out
                r.entity_expense_out = source.expense_out
                r.expense_changed = True

            # Prefix description with "Refund/" if not already
            desc = r.entity_desc_out or r.desc_out
            if not desc.lower().startswith("refund/") and not desc.lower().startswith("refund "):
                r.entity_desc_out = f"Refund/{desc}"
                r.desc_changed = True

            r.changed = True

    # ── Duplicate home-office car service ────────────────────────────────────
    def _flag_duplicate_office_home(self, rows: List[Row]) -> None:
        """Flag >1 Work Late/Office-Home rows for same employee+date."""
        buckets: Dict[Tuple, List[Row]] = {}
        office_home_phrases = {
            "work late/office-home",
            "early arrival/home-office",
            "weekend/home-office",
            "weekend/office-home",
        }
        for r in rows:
            desc_l = str(r.entity_desc_out or r.desc_out or "").lower()
            if any(p in desc_l for p in office_home_phrases):
                emp_key = r.employee_id or f"{r.first_name}|{r.last_name}"
                key     = (emp_key, str(r.tran_dt or "")[:10])
                buckets.setdefault(key, []).append(r)

        for grouped in buckets.values():
            if len(grouped) > 1:
                for r in grouped:
                    r.flag(
                        "Multiple home-office car service rows on same date",
                        "Check: tip on same ride, or after-midnight service "
                        "(service date = prior day)",
                    )

    # ── Trip-level project code consistency ───────────────────────────────────
    def _flag_inconsistent_trip_projects(self, rows: List[Row]) -> None:
        """
        Same-day trip-like rows (airline/lodging/car/meals) that use
        more than one project code are flagged for reviewer alignment.
        """
        trip_expense_types = {
            "airline", "lodging", "car service", "meals",
            "other travel", "other",
        }
        trip_desc_tokens = (
            "lodging", "travel meal", "tkt fee", "rt:",
            "site visit", "bod mtg", "mgmt mtg", "strategy mtg",
            "portfolio mtg", "hotel", "train/", "bus.parking",
            "bus.fuel", "bus.rental",
        )
        buckets: Dict[Tuple, List[Row]] = {}

        for r in rows:
            exp_l  = str(r.expense_out or "").lower()
            desc_l = str(r.entity_desc_out or r.desc_out or "").lower()
            if exp_l not in trip_expense_types:
                continue
            if not any(token in desc_l for token in trip_desc_tokens):
                continue
            emp_key = r.employee_id or f"{r.first_name}|{r.last_name}"
            key     = (emp_key, str(r.tran_dt or "")[:10])
            buckets.setdefault(key, []).append(r)

        for grouped in buckets.values():
            projects = {
                r.project_str()
                for r in grouped
                if r.project_str() and r.project_str() != EXPENSE_CFG.personal_project
            }
            if len(projects) > 1:
                proj_list = ", ".join(sorted(projects))
                for r in grouped:
                    r.flag(
                        "Same-day trip rows use multiple project codes",
                        f"Review alignment: {proj_list}",
                    )

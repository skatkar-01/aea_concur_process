"""
models.py
─────────
Single Row dataclass used throughout the pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Row:
    idx: int

    # ── Original Concur values (never modified after load) ─────────────────────
    first_name:     str    = ""
    middle_name:    str    = ""
    last_name:      str    = ""
    blank:          str    = ""
    tran_dt:        object = None
    description:    str    = ""
    amount:         float  = 0.0
    pay_type:       str    = ""
    expense_code:   str    = ""
    vendor_desc:    str    = ""
    vendor_name:    str    = ""
    project:        object = None
    cost_center:    str    = ""
    report_purpose: str    = ""
    employee_id:    str    = ""

    # ── AmEx All tab output (apply deterministic scrub) ───────────────────────
    desc_out:      str = ""
    pay_type_out:  str = ""
    expense_out:   str = ""
    vendor_out:    str = ""

    # ── Entity tab output (apply LLM + additional entity-tab rules) ───────────
    entity_desc_out:         str    = ""
    entity_expense_out:      str    = ""
    entity_project_out:      object = None
    entity_cost_center_out:  str    = ""

    # ── Metadata ──────────────────────────────────────────────────────────────
    len_value:    int   = 0
    entity:       str   = ""
    review_comment: str = ""

    # ── Change tracking ───────────────────────────────────────────────────────
    desc_changed:       bool = False
    pay_type_changed:   bool = False
    expense_changed:    bool = False
    vendor_changed:     bool = False
    changed:            bool = False

    # ── LLM metadata ──────────────────────────────────────────────────────────
    llm_confidence: float = 0.0
    llm_rule_ids:   List[str] = field(default_factory=list)

    # ── Flags (summary report only — not written to any Excel cell) ───────────
    flags: List[str] = field(default_factory=list)

    # ── Receipt data (from transaction memory file) ───────────────────────────
    receipt_data:   Optional[dict] = field(default=None, repr=False)

    def flag(self, msg: str, comment: str = "") -> None:
        """Record a flag for the summary report and optionally a reviewer comment."""
        if msg and msg not in self.flags:
            self.flags.append(msg)
        comment = str(comment or "").strip()
        if comment and comment not in self.review_comment:
            self.review_comment = (
                comment if not self.review_comment
                else f"{self.review_comment} | {comment}"
            )

    def project_str(self) -> str:
        return str(self.project or "").strip()

    @property
    def full_name(self) -> str:
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(p for p in parts if p).strip()

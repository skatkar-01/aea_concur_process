"""
config/policy.py
Business rules only. Changes here are finance decisions, not engineering ones.
Deliberately separate from settings.py (which is infrastructure config).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict


@dataclass(frozen=True)
class Policy:
    # ── Amount reconciliation ─────────────────────────────────────────────────
    # Max allowed difference between AMEX total and Concur total (USD)
    amount_tolerance_usd:   float = 0.02

    # ── Auto-approval threshold ───────────────────────────────────────────────
    # Reports with total_claimed above this always require manual review
    auto_approve_max_usd:   float = 500.0

    # ── Per-category spending limits ──────────────────────────────────────────
    category_limits: Dict[str, float] = field(default_factory=lambda: {
        "ride":    200.0,
        "meal":    150.0,
        "hotel":   400.0,
        "flight": 3000.0,
        "parking":  75.0,
        "other":   500.0,
    })

    # ── Tip rules ─────────────────────────────────────────────────────────────
    tip_warn_pct:  float = 20.0   # warn if tip > X% of base fare
    tip_flag_pct:  float = 30.0   # hard flag if tip > X% of base fare

    # ── Attendees ─────────────────────────────────────────────────────────────
    require_named_attendees: bool = True   # flag "Employee" as unnamed

    # ── Business purpose ──────────────────────────────────────────────────────
    min_purpose_words: int = 3   # flag vague purpose descriptions

    # ── Receipt matching ──────────────────────────────────────────────────────
    # LLM returns this confidence level for individual field matches
    # below which we treat the field as a warning (not a hard flag)
    receipt_match_warn_confidence: str = "LOW"


# Singleton
_policy: Policy | None = None

def get_policy() -> Policy:
    global _policy
    if _policy is None:
        _policy = Policy()
    return _policy

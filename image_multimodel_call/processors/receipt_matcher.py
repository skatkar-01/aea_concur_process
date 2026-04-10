"""
processors/receipt_matcher.py
This is Match 2 of 2 — the transaction-level check:
  "Does each Concur transaction match its receipt on amount, date, vendor, description?"

Two layers:
  1. Programmatic hard checks — arithmetic (NEVER delegated to LLM)
  2. LLM semantic checks — date format normalisation, vendor name fuzzy match,
     description plausibility

Input:  list[ConcurTransaction] + list[Receipt]
Output: list[TransactionMatchResult]

No I/O. Pure data transformation.
"""
from __future__ import annotations

from config.policy import get_policy
from config.settings import get_settings
from models.concur import ConcurTransaction
from models.receipt import Receipt
from models.tracker import (
    FieldMatchResult,
    MatchConfidence,
    MatchStatus,
    TransactionMatchResult,
)
from shared.logger import get_logger

log = get_logger(__name__)

# ── Linking prompt ─────────────────────────────────────────────────────────────
_LINK_PROMPT = """\
You have Concur expense transactions and parsed receipts from the same PDF.
Match each transaction to its receipt using vendor name, date, and amount.
Each transaction matches exactly one receipt.
Supporting documents (itineraries, confirmations without a charge) → unlinked receipts.

Return ONLY valid JSON:
{
  "links": [
    {
      "transaction_index": 0,
      "receipt_index": 0,
      "confidence": "HIGH" | "MEDIUM" | "LOW",
      "match_basis": "<brief reason>"
    }
  ],
  "unlinked_transactions": [],
  "unlinked_receipts": []
}
"""

# ── Match verification prompt ──────────────────────────────────────────────────
_MATCH_PROMPT = """\
You are an expense auditor. Compare this Concur transaction against its receipt.
Verify all four fields. Return ONLY valid JSON — no markdown:

{
  "overall_match": true or false,
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "field_results": {
    "amount":      { "match": true/false, "transaction_value": "", "receipt_value": "", "difference": 0.0, "note": "" },
    "date":        { "match": true/false, "transaction_value": "", "receipt_value": "", "note": "" },
    "vendor":      { "match": true/false, "transaction_value": "", "receipt_value": "", "note": "" },
    "description": { "match": true/false, "transaction_value": "", "receipt_value": "", "note": "" }
  },
  "discrepancies": [],
  "summary": ""
}

Rules:
  AMOUNT     : match within $0.02. Larger difference = mismatch.
  DATE       : same calendar day regardless of format. "Jan 27 2026" = "01/27/2026".
  VENDOR     : trade names/abbreviations OK. "UBER" = "Uber Technologies". Different companies = mismatch.
  DESCRIPTION: business purpose must be plausible for the vendor/service.
               "BOD Dinner" is plausible for a restaurant.
               "Office supplies" at a restaurant is suspicious — flag it.
"""


class ReceiptMatcher:
    """
    Links and verifies each Concur transaction against its receipt.
    Requires an azure_client for LLM calls.
    """

    def __init__(self, azure_client):
        self._llm = azure_client

    def match(
        self,
        transactions: list[ConcurTransaction],
        receipts:     list[Receipt],
    ) -> list[TransactionMatchResult]:
        """
        Match all transactions to receipts.
        Returns one TransactionMatchResult per transaction.
        """
        if not transactions:
            log.warning("No transactions to match")
            return []

        if not receipts:
            log.warning("No receipts to match against — all transactions will be MISSING")
            return [
                self._missing_result(i)
                for i in range(len(transactions))
            ]

        # Step 1: Link transactions → receipts via LLM
        links = self._link(transactions, receipts)

        # Step 2: Verify each linked pair
        results: list[TransactionMatchResult] = []
        linked_txn_indices = {lk["transaction_index"] for lk in links.get("links", [])}

        for link in links.get("links", []):
            ti = link["transaction_index"]
            ri = link["receipt_index"]

            if ti >= len(transactions) or ri >= len(receipts):
                log.warning("Out-of-range link ti=%d ri=%d — skipping", ti, ri)
                continue

            txn = transactions[ti]
            rec = receipts[ri]

            log.info(
                "  Matching txn[%d] %s $%.2f ↔ receipt[%d] %s $%.2f (pages %s)",
                ti, txn.vendor, txn.amount,
                ri, rec.effective_vendor, rec.total_charged, rec.source_pages,
            )

            # Hard arithmetic check first (never LLM)
            hard_amount = self._hard_amount_check(txn, rec, ti)

            # LLM semantic check
            llm_result = self._llm_verify(txn, rec, ti)

            # Merge: hard check overrides LLM on amount field
            result = self._merge_results(ti, ri, rec, hard_amount, llm_result, link)
            results.append(result)

        # Unlinked transactions → MISSING_RECEIPT flag
        for ti in links.get("unlinked_transactions", []):
            if ti < len(transactions):
                results.append(self._missing_result(ti))

        # Sort by transaction index
        results.sort(key=lambda r: r.transaction_index)
        return results

    # ── Step 1: LLM linking ────────────────────────────────────────────────────

    def _link(
        self,
        transactions: list[ConcurTransaction],
        receipts:     list[Receipt],
    ) -> dict:
        txn_summary = "\n".join(
            f"[{i}] vendor={t.vendor} date={t.transaction_date} "
            f"amount=${t.amount:.2f} type={t.expense_type}"
            for i, t in enumerate(transactions)
        )
        rec_summary = "\n".join(
            f"[{i}] vendor={r.effective_vendor} date={r.effective_date} "
            f"amount=${r.total_charged:.2f} type={r.receipt_type.value} pages={r.source_pages}"
            for i, r in enumerate(receipts)
        )

        prompt = (
            f"{_LINK_PROMPT}\n\n"
            f"TRANSACTIONS ({len(transactions)}):\n{txn_summary}\n\n"
            f"RECEIPTS ({len(receipts)}):\n{rec_summary}"
        )

        try:
            result = self._llm.call_json(
                messages=self._llm.user_message([self._llm.text_block(prompt)]),
                max_completion_tokens=get_settings().llm_max_completion_tokens_med,
                context=f"link {len(transactions)} txns",
                required_keys=["links"],
            )
        except Exception as exc:
            log.error("Linking failed: %s — sequential fallback", exc)
            return self._sequential_fallback(transactions, receipts)

        # Validate index ranges
        n_t, n_r = len(transactions), len(receipts)
        valid = []
        for lk in result.get("links", []):
            ti = lk.get("transaction_index")
            ri = lk.get("receipt_index")
            if ti is None or ri is None:
                continue
            if not (0 <= ti < n_t and 0 <= ri < n_r):
                log.warning("Out-of-range link ti=%s ri=%s — discarding", ti, ri)
                continue
            valid.append(lk)

        result["links"] = valid
        linked_t = {lk["transaction_index"] for lk in valid}
        linked_r = {lk["receipt_index"]     for lk in valid}
        result.setdefault("unlinked_transactions", [i for i in range(n_t) if i not in linked_t])
        result.setdefault("unlinked_receipts",     [i for i in range(n_r) if i not in linked_r])

        log.info(
            "  linked %d pair(s) | unlinked txns=%d receipts=%d",
            len(valid),
            len(result["unlinked_transactions"]),
            len(result["unlinked_receipts"]),
        )
        return result

    # ── Step 2a: Hard arithmetic check ───────────────────────────────────────

    def _hard_amount_check(
        self,
        txn: ConcurTransaction,
        rec: Receipt,
        txn_index: int,
    ) -> FieldMatchResult:
        """
        Programmatic amount comparison — never delegated to LLM.
        Uses round() to avoid floating point precision issues.
        """
        policy   = get_policy()
        ta       = round(txn.amount, 2)
        ra       = round(rec.total_charged, 2)
        diff     = round(abs(ta - ra), 2)
        matched  = diff <= policy.amount_tolerance_usd

        note = (
            f"✅ ${ta:.2f} == ${ra:.2f}"
            if matched
            else f"❌ ${ta:.2f} ≠ ${ra:.2f} (diff ${diff:.2f}, tolerance ${policy.amount_tolerance_usd:.2f})"
        )

        log.debug(
            "  hard amount check txn[%d]: txn=$%.2f rec=$%.2f diff=$%.2f %s",
            txn_index, ta, ra, diff, "PASS" if matched else "FAIL",
        )
        return FieldMatchResult(
            field_name        = "amount",
            matched           = matched,
            transaction_value = f"${ta:.2f}",
            receipt_value     = f"${ra:.2f}",
            note              = note,
        )

    # ── Step 2b: LLM semantic verification ───────────────────────────────────

    def _llm_verify(
        self,
        txn:       ConcurTransaction,
        rec:       Receipt,
        txn_index: int,
    ) -> dict:
        """Ask LLM to verify date, vendor, description (not amount — that's hard check)."""
        txn_text = self._format_txn(txn)
        rec_text = self._format_receipt(rec)

        content = [
            self._llm.text_block(
                f"=== CONCUR TRANSACTION ===\n{txn_text}\n\n"
                f"=== RECEIPT ===\n{rec_text}"
            )
        ]
        # Include receipt image for photo captures
        if rec.is_image_capture:
            for page in rec.source_pages:
                pass  # images freed after extraction — text-only at this stage

        content.append(self._llm.text_block(_MATCH_PROMPT))

        try:
            return self._llm.call_json(
                messages=self._llm.user_message(content),
                max_completion_tokens=get_settings().llm_max_completion_tokens_med,
                context=f"match txn[{txn_index}]",
                required_keys=["overall_match", "confidence", "field_results"],
            )
        except Exception as exc:
            log.error("LLM match failed txn[%d]: %s", txn_index, exc)
            return {
                "overall_match": False,
                "confidence":    "LOW",
                "field_results": {},
                "discrepancies": [f"LLM match failed: {exc}"],
                "summary":       "Match check failed — manual review required",
            }

    # ── Merge hard + LLM results ──────────────────────────────────────────────

    def _merge_results(
        self,
        ti:           int,
        ri:           int,
        rec:          Receipt,
        hard_amount:  FieldMatchResult,
        llm_result:   dict,
        link:         dict,
    ) -> TransactionMatchResult:
        """
        Combine hard arithmetic result with LLM semantic result.
        Hard amount check always wins over LLM amount assessment.
        """
        llm_fields    = llm_result.get("field_results", {})
        field_results: list[FieldMatchResult] = []

        # Amount: use hard check result (override LLM)
        field_results.append(hard_amount)

        # Other fields: use LLM result
        for fname in ("date", "vendor", "description"):
            fr = llm_fields.get(fname, {})
            field_results.append(FieldMatchResult(
                field_name        = fname,
                matched           = bool(fr.get("match", True)),
                transaction_value = fr.get("transaction_value"),
                receipt_value     = fr.get("receipt_value"),
                note              = fr.get("note"),
            ))

        # overall_match = hard amount AND LLM overall
        overall = hard_amount.matched and bool(llm_result.get("overall_match", False))

        # Confidence: downgrade if link was LOW confidence
        llm_conf   = llm_result.get("confidence", "MEDIUM")
        link_conf  = link.get("confidence", "HIGH")
        confidence = self._min_confidence(llm_conf, link_conf)

        # Status
        if not hard_amount.matched:
            status = MatchStatus.MISMATCH
        elif not overall:
            status = MatchStatus.MISMATCH
        else:
            status = MatchStatus.MATCHED

        discrepancies = list(llm_result.get("discrepancies", []))
        if not hard_amount.matched:
            discrepancies.insert(0, hard_amount.note)

        return TransactionMatchResult(
            transaction_index = ti,
            receipt_pages     = rec.source_pages,
            receipt_type      = rec.receipt_type.value,
            overall_match     = overall,
            confidence        = MatchConfidence(confidence),
            status            = status,
            field_results     = field_results,
            discrepancies     = discrepancies,
            summary           = llm_result.get("summary", ""),
        )

    def _missing_result(self, txn_index: int) -> TransactionMatchResult:
        return TransactionMatchResult(
            transaction_index = txn_index,
            receipt_pages     = [],
            receipt_type      = None,
            overall_match     = False,
            confidence        = MatchConfidence.HIGH,
            status            = MatchStatus.MISSING,
            discrepancies     = ["No receipt found for this transaction"],
            summary           = "MISSING — no receipt linked",
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _sequential_fallback(
        transactions: list[ConcurTransaction],
        receipts:     list[Receipt],
    ) -> dict:
        n = min(len(transactions), len(receipts))
        return {
            "links": [
                {
                    "transaction_index": i,
                    "receipt_index":     i,
                    "confidence":        "LOW",
                    "match_basis":       "Sequential fallback — LLM linking failed",
                }
                for i in range(n)
            ],
            "unlinked_transactions": list(range(n, len(transactions))),
            "unlinked_receipts":     list(range(n, len(receipts))),
        }

    @staticmethod
    def _min_confidence(a: str, b: str) -> str:
        order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        return a if order.get(a, 1) <= order.get(b, 1) else b

    @staticmethod
    def _format_txn(txn: ConcurTransaction) -> str:
        attendees = ", ".join(txn.attendees) if txn.attendees else "Not listed"
        return (
            f"Date            : {txn.transaction_date}\n"
            f"Expense Type    : {txn.expense_type}\n"
            f"Business Purpose: {txn.business_purpose}\n"
            f"Vendor          : {txn.vendor}\n"
            f"Amount          : ${txn.amount:.2f}\n"
            f"Payment Type    : {txn.payment_type}\n"
            f"Cost Center     : {txn.cost_center}\n"
            f"Project         : {txn.project}\n"
            f"Attendees       : {attendees}"
        )

    @staticmethod
    def _format_receipt(rec: Receipt) -> str:
        lines = [
            f"Receipt Type    : {rec.receipt_type.value}",
            f"Vendor          : {rec.effective_vendor}",
            f"Date            : {rec.effective_date}",
            f"Total Charged   : ${rec.total_charged:.2f}",
        ]
        if rec.base_fare:
            lines.append(f"Base Fare       : ${rec.base_fare:.2f}")
        if rec.tax:
            lines.append(f"Tax             : ${rec.tax:.2f}")
        if rec.tip:
            lines.append(f"Tip             : ${rec.tip:.2f}")
        if rec.fees:
            for k, v in rec.fees.items():
                if v:
                    lines.append(f"  {k}: ${float(v):.2f}")
        if rec.payment_method:
            lines.append(f"Payment         : {rec.payment_method} ····{rec.payment_last4 or ''}")
        if rec.origin_address:
            lines.append(f"Origin          : {rec.origin_address}")
        if rec.destination_address:
            lines.append(f"Destination     : {rec.destination_address}")
        if rec.check_in_date:
            lines.append(f"Check-in        : {rec.check_in_date}")
        if rec.check_out_date:
            lines.append(f"Check-out       : {rec.check_out_date}")
        return "\n".join(lines)

"""
extractors/concur_extractor.py
Concur report PDF → ConcurReport model.
No business validation — pure extraction.
"""
from __future__ import annotations
import time
from datetime import datetime, timezone
from pathlib import Path

from config.settings import get_settings
from extractors.base import BaseExtractor
from models.concur import (
    ConcurReport, ConcurTransaction, ConcurTotals, AuditEntry,
)
from shared.exceptions import ExtractionError
from shared.logger import get_logger
from shared.pdf_loader import PDFLoader

log = get_logger(__name__)

_PROMPT = """\
Extract ALL data from these SAP Concur expense report pages.
Return ONLY valid JSON — no markdown, no explanation.
Monetary values must be numbers (52.01 not "$52.01").
Dates: keep exactly as shown. Missing fields: null for strings, 0.0 for amounts, [] for lists.
Extract EVERY expense line — there may be multiple across pages.
"attendees" is always a list of strings (empty list [] if none).

{
  "report_header": {
    "report_name": null, "report_id": null, "report_date": null,
    "approval_status": null, "payment_status": null,
    "receipts_received": null, "currency": null
  },
  "employee": { "name": null, "employee_id": null },
  "transactions": [
    {
      "transaction_date": null, "expense_type": null, "business_purpose": null,
      "vendor": null, "payment_type": null, "amount": 0.0,
      "cost_center": null, "project": null, "attendees": [],
      "receipt_required": null, "notes": null
    }
  ],
  "totals": {
    "report_total": 0.0, "personal_expenses": 0.0, "total_claimed": 0.0,
    "amount_approved": 0.0, "amount_due_employee": 0.0,
    "amount_due_card": 0.0, "total_paid_by_company": 0.0
  },
  "audit_trail": [
    { "timestamp": null, "actor": null, "action": null, "detail": null }
  ]
}
"""


class ConcurExtractor(BaseExtractor):

    def extract(self, pdf_path: Path) -> ConcurReport:
        t0 = time.monotonic()
        log.info("Extracting Concur report: %s", pdf_path.name)

        try:
            pages = PDFLoader.load(pdf_path)
        except Exception as exc:
            raise ExtractionError(str(exc), source_file=pdf_path.name) from exc

        content = []
        for page in pages:
            content.append(self._llm.text_block(
                f"--- PAGE {page['page_num']} ---"
            ))
            text = page.get("text", "").strip()
            if text:
                content.append(self._llm.text_block(
                    self._llm.truncate(text, get_settings().pdf_text_char_limit)
                ))
            if page.get("image_b64"):
                content.append(self._llm.image_block(page["image_b64"]))
        content.append(self._llm.text_block(_PROMPT))

        try:
            data = self._llm.call_json(
                messages=self._llm.user_message(content),
                max_completion_tokens=get_settings().llm_max_completion_tokens_large,
                context=f"concur {pdf_path.name}",
                required_keys=["report_header", "transactions", "totals"],
            )
        except Exception as exc:
            raise ExtractionError(str(exc), source_file=pdf_path.name) from exc

        doc = self._build_model(data, pdf_path.name)
        doc.processing_ms = int((time.monotonic() - t0) * 1000)
        doc.extracted_at  = datetime.now(timezone.utc).isoformat()

        log.info(
            "  extracted %d transaction(s) [%dms]",
            len(doc.transactions), doc.processing_ms,
        )
        return doc

    def _build_model(self, data: dict, source_file: str) -> ConcurReport:
        h = data.get("report_header", {})
        e = data.get("employee", {})
        t = data.get("totals", {})

        transactions = []
        for raw in data.get("transactions", []):
            attendees = raw.get("attendees", [])
            if not isinstance(attendees, list):
                attendees = [attendees] if attendees else []
            try:
                amount = float(raw.get("amount") or 0.0)
            except (TypeError, ValueError):
                amount = 0.0
            transactions.append(ConcurTransaction(
                transaction_date  = raw.get("transaction_date"),
                expense_type      = raw.get("expense_type"),
                business_purpose  = raw.get("business_purpose"),
                vendor            = raw.get("vendor"),
                payment_type      = raw.get("payment_type"),
                amount            = amount,
                cost_center       = raw.get("cost_center"),
                project           = raw.get("project"),
                attendees         = attendees,
                receipt_required  = raw.get("receipt_required"),
                notes             = raw.get("notes"),
            ))

        def _f(key: str) -> float:
            try:
                return float(t.get(key) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        totals = ConcurTotals(
            report_total          = _f("report_total"),
            personal_expenses     = _f("personal_expenses"),
            total_claimed         = _f("total_claimed"),
            amount_approved       = _f("amount_approved"),
            amount_due_employee   = _f("amount_due_employee"),
            amount_due_card       = _f("amount_due_card"),
            total_paid_by_company = _f("total_paid_by_company"),
        )

        audit = [
            AuditEntry(
                timestamp = a.get("timestamp"),
                actor     = a.get("actor"),
                action    = a.get("action"),
                detail    = a.get("detail"),
            )
            for a in data.get("audit_trail", [])
        ]

        return ConcurReport(
            source_file       = source_file,
            report_name       = h.get("report_name"),
            report_id         = h.get("report_id"),
            report_date       = h.get("report_date"),
            approval_status   = h.get("approval_status"),
            payment_status    = h.get("payment_status"),
            receipts_received = h.get("receipts_received"),
            currency          = h.get("currency"),
            employee_name     = e.get("name"),
            employee_id       = e.get("employee_id"),
            transactions      = transactions,
            totals            = totals,
            audit_trail       = audit,
        )

"""
llm_formatter.py
────────────────
LLM-powered description formatter using Azure OpenAI (gpt-4o-mini / gpt-5-mini).
Features:
  - Chain-of-thought prompting for context-aware decisions
  - Structured JSON outputs with confidence scoring
  - Receipt data enrichment from transaction memory
  - Batch processing with progress logging
  - Cache integration (caller must pass a LLMCache instance)
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import List, Optional

from .config import (
    AZURE_API_KEY, AZURE_ENDPOINT, AZURE_MODEL,
    LLM_APPLY_THRESHOLD, LLM_BATCH_SIZE,
    LLM_SYSTEM_PROMPT, LLM_ROW_SCHEMA,
    PROJECT_CFG,
)
from .models import Row
from .cache import LLMCache

log = logging.getLogger(__name__)

try:
    from openai import OpenAI
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False


class LLMFormatter:
    """
    Batches rows to the Azure OpenAI API and merges back structured
    JSON results. Caches results to avoid duplicate API calls.
    """

    def __init__(
        self,
        api_key:    str = AZURE_API_KEY,
        endpoint:   str = AZURE_ENDPOINT,
        model:      str = AZURE_MODEL,
        batch_size: int = LLM_BATCH_SIZE,
        cache:      Optional[LLMCache] = None,
        apply_threshold: float = LLM_APPLY_THRESHOLD,
    ):
        if not _OPENAI_AVAILABLE:
            raise ImportError(
                "openai package not installed. Run: pip install openai"
            )
        self.model           = model
        self.batch_size      = batch_size
        self.cache           = cache
        self.apply_threshold = apply_threshold
        self.client          = OpenAI(api_key=api_key, base_url=endpoint)
        self._total_calls    = 0
        self._cache_hits     = 0

    # ── Public API ─────────────────────────────────────────────────────────────
    def format_rows(self, rows: List[Row]) -> None:
        """
        Run LLM formatting in-place on all rows.
        Modifies: entity_desc_out, entity_expense_out,
                  entity_project_out, entity_cost_center_out,
                  vendor_out, flags, review_comment.
        """
        total = len(rows)
        for start in range(0, total, self.batch_size):
            end   = min(start + self.batch_size, total)
            batch = rows[start:end]
            log.info("  LLM batch %d–%d / %d", start + 1, end, total)
            payloads = [self._build_payload(r) for r in batch]
            results  = self._process_batch(payloads)
            for row, result in zip(batch, results):
                if result:
                    self._apply_result(row, result)
            time.sleep(0.25)  # Rate-limit courtesy delay

        log.info(
            "  LLM done: %d API calls, %d cache hits (%.0f%% saved)",
            self._total_calls,
            self._cache_hits,
            (self._cache_hits / max(self._total_calls + self._cache_hits, 1)) * 100,
        )

    # ── Payload building ──────────────────────────────────────────────────────
    @staticmethod
    def _build_payload(r: Row) -> dict:
        """
        Chain-of-thought payload for one row.
        Includes: original + current description, receipt data, trip context.
        """
        receipt_ctx = ""
        if r.receipt_data:
            rd = r.receipt_data
            parts = []
            if rd.get("ticket_number"):
                parts.append(f"ticket={rd['ticket_number']}")
            if rd.get("passenger"):
                parts.append(f"passenger={rd['passenger']}")
            if rd.get("route"):
                parts.append(f"route={rd['route']}")
            if rd.get("summary"):
                parts.append(f"receipt_summary={rd['summary'][:200]}")
            receipt_ctx = " | ".join(parts)

        return {
            "_row_idx":           r.idx,
            "employee":           r.full_name,
            "employee_id":        r.employee_id,
            "date":               str(r.tran_dt or ""),
            "amount":             round(r.amount, 2),
            "original_description": r.description,
            "current_description":  r.entity_desc_out or r.desc_out,
            "original_expense":   r.expense_code,
            "current_expense":    r.entity_expense_out or r.expense_out,
            "vendor_description": r.vendor_desc,
            "vendor_name":        r.vendor_out,
            "project":            str(
                r.entity_project_out
                if r.entity_project_out not in (None, "")
                else r.project_str()
            ),
            "dept":               r.entity_cost_center_out or r.cost_center,
            "entity":             r.entity,
            "receipt_context":    receipt_ctx,
        }

    # ── Batch processing ──────────────────────────────────────────────────────
    def _process_batch(self, payloads: List[dict]) -> List[dict]:
        results = [None] * len(payloads)

        # 1. Check cache
        cache_results = (
            self.cache.get_batch(payloads) if self.cache else [None] * len(payloads)
        )

        uncached_indices = []
        for i, cr in enumerate(cache_results):
            if cr is not None:
                results[i] = cr
                self._cache_hits += 1
            else:
                uncached_indices.append(i)

        if not uncached_indices:
            return results

        # 2. Call LLM for uncached rows
        uncached_payloads = [payloads[i] for i in uncached_indices]
        llm_results       = self._call_llm(uncached_payloads)

        # 3. Store in cache + merge
        if self.cache:
            self.cache.set_batch(uncached_payloads, llm_results)

        for i, res in zip(uncached_indices, llm_results):
            results[i] = res

        return results

    # ── LLM API call ──────────────────────────────────────────────────────────
    def _call_llm(self, payloads: List[dict]) -> List[dict]:
        self._total_calls += 1
        prompt = self._build_prompt(payloads)
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                max_tokens=4000,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": LLM_SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
            )
            raw = resp.choices[0].message.content or ""
            return self._parse_response(raw, len(payloads))
        except Exception as exc:
            log.warning("LLM call failed: %s", exc)
            return [{}] * len(payloads)

    def _build_prompt(self, payloads: List[dict]) -> str:
        return (
            f"Scrub the following {len(payloads)} expense row(s) for entity-tab output "
            f"using written rules as the sole source of truth.\n\n"
            f"Output schema per row:\n{json.dumps(LLM_ROW_SCHEMA, indent=2)}\n\n"
            f"Chain-of-thought instructions:\n"
            f"1. Read the original_description and current_description.\n"
            f"2. Check expense code vs description (e.g. Inflight Wifi → Info Services).\n"
            f"3. Check project vs dept alignment.\n"
            f"4. Use receipt_context (if provided) to fill missing flight routes or ticket info.\n"
            f"5. Propose description_fixed only when the format genuinely violates policy.\n"
            f"6. Set flag=true only for real issues requiring human review.\n"
            f"7. Confidence < 0.80 means don't auto-apply — just flag.\n\n"
            f"Return JSON: {{\"results\": [...]}}\n\n"
            f"Rows:\n{json.dumps(payloads, indent=2, default=str)}"
        )

    @staticmethod
    def _parse_response(raw: str, n: int) -> List[dict]:
        raw = re.sub(r'^```(?:json)?|```$', '', raw.strip(),
                     flags=re.MULTILINE).strip()
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return data[:n] + [{}] * max(0, n - len(data))
            if isinstance(data, dict):
                for v in data.values():
                    if isinstance(v, list):
                        return v[:n] + [{}] * max(0, n - len(v))
        except json.JSONDecodeError:
            pass
        return [{}] * n

    # ── Apply results back to Row ──────────────────────────────────────────────
    def _apply_result(self, r: Row, lr: dict) -> None:
        if not isinstance(lr, dict):
            return

        try:
            confidence = float(lr.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        r.llm_confidence = max(r.llm_confidence, confidence)

        # Accumulate applied rule IDs
        rule_ids = lr.get("rule_ids_applied") or []
        if isinstance(rule_ids, list):
            for rid in rule_ids:
                if rid and rid not in r.llm_rule_ids:
                    r.llm_rule_ids.append(rid)

        # Conflict notes always surfaced as flags
        if lr.get("conflict_note"):
            r.flag("Rule/reference conflict — LLM note", lr["conflict_note"])

        # High-confidence fixes auto-applied
        if confidence >= self.apply_threshold:
            if lr.get("description_fixed"):
                r.entity_desc_out = str(lr["description_fixed"]).strip()
            if lr.get("expense_code_fixed"):
                r.entity_expense_out = str(lr["expense_code_fixed"]).strip()
            if lr.get("project_fixed"):
                raw_proj = str(lr["project_fixed"]).strip()
                r.entity_project_out = (
                    int(raw_proj) if raw_proj.isdigit() else raw_proj
                )
            if lr.get("cost_center_fixed"):
                r.entity_cost_center_out = str(lr["cost_center_fixed"]).strip()

        # Vendor fixes applied regardless of confidence (vendor list is definitive)
        if lr.get("vendor_fixed"):
            r.vendor_out = str(lr["vendor_fixed"]).strip()
            r.vendor_changed = r.vendor_out != r.vendor_name

        # Flags
        if lr.get("flag"):
            r.flag(
                lr.get("flag_reason") or "LLM flagged for review",
                lr.get("comments") or "",
            )

        # Recompute len
        from .config import PROJECT_CFG as _pc
        r.len_value = (
            len(r.entity_desc_out or r.desc_out) +
            len(r.vendor_out) +
            _pc.len_overhead
        )

        # Mark changed
        r.changed = (
            r.desc_changed
            or r.pay_type_changed
            or r.expense_changed
            or r.vendor_changed
            or (r.entity_desc_out != r.desc_out)
            or (r.entity_expense_out != r.expense_out)
            or (r.entity_project_out != r.project)
            or (r.entity_cost_center_out != r.cost_center)
        )

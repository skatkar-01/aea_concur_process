"""
checkpointing.py
────────────────
Progress checkpointing for large batches.

If the scrubber is interrupted mid-run, the next invocation can
resume from the last saved checkpoint rather than reprocessing
everything from scratch.

Checkpoint file layout (JSON):
    {
      "input_path":   "/path/to/batch.xlsx",
      "input_mtime":  1234567890.0,
      "last_step":    "deterministic" | "llm" | "done",
      "rows":         [ {row dict} ... ]
    }
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import List, Optional

log = logging.getLogger(__name__)

# Steps in order
STEPS = ["load", "deterministic", "llm", "validate", "done"]


class Checkpoint:

    def __init__(self, checkpoint_dir: Path, input_path: Path):
        self.dir        = Path(checkpoint_dir)
        self.input_path = Path(input_path)
        self.dir.mkdir(parents=True, exist_ok=True)
        stem             = self.input_path.stem.replace(" ", "_")
        self._path       = self.dir / f"{stem}_checkpoint.json"

    # ── Public API ─────────────────────────────────────────────────────────────
    def save(self, step: str, rows: List) -> None:
        """Serialise rows and current step to disk."""
        try:
            data = {
                "input_path":  str(self.input_path),
                "input_mtime": self.input_path.stat().st_mtime,
                "saved_at":    time.time(),
                "last_step":   step,
                "rows":        [self._row_to_dict(r) for r in rows],
            }
            with open(self._path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, default=str)
            log.debug("Checkpoint saved at step '%s' → %s", step, self._path.name)
        except Exception as exc:
            log.warning("Checkpoint save failed: %s", exc)

    def load(self) -> Optional[dict]:
        """
        Return checkpoint dict if it exists AND the input file hasn't changed.
        Returns None if checkpoint is stale or missing.
        """
        if not self._path.exists():
            return None
        try:
            with open(self._path, encoding="utf-8") as fh:
                data = json.load(fh)
            # Validate freshness against input file mtime
            current_mtime = self.input_path.stat().st_mtime
            if abs(data.get("input_mtime", 0) - current_mtime) > 1.0:
                log.info("Checkpoint stale (input file changed) — starting fresh")
                return None
            log.info(
                "Checkpoint found: last step '%s', %d rows",
                data.get("last_step", "?"),
                len(data.get("rows", [])),
            )
            return data
        except Exception as exc:
            log.warning("Checkpoint load failed: %s — starting fresh", exc)
            return None

    def clear(self) -> None:
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            pass

    def step_complete(self, checkpoint_data: dict, step: str) -> bool:
        """Return True if this step has already been completed."""
        last = checkpoint_data.get("last_step", "")
        try:
            return STEPS.index(last) >= STEPS.index(step)
        except ValueError:
            return False

    # ── Serialisation ──────────────────────────────────────────────────────────
    @staticmethod
    def _row_to_dict(r) -> dict:
        from .models import Row
        if not isinstance(r, Row):
            return {}
        return {
            "idx":               r.idx,
            "first_name":        r.first_name,
            "middle_name":       r.middle_name,
            "last_name":         r.last_name,
            "blank":             r.blank,
            "tran_dt":           str(r.tran_dt or ""),
            "description":       r.description,
            "amount":            r.amount,
            "pay_type":          r.pay_type,
            "expense_code":      r.expense_code,
            "vendor_desc":       r.vendor_desc,
            "vendor_name":       r.vendor_name,
            "project":           str(r.project or ""),
            "cost_center":       r.cost_center,
            "report_purpose":    r.report_purpose,
            "employee_id":       r.employee_id,
            # Scrubbed values
            "desc_out":          r.desc_out,
            "pay_type_out":      r.pay_type_out,
            "expense_out":       r.expense_out,
            "vendor_out":        r.vendor_out,
            "entity_desc_out":   r.entity_desc_out,
            "entity_expense_out":r.entity_expense_out,
            "entity_project_out":str(r.entity_project_out or ""),
            "entity_cost_center_out": r.entity_cost_center_out,
            # Metadata
            "len_value":         r.len_value,
            "entity":            r.entity,
            "review_comment":    r.review_comment,
            "flags":             r.flags,
            "llm_confidence":    r.llm_confidence,
            "llm_rule_ids":      r.llm_rule_ids,
            # Change tracking
            "desc_changed":      r.desc_changed,
            "pay_type_changed":  r.pay_type_changed,
            "expense_changed":   r.expense_changed,
            "vendor_changed":    r.vendor_changed,
            "changed":           r.changed,
        }

    @staticmethod
    def dict_to_rows(rows_data: List[dict]) -> List:
        """Reconstruct Row objects from checkpoint data."""
        from .models import Row
        from datetime import datetime
        rows = []
        for d in rows_data:
            r = Row(idx=d.get("idx", 0))
            for attr in (
                "first_name", "middle_name", "last_name", "blank",
                "description", "pay_type", "expense_code", "vendor_desc",
                "vendor_name", "cost_center", "report_purpose", "employee_id",
                "desc_out", "pay_type_out", "expense_out", "vendor_out",
                "entity_desc_out", "entity_expense_out", "entity_cost_center_out",
                "len_value", "entity", "review_comment",
                "desc_changed", "pay_type_changed", "expense_changed",
                "vendor_changed", "changed", "llm_confidence",
            ):
                if attr in d:
                    setattr(r, attr, d[attr])

            # amount
            r.amount = float(d.get("amount", 0))

            # project
            proj_raw = str(d.get("project", "") or "")
            if proj_raw.isdigit():
                r.project = int(proj_raw)
            else:
                r.project = proj_raw or None

            # entity_project_out
            ep_raw = str(d.get("entity_project_out", "") or "")
            if ep_raw.isdigit():
                r.entity_project_out = int(ep_raw)
            else:
                r.entity_project_out = ep_raw or None

            # tran_dt
            tdt = d.get("tran_dt", "")
            if tdt and tdt not in ("None", ""):
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                    try:
                        r.tran_dt = datetime.strptime(tdt[:19], fmt)
                        break
                    except ValueError:
                        pass

            r.flags        = d.get("flags", [])
            r.llm_rule_ids = d.get("llm_rule_ids", [])

            rows.append(r)
        return rows

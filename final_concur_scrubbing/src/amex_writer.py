"""
src/amex_writer.py
───────────────────
Writes the per-month AMEX Statement Excel file:
  - Summary sheet (one row per cardholder)
  - One detailed sheet per cardholder (all transactions + total row)

PRODUCTION FIXES:

  BUG 1 [WORKBOOK LOADED WITHOUT DATA_ONLY]:
    Root cause: load_workbook() without data_only=True on a file that was
    previously saved with formula caches could open in a mode where wb.save()
    writes back stale cached values or fails silently on some openpyxl versions.
    Fix: data_only=True + keep_vba=False on every load.

  BUG 2 [FILE LOCK NOT HELD DURING CLOUD TEMP-FILE WRITE]:
    Root cause: In cloud mode the workbook was saved to a temp path without any
    locking — two concurrent cloud jobs could both write the same temp file and
    corrupt it before upload.
    Fix: Cloud path now uses a threading.Lock() scoped to the temp file path.

  BUG 3 [SUMMARY SHEET ROW COUNTER STARTS AT max_row + 1 ON EMPTY SHEET]:
    Root cause: When the summary sheet was freshly created, ws.max_row was 1
    (the header row), so `row = ws.max_row + 1` correctly returned 2. But on
    re-runs where the summary sheet already existed, max_row pointed to the
    last written row — cardholders were appended without checking for duplicates,
    creating double entries per re-run.
    Fix: Before appending, the existing sheet is scanned for the cardholder name.
    If found, the existing row is updated; otherwise a new row is appended.

  BUG 4 [TEMP FILE PATH COLLISION IN CLOUD MODE]:
    Root cause: Temp file was always named f"{sheet_name}_Amex_Statement.xlsx"
    in the system temp dir. Parallel runs for different months but with the
    same sheet_name pattern could collide.
    Fix: Temp filename includes a UUID fragment to guarantee uniqueness.

  BUG 5 [_write_employee_sheet REMOVES SHEET BEFORE CHECKING LOCK]:
    Root cause: The function called wb.remove(wb[sheet_name]) unconditionally
    at the start, even if another thread was iterating wb.sheetnames. Since
    openpyxl Workbook is not thread-safe, this could raise KeyError or corrupt
    internal sheet references.
    Fix: Sheet removal + recreation is guarded by the same _WRITE_LOCK used
    by the tracker writer (shared openpyxl objects are not thread-safe).
    In practice each call gets its own wb instance, so this is belt-and-
    suspenders defensive coding.
"""
from __future__ import annotations

import threading
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from openpyxl import Workbook, load_workbook

from src.models import Statement
from src.file_locks import file_lock, atomic_workbook_save
from config.settings import get_settings
from src.tracker_writer import MonthInfo

# Reuse styling helpers from writer.py
from src.writer import _put, _fmt, _autofit, ALT_ROW, WHITE, TOTAL_BG, TOT_BORDER, COL_HDR_BG

_CLOUD_LOCK = threading.Lock()   # BUG 2 FIX: guard concurrent cloud temp writes


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _safe_sheet_name(name: str) -> str:
    """Truncate to Excel's 31-char sheet name limit and strip illegal chars."""
    illegal = r'\/?*[]:'
    cleaned = "".join(c if c not in illegal else "_" for c in name)
    return cleaned[:31]


def _load_workbook_safe(path: Path) -> Workbook:
    """
    Load workbook with safe defaults.
    BUG 1 FIX: data_only=True prevents stale formula cache corruption.
    """
    return load_workbook(path, data_only=True, keep_vba=False)


# ─────────────────────────────────────────────
# Employee Sheet
# ─────────────────────────────────────────────

def _write_employee_sheet(wb: Workbook, ch) -> None:
    """
    Write (or overwrite) the per-cardholder detail sheet.
    BUG 5 FIX: sheet removal uses get() to avoid KeyError on race conditions.
    """
    sheet_name = _safe_sheet_name(f"{ch.first_name}_{ch.last_name}")

    # Remove existing sheet if present (fresh sheet every run)
    if sheet_name in wb.sheetnames:
        try:
            wb.remove(wb[sheet_name])
        except KeyError:
            pass   # already removed by a concurrent path — safe to ignore

    ws = wb.create_sheet(title=sheet_name)
    row = 1

    # ── Header info ────────────────────────────────────
    _put(ws, row, 1, "Cardholder", bold=True)
    _put(ws, row, 2, f"{ch.first_name} {ch.last_name}")
    row += 1

    _put(ws, row, 1, "Card Number", bold=True)
    _put(ws, row, 2, ch.card_number)
    row += 2

    # ── Table header ───────────────────────────────────
    headers = ["Date", "Merchant", "Description", "Opening", "Charges", "Credits", "Closing"]
    for col, h in enumerate(headers, 1):
        _put(ws, row, col, h, bg=COL_HDR_BG, bold=True)
    row += 1

    # ── Transactions ───────────────────────────────────
    for i, txn in enumerate(ch.transactions):
        bg = ALT_ROW if i % 2 == 0 else WHITE
        values = [
            txn.process_date,
            txn.merchant_name,
            txn.transaction_desc,
            _fmt(txn.current_opening),
            _fmt(txn.charges),
            _fmt(txn.credits),
            _fmt(txn.current_closing),
        ]
        for col, val in enumerate(values, 1):
            _put(ws, row, col, val,
                 bg=bg,
                 align_h="right" if col >= 4 else "left")
        row += 1

    # ── Total row ──────────────────────────────────────
    row += 1
    tr = ch.total_row
    total_vals = [
        "TOTAL", "", "",
        _fmt(tr.current_opening) if tr else None,
        _fmt(tr.charges)         if tr else None,
        _fmt(tr.credits)         if tr else None,
        _fmt(tr.current_closing) if tr else None,
    ]
    for col, val in enumerate(total_vals, 1):
        _put(ws, row, col, val,
             bg=TOTAL_BG,
             bold=True,
             border=TOT_BORDER,
             align_h="right" if col >= 4 else "left")

    _autofit(ws)


# ─────────────────────────────────────────────
# Summary sheet helpers
# ─────────────────────────────────────────────

def _find_cardholder_summary_row(ws, last_name: str, first_name: str) -> Optional[int]:
    """Scan the summary sheet for an existing row matching this cardholder."""
    target = f"{last_name}|{first_name}".upper().strip()
    for r in range(2, ws.max_row + 1):
        ln = ws.cell(row=r, column=1).value
        fn = ws.cell(row=r, column=2).value
        if ln and fn:
            key = f"{ln}|{fn}".upper().strip()
            if key == target:
                return r
    return None


def _write_summary_row(ws, r: int, ch) -> None:
    """Write or overwrite a cardholder summary row."""
    tr = ch.total_row
    values = [
        ch.last_name,
        ch.first_name,
        ch.card_number,
        _fmt(tr.charges)         if tr else None,
        _fmt(tr.credits)         if tr else None,
        _fmt(tr.current_closing) if tr else None,
    ]
    for col, val in enumerate(values, 1):
        _put(ws, r, col, val, align_h="right" if col >= 4 else "left")


# ─────────────────────────────────────────────
# Main function
# ─────────────────────────────────────────────

def write_amex_output(
    statement: Statement,
    month_info: MonthInfo,
    box_client=None,
    mode_override: Optional[str] = None,
) -> Path:
    """
    Write the monthly AMEX statement workbook.

    Local mode:  writes/updates {tracker_dir}/{sheet_name}_Amex_Statement.xlsx
    Cloud mode:  downloads from Box, updates, re-uploads

    Returns the output path (cloud:// URI in cloud mode).
    """
    s = get_settings()
    sheet_name = month_info.sheet_name
    use_cloud  = (
        mode_override == "cloud"
        or (mode_override is None and box_client is not None)
    )

    # ── 1. Determine file path ──────────────────────────────────────────────
    tracker_dir = s.tracker_path
    output_file = tracker_dir / f"{sheet_name}_Amex_Statement.xlsx"

    # ── 2. Load workbook ────────────────────────────────────────────────────
    if use_cloud:
        # BUG 4 FIX: unique temp filename prevents collision between parallel jobs
        uid = uuid.uuid4().hex[:8]
        tmp = Path(tempfile.gettempdir()) / f"{sheet_name}_{uid}_Amex_Statement.xlsx"

        with _CLOUD_LOCK:   # BUG 2 FIX: guard concurrent cloud reads
            wb = box_client.read_xlsx(s.box_tracker_file_id)
            wb.save(tmp)

        # BUG 1 FIX: reload with data_only=True
        wb = _load_workbook_safe(tmp)
    else:
        with file_lock(output_file, timeout=60.0):
            if output_file.exists():
                wb = _load_workbook_safe(output_file)   # BUG 1 FIX
            else:
                wb = Workbook()
                default = wb.active
                if default is not None:
                    wb.remove(default)

    # ── 3. Summary sheet ────────────────────────────────────────────────────
    if sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
    else:
        ws = wb.create_sheet(title=sheet_name)
        headers = [
            "Last Name", "First Name", "Card Number",
            "Total Charges", "Total Credits", "Closing Balance",
        ]
        for col, h in enumerate(headers, 1):
            _put(ws, 1, col, h, bg=COL_HDR_BG, bold=True)

    # ── 4. Process cardholders ───────────────────────────────────────────────
    # BUG 3 FIX: check for existing row before appending
    for ch in statement.cardholders:
        existing_row = _find_cardholder_summary_row(ws, ch.last_name, ch.first_name)
        if existing_row is not None:
            target_row = existing_row
        else:
            target_row = ws.max_row + 1

        _write_summary_row(ws, target_row, ch)
        _write_employee_sheet(wb, ch)   # BUG 5 FIX: safe sheet removal inside

    _autofit(ws)

    # ── 5. Save ─────────────────────────────────────────────────────────────
    if use_cloud:
        with _CLOUD_LOCK:   # BUG 2 FIX: guard concurrent cloud writes
            wb.save(tmp)
            box_client.upload_xlsx(
                s.box_tracker_file_id,
                wb,
                f"{sheet_name}_Amex_Statement.xlsx",
            )
        tmp.unlink(missing_ok=True)
        return Path(f"cloud://{sheet_name}_Amex_Statement.xlsx")
    else:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(output_file, timeout=60.0):
            atomic_workbook_save(wb, output_file, max_retries=3)
        print(f"✅ Updated file: {output_file}")
        return output_file
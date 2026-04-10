"""
utils/month_detector.py
────────────────────────
Parses year and month from a Box folder path like:
  C:/Users/SKatkar/Box/AEA - Concur/2026/03-MARCH/AmEx Statements...

Returns a MonthInfo dataclass used throughout the tracker pipeline.

Also classifies a PDF path as AMEX or Concur based on which subfolder it lives in.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from enum import Enum, auto

from src.tracker_writer import MonthInfo


# ── PDF type ──────────────────────────────────────────────────────────────────

class PdfType(Enum):
    AMEX   = auto()
    CONCUR = auto()
    UNKNOWN = auto()


# ── Month folder pattern ──────────────────────────────────────────────────────
# Matches: "03-MARCH", "3-MARCH", "03-March", "03_MARCH", "03 MARCH"
_MONTH_FOLDER_RE = re.compile(
    r"(?:^|[\\/])(\d{1,2})[-_ ]([A-Za-z]+)(?:[\\/]|$)"
)

_MONTH_NAMES = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
# e.g. {"january": 1, "february": 2, ..., "december": 12}


def _statement_date_for_month(year: int, month: int) -> str:
    """
    Returns a human-friendly statement date string for the column B header.
    Approximates the typical AMEX statement close date as the 4th of the month.
    Adjust if your statement always closes on a different day.
    """
    day = 4
    month_name = calendar.month_name[month]
    return f"{month_name} {day}, {year} Statement Total"


def detect_month(path: Path) -> Optional[MonthInfo]:
    """
    Walk up the path tree looking for a folder matching the month pattern.

    Args:
        path: Any file or folder path under the Box sync root.

    Returns:
        MonthInfo if a month folder is found in the path, else None.

    Examples:
        .../2026/03-MARCH/AmEx Statements/file.pdf  → MonthInfo(2026, 3, "March 2026", ...)
        .../2026/03-MARCH/Final Concur Reports/...  → MonthInfo(2026, 3, "March 2026", ...)
    """
    path_str = str(path)

    # ── Extract month folder ──────────────────────────────────────────────────
    match = _MONTH_FOLDER_RE.search(path_str)
    if not match:
        return None

    month_num_str, month_name_str = match.group(1), match.group(2)
    month_num  = int(month_num_str)
    month_key  = month_name_str.lower()

    # Validate month number
    if not (1 <= month_num <= 12):
        return None

    # Validate month name (must match the number)
    lookup = _MONTH_NAMES.get(month_key)
    if lookup is None:
        return None
    if lookup != month_num:
        return None

    # ── Extract year ──────────────────────────────────────────────────────────
    year: Optional[int] = None
    for part in Path(path_str).parts:
        if re.fullmatch(r"\d{4}", part):
            candidate = int(part)
            if 2020 <= candidate <= 2099:
                year = candidate
                break

    if year is None:
        return None

    month_long  = calendar.month_name[month_num]   # "March"
    sheet_name  = f"{month_long} {year}"           # "March 2026"
    col_b_hdr   = _statement_date_for_month(year, month_num)

    return MonthInfo(
        year=year,
        month=month_num,
        sheet_name=sheet_name,
        col_b_header=col_b_hdr,
    )


def classify_pdf(pdf_path: Path, amex_subfolder: str, concur_subfolder: str) -> PdfType:
    """
    Determine whether a PDF belongs to the AMEX or Concur pipeline
    based on which subfolder it lives in.

    Args:
        pdf_path:        Full path to the PDF file.
        amex_subfolder:  Configured AMEX subfolder string (from settings).
        concur_subfolder: Configured Concur subfolder string (from settings).

    Returns:
        PdfType.AMEX | PdfType.CONCUR | PdfType.UNKNOWN
    """
    path_str = str(pdf_path).replace("\\", "/")

    # Normalise subfolder separators for comparison
    amex_norm   = amex_subfolder.replace("\\", "/").lower()
    concur_norm = concur_subfolder.replace("\\", "/").lower()

    # Check each segment of the path
    lower = path_str.lower()
    if amex_norm in lower:
        return PdfType.AMEX
    if concur_norm in lower:
        return PdfType.CONCUR
    return PdfType.UNKNOWN

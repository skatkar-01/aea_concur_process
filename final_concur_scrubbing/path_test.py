from config.settings import get_settings
from src.tracker_writer import MonthInfo, patch_cardholder_row
from src.reconciler import TrackerRow

s = get_settings()
mi = MonthInfo(year=2026, month=3, sheet_name="March 2026", col_b_header="March 4, 2026 Statement Total")
row = TrackerRow(
    cardholder_name="CARDHOLDER_NAME",
    amex_total=123.45,
    concur_submitted=321.00,
    report_pdf=True,
    approvals=True,
    receipts=True,
    comments="test patch"
)
patched = patch_cardholder_row(row, mi, s.tracker_path)
print("patched:", patched, "tracker_path:", s.tracker_path)
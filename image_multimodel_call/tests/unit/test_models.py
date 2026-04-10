"""tests/unit/test_models.py — model layer unit tests."""
from decimal import Decimal
import pytest

from models.amex import parse_amount, fmt_amount
from models.enums import PageType, RecordStatus
from models.tracker import TrackerRecord


class TestParseAmount:
    def test_none_returns_none(self):             assert parse_amount(None) is None
    def test_empty_string_returns_none(self):     assert parse_amount("") is None
    def test_null_string_returns_none(self):      assert parse_amount("null") is None
    def test_zero_string(self):                   assert parse_amount("0.00") == Decimal("0")
    def test_positive(self):                      assert parse_amount("52.01") == Decimal("52.01")
    def test_comma_formatted(self):               assert parse_amount("1,477.97") == Decimal("1477.97")
    def test_parentheses_negative(self):          assert parse_amount("(90.73)") == Decimal("-90.73")
    def test_dash_string_returns_none(self):      assert parse_amount("-") is None
    def test_float_input(self):                   assert parse_amount(52.01) == Decimal("52.01")


class TestFmtAmount:
    def test_none_returns_empty(self):   assert fmt_amount(None) == ""
    def test_zero(self):                 assert fmt_amount(Decimal("0")) == "0.00"
    def test_positive(self):             assert fmt_amount(Decimal("52.01")) == "52.01"
    def test_negative(self):             assert fmt_amount(Decimal("-90.73")) == "-90.73"


class TestPageType:
    def test_is_receipt_true(self):      assert PageType.is_receipt("receipt_ride") is True
    def test_is_receipt_false(self):     assert PageType.is_receipt("concur_report") is False
    def test_is_concur_true(self):       assert PageType.is_concur("concur_report") is True
    def test_is_concur_audit(self):      assert PageType.is_concur("audit_trail") is True


class TestTrackerRecord:
    def test_approved_when_all_match(self):
        r = TrackerRecord(
            employee_name="Test User", employee_id="T01",
            period="JAN_2026", report_id="R01",
            amounts_match=True, all_receipts_matched=True,
        )
        r.update_status()
        assert r.status == RecordStatus.APPROVED

    def test_flagged_when_amount_mismatch(self):
        r = TrackerRecord(
            employee_name="Test User", employee_id="T01",
            period="JAN_2026", report_id="R01",
            amounts_match=False,
        )
        r.update_status()
        assert r.status == RecordStatus.FLAGGED

    def test_flagged_when_receipts_missing(self):
        r = TrackerRecord(
            employee_name="Test User", employee_id="T01",
            period="JAN_2026", report_id="R01",
            amounts_match=True, receipts_missing=1,
        )
        r.update_status()
        assert r.status == RecordStatus.FLAGGED

    def test_pending_when_amounts_unknown(self):
        r = TrackerRecord(
            employee_name="Test User", employee_id="T01",
            period="JAN_2026", report_id="R01",
        )
        r.update_status()
        assert r.status == RecordStatus.PENDING_REVIEW

    def test_to_tracker_row_has_all_columns(self):
        from models.tracker import TRACKER_COLUMNS
        r = TrackerRecord(
            employee_name="Test", employee_id=None,
            period="JAN_2026", report_id=None,
        )
        row = r.to_tracker_row()
        for col in TRACKER_COLUMNS:
            assert col in row, f"Missing column: {col}"

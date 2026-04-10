"""tests/unit/test_amount_reconciler.py"""
from decimal import Decimal
import pytest

from processors.amount_reconciler import AmountReconciler


class TestAmountReconciler:
    def setup_method(self):
        self.reconciler = AmountReconciler()

    def test_exact_match(self, amex_statement, concur_report):
        result = self.reconciler.reconcile(amex_statement, concur_report)
        assert result.matched is True
        assert result.difference == 0.0

    def test_within_tolerance(self, amex_statement, concur_report):
        # Adjust concur total slightly within $0.02 tolerance
        concur_report.totals.total_claimed = 52.02
        result = self.reconciler.reconcile(amex_statement, concur_report)
        assert result.matched is True

    def test_outside_tolerance(self, amex_statement, concur_report):
        concur_report.totals.total_claimed = 55.00
        result = self.reconciler.reconcile(amex_statement, concur_report)
        assert result.matched is False
        assert result.difference == pytest.approx(2.99, abs=0.01)

    def test_missing_cardholder(self, amex_statement, concur_report):
        concur_report.employee_name = "UNKNOWN PERSON"
        result = self.reconciler.reconcile(amex_statement, concur_report)
        assert result.matched is False
        assert result.amex_total is None
        assert "No AMEX cardholder" in result.note

    def test_extract_last_name_first_last(self):
        assert AmountReconciler._extract_last_name("Nicholas Alers") == "Alers"

    def test_extract_last_name_last_first(self):
        assert AmountReconciler._extract_last_name("Alers, Nicholas") == "Alers"

    def test_extract_last_name_single(self):
        assert AmountReconciler._extract_last_name("Alers") == "Alers"

    def test_extract_last_name_empty(self):
        assert AmountReconciler._extract_last_name("") == ""

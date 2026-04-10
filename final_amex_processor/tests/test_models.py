"""
tests/test_models.py
─────────────────────
Unit tests for Pydantic domain models.
Run with:  pytest tests/ -v
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models import Statement, Cardholder, Transaction


# ── Transaction ───────────────────────────────────────────────────────────────

class TestTransaction:
    def test_amount_coercion_string_float(self):
        txn = Transaction(charges="1,234.56")
        assert txn.charges == pytest.approx(1234.56)

    def test_amount_coercion_dollar_sign(self):
        txn = Transaction(charges="$99.00")
        assert txn.charges == pytest.approx(99.00)

    def test_amount_coercion_parentheses_negative(self):
        txn = Transaction(credits="(90.73)")
        assert txn.credits == pytest.approx(-90.73)

    def test_amount_blank_is_none(self):
        txn = Transaction(charges="", current_opening="null")
        assert txn.charges is None
        assert txn.current_opening is None

    def test_amount_none_stays_none(self):
        txn = Transaction(charges=None)
        assert txn.charges is None

    def test_date_passthrough(self):
        txn = Transaction(process_date="01/15/2026")
        assert txn.process_date == "01/15/2026"

    def test_date_null_string_normalised(self):
        txn = Transaction(process_date="null")
        assert txn.process_date is None

    def test_str_null_string_normalised(self):
        txn = Transaction(merchant_name="null", last_name="null")
        assert txn.merchant_name is None
        assert txn.last_name is None


# ── Cardholder ────────────────────────────────────────────────────────────────

class TestCardholder:
    def test_name_inherited_to_transactions(self):
        ch = Cardholder(
            last_name="DOE",
            first_name="JANE",
            card_number="3782-123456-12345",
            transactions=[
                Transaction(last_name=None, first_name=None, card_number=None)
            ],
        )
        txn = ch.transactions[0]
        assert txn.last_name  == "DOE"
        assert txn.first_name == "JANE"
        assert txn.card_number == "3782-123456-12345"

    def test_name_not_overwritten_if_set(self):
        ch = Cardholder(
            last_name="DOE",
            transactions=[Transaction(last_name="SMITH")],
        )
        assert ch.transactions[0].last_name == "SMITH"


# ── Statement ─────────────────────────────────────────────────────────────────

class TestStatement:
    def _make_statement(self, n_txns: int = 3) -> Statement:
        txns = [Transaction(charges=10.0) for _ in range(n_txns)]
        ch   = Cardholder(last_name="DOE", transactions=txns)
        return Statement(
            company_name="ACME CORP",
            period="JAN_042026",
            cardholders=[ch],
        )

    def test_total_transactions(self):
        stmt = self._make_statement(n_txns=5)
        assert stmt.total_transactions == 5

    def test_total_cardholders(self):
        stmt = self._make_statement()
        assert stmt.total_cardholders == 1

    def test_empty_statement(self):
        stmt = Statement()
        assert stmt.total_transactions == 0
        assert stmt.total_cardholders  == 0

    def test_validate_from_raw_dict(self):
        raw = {
            "company_name": "TEST CO",
            "statement_type": "CORPORATE",
            "period": "FEB_2026",
            "cardholders": [
                {
                    "last_name": "BAKER",
                    "first_name": "CHARLIE",
                    "card_number": "3782-123456-00001",
                    "transactions": [
                        {
                            "process_date": "02/01/2026",
                            "merchant_name": "AMAZON",
                            "transaction_desc": "PRIME",
                            "charges": "19.99",
                            "credits": None,
                            "current_opening": None,
                            "current_closing": None,
                            "is_total_row": False,
                        }
                    ],
                    "total_row": {
                        "charges": "19.99",
                        "is_total_row": True,
                    },
                }
            ],
        }
        stmt = Statement.model_validate(raw)
        assert stmt.company_name == "TEST CO"
        assert stmt.cardholders[0].transactions[0].charges == pytest.approx(19.99)

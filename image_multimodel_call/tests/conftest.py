"""
tests/conftest.py
Shared fixtures available to all tests.
"""
from __future__ import annotations
import pytest
from decimal import Decimal
from unittest.mock import MagicMock

from models.amex import AmexStatement, AmexCardholder, AmexTransaction
from models.concur import ConcurReport, ConcurTransaction, ConcurTotals
from models.enums import ExpenseType, ExtractionMethod, MatchConfidence, MatchStatus
from models.receipt import Receipt


# ── AMEX fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def amex_transaction():
    return AmexTransaction(
        last_name        = "ALERS",
        first_name       = "NICHOLAS",
        card_number      = "3792-197312-01009",
        process_date     = "01/27/2026",
        merchant_name    = "UBER",
        transaction_desc = "UBER TRIP TO BOD DINNER",
        current_opening  = None,
        charges          = Decimal("52.01"),
        credits          = None,
        current_closing  = None,
        is_total_row     = False,
    )


@pytest.fixture
def amex_total_row():
    return AmexTransaction(
        last_name="ALERS", first_name="", card_number="",
        process_date=None, merchant_name=None, transaction_desc=None,
        current_opening=Decimal("0.00"),
        charges=Decimal("52.01"),
        credits=Decimal("0.00"),
        current_closing=Decimal("52.01"),
        is_total_row=True,
    )


@pytest.fixture
def amex_cardholder(amex_transaction, amex_total_row):
    return AmexCardholder(
        last_name    = "ALERS",
        first_name   = "NICHOLAS",
        card_number  = "3792-197312-01009",
        transactions = [amex_transaction],
        total_row    = amex_total_row,
    )


@pytest.fixture
def amex_statement(amex_cardholder):
    return AmexStatement(
        source_file    = "ALERS_N_JAN_042026.pdf",
        company_name   = "AEA INVESTORS",
        statement_type = "AMERICAN EXPRESS STATEMENT",
        period         = "JAN_042026",
        cardholders    = [amex_cardholder],
        extraction_method = ExtractionMethod.AZURE_HYBRID,
        confidence     = 0.95,
    )


# ── Concur fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def concur_transaction():
    return ConcurTransaction(
        transaction_date  = "01/27/2026",
        expense_type      = "Car Service",
        business_purpose  = "Home to BOD Dinner Spectrum",
        vendor            = "UBER",
        payment_type      = "American Express Corporate Card",
        amount            = 52.01,
        cost_center       = "VAIP-VAIP Team",
        project           = "6416-Spectrum Control",
        attendees         = ["Nicholas Alers"],
        receipt_required  = "Yes",
    )


@pytest.fixture
def concur_report(concur_transaction):
    return ConcurReport(
        source_file       = "Alers_52_01.pdf",
        report_name       = "Nick Alers Jan 26 AMEX",
        report_id         = "7BE4679F86E0488A99C6",
        report_date       = "02/10/2026",
        approval_status   = "Approved",
        payment_status    = "Processing Payment",
        receipts_received = "Yes",
        currency          = "US, Dollar",
        employee_name     = "Nicholas Alers",
        employee_id       = "A06L",
        transactions      = [concur_transaction],
        totals            = ConcurTotals(
            report_total=52.01, total_claimed=52.01,
            amount_approved=52.01, amount_due_card=52.01,
            total_paid_by_company=52.01,
        ),
    )


# ── Receipt fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def uber_receipt():
    return Receipt(
        source_pages        = [4],
        receipt_type        = ExpenseType.RIDE,
        vendor              = "Uber",
        date                = "Jan 27, 2026",
        total_charged       = 52.01,
        base_fare           = 36.61,
        taxes               = 4.65,
        tip                 = 6.78,
        fees                = {
            "mta_congestion_surcharge": 1.50,
            "ny_congestion_fee":        2.75,
            "ny_state_black_car_fund":  0.92,
            "ny_state_benefits":        0.08,
        },
        payment_method      = "American Express",
        payment_last4       = "3009",
        origin_address      = "12 W 72nd St, New York, NY",
        destination_address = "57 W 57th St, New York, NY",
        driver_name         = "Rajinder",
    )


# ── Mock Azure client ─────────────────────────────────────────────────────────

@pytest.fixture
def mock_azure_client():
    client = MagicMock()
    client.text_block  = lambda text: {"type": "text", "text": text}
    client.image_block = lambda b64, mt="image/png": {"type": "image_url", "image_url": {"url": f"data:{mt};base64,{b64}"}}
    client.user_message = lambda content: [{"role": "user", "content": content}]
    client.system_user_message = lambda sys, content: [
        {"role": "system", "content": sys},
        {"role": "user",   "content": content},
    ]
    client.truncate = lambda text, n: text[:n]
    return client

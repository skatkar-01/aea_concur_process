"""tests/unit/test_receipt_matcher.py"""
import pytest
from unittest.mock import MagicMock

from models.enums import MatchStatus
from models.tracker import MatchConfidence
from processors.receipt_matcher import ReceiptMatcher


def _make_matcher(llm_response: dict) -> ReceiptMatcher:
    client = MagicMock()
    client.text_block  = lambda t: {"type": "text", "text": t}
    client.image_block = lambda b, m="image/png": {}
    client.user_message = lambda c: [{"role": "user", "content": c}]
    client.truncate = lambda t, n: t
    client.call_json.return_value = llm_response
    return ReceiptMatcher(client)


class TestHardAmountCheck:
    def setup_method(self):
        self.matcher = _make_matcher({})

    def test_exact_match(self, concur_transaction, uber_receipt):
        result = self.matcher._hard_amount_check(concur_transaction, uber_receipt, 0)
        assert result.matched is True

    def test_within_tolerance(self, concur_transaction, uber_receipt):
        uber_receipt.total_charged = 52.02   # $0.01 diff
        result = self.matcher._hard_amount_check(concur_transaction, uber_receipt, 0)
        assert result.matched is True

    def test_outside_tolerance(self, concur_transaction, uber_receipt):
        uber_receipt.total_charged = 55.00
        result = self.matcher._hard_amount_check(concur_transaction, uber_receipt, 0)
        assert result.matched is False
        assert "≠" in result.note

    def test_zero_receipt_is_mismatch(self, concur_transaction, uber_receipt):
        uber_receipt.total_charged = 0.0
        result = self.matcher._hard_amount_check(concur_transaction, uber_receipt, 0)
        assert result.matched is False


class TestMinConfidence:
    def test_low_wins(self):
        assert ReceiptMatcher._min_confidence("LOW", "HIGH") == "LOW"
    def test_medium_beats_low(self):
        assert ReceiptMatcher._min_confidence("MEDIUM", "LOW") == "LOW"
    def test_same_returns_same(self):
        assert ReceiptMatcher._min_confidence("HIGH", "HIGH") == "HIGH"


class TestMissingReceipt:
    def test_no_receipts_all_missing(self, concur_transaction, mock_azure_client):
        matcher = ReceiptMatcher(mock_azure_client)
        results = matcher.match([concur_transaction], [])
        assert len(results) == 1
        assert results[0].status == MatchStatus.MISSING

    def test_no_transactions_returns_empty(self, uber_receipt, mock_azure_client):
        matcher = ReceiptMatcher(mock_azure_client)
        results = matcher.match([], [uber_receipt])
        assert results == []

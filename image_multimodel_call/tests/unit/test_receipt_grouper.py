"""tests/unit/test_receipt_grouper.py"""
import pytest
from unittest.mock import MagicMock

from models.enums import PageType
from processors.receipt_grouper import (
    MultiPageReceiptGrouper,
    _has_continuation,
    _has_terminal,
    _has_final_total,
    _vendor_match,
    _vendor_hint,
)


def _page(num: int, text: str = "", page_type: str = "receipt_ride",
          vendor: str = None, amount: str = None, date: str = None) -> dict:
    return {
        "page_num": num,
        "text": text,
        "image_b64": None,
        "is_image_dominant": not bool(text),
        "classification": {
            "page_type":      page_type,
            "vendor_name":    vendor,
            "amount_visible": amount,
            "date_visible":   date,
            "confidence":     "HIGH",
        },
    }


class TestPatterns:
    def test_continuation_page_x_of_y(self):
        assert _has_continuation("Page 2 of 4") is True

    def test_continuation_continued_on(self):
        assert _has_continuation("continued on next page") is True

    def test_continuation_footer(self):
        assert _has_continuation("some text\n2/4") is True

    def test_continuation_price_not_matched(self):
        # "$12/day" should NOT match as a page footer
        assert _has_continuation("Rate: $12/day") is False

    def test_terminal_auth_code(self):
        assert _has_terminal("Auth Code: 123456") is True

    def test_terminal_amount_paid(self):
        assert _has_terminal("Amount Paid $52.01") is True

    def test_final_total_present(self):
        assert _has_final_total("Total Due: $52.01") is True

    def test_final_total_absent(self):
        assert _has_final_total("Item 1: $20.00") is False


class TestVendorMatch:
    def test_exact_match(self):             assert _vendor_match("Uber", "Uber") is True
    def test_case_insensitive(self):        assert _vendor_match("UBER", "uber") is True
    def test_partial_overlap(self):         assert _vendor_match("Uber", "Uber Technologies") is True
    def test_no_overlap(self):              assert _vendor_match("Uber", "Delta Air Lines") is False
    def test_short_token_no_match(self):    assert _vendor_match("A", "Abc Corp") is False
    def test_none_returns_false(self):      assert _vendor_match(None, "Uber") is False


class TestGrouper:
    def _make_grouper(self, merge: bool = False) -> MultiPageReceiptGrouper:
        client = MagicMock()
        client.text_block  = lambda t: {"type": "text", "text": t}
        client.image_block = lambda b, m="image/png": {}
        client.user_message = lambda c: [{"role": "user", "content": c}]
        client.truncate = lambda t, n: t
        client.call_json.return_value = {
            "same_receipt": merge,
            "confidence":   "HIGH",
            "reason":       "mock",
        }
        return MultiPageReceiptGrouper(client)

    def test_single_page_returns_one_group(self):
        grouper = self._make_grouper()
        pages   = [_page(1, "Uber receipt Total $52.01\nAmount Paid $52.01")]
        groups  = grouper.group(pages)
        assert len(groups) == 1
        assert groups[0].is_multi_page is False

    def test_continuation_signal_merges(self):
        grouper = self._make_grouper()
        pages   = [
            _page(1, "Hotel Marriott\nPage 1 of 2\nRoom charges $300", "receipt_hotel", "Marriott"),
            _page(2, "Taxes $45\nTotal $345", "receipt_hotel", "Marriott"),
        ]
        groups = grouper.group(pages)
        assert len(groups) == 1
        assert groups[0].is_multi_page is True

    def test_terminal_signal_separates(self):
        grouper = self._make_grouper()
        pages   = [
            _page(1, "Uber receipt\nTotal Due $52.01\nAmount Paid $52.01\nAuth: 123", "receipt_ride"),
            _page(2, "Delta Airlines\nFlight NYC-LAX\nTotal $450", "receipt_flight"),
        ]
        groups = grouper.group(pages)
        assert len(groups) == 2

    def test_same_vendor_merges(self):
        grouper = self._make_grouper()
        pages   = [
            _page(1, "Marriott Hotels\nRoom charges", "receipt_hotel", "Marriott"),
            _page(2, "Marriott Hotels\nTaxes and fees", "receipt_hotel", "Marriott"),
        ]
        groups = grouper.group(pages)
        assert len(groups) == 1

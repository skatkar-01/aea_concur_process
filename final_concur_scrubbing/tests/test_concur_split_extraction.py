from __future__ import annotations

from types import SimpleNamespace

from src.concur_extractor import (
    CONCUR_STAGE_RECEIPTS,
    CONCUR_STAGE_RECONCILIATION,
    CONCUR_STAGE_TRANSACTIONS,
    _build_reconciliation_prompt,
    _merge_concur_stage_payloads,
    _stage_model_list,
)
from src.models import ConcurRecord


def test_stage_model_list_uses_distinct_global_models_as_stage_primaries():
    settings = SimpleNamespace(
        azure_openai_model="gpt-trxn",
        azure_openai_model1="gpt-receipts",
        azure_openai_model2="gpt-recon",
        azure_openai_concur_transaction_model="",
        azure_openai_concur_receipt_model="",
        azure_openai_concur_reconciliation_model="",
    )

    assert _stage_model_list(settings, CONCUR_STAGE_TRANSACTIONS)[0] == "gpt-trxn"
    assert _stage_model_list(settings, CONCUR_STAGE_RECEIPTS)[0] == "gpt-receipts"
    assert _stage_model_list(settings, CONCUR_STAGE_RECONCILIATION)[0] == "gpt-recon"


def test_stage_model_list_allows_concur_specific_overrides():
    settings = SimpleNamespace(
        azure_openai_model="gpt-global",
        azure_openai_model1="gpt-fallback-1",
        azure_openai_model2="gpt-fallback-2",
        azure_openai_concur_transaction_model="gpt-trxn-special",
        azure_openai_concur_receipt_model="gpt-receipt-special",
        azure_openai_concur_reconciliation_model="gpt-recon-special",
    )

    assert _stage_model_list(settings, CONCUR_STAGE_TRANSACTIONS) == [
        "gpt-trxn-special",
        "gpt-global",
        "gpt-fallback-1",
        "gpt-fallback-2",
    ]
    assert _stage_model_list(settings, CONCUR_STAGE_RECEIPTS)[0] == "gpt-receipt-special"
    assert _stage_model_list(settings, CONCUR_STAGE_RECONCILIATION)[0] == "gpt-recon-special"


def test_merge_stage_payloads_produces_valid_concur_record():
    transaction_payload = {
        "transactions": [
            {
                "transaction_id": "txn_1",
                "transaction_date": "03/01/2026",
                "vendor_description": "Hotel",
                "amount": "$100.00",
            }
        ],
        "employee_report": {
            "employee_name": "Jane Doe",
            "report_id": "R-1",
            "total_amount_claimed": "$100.00",
        },
        "approval_log": [{"status": "Approved"}],
        "report_summary": {"approval_comment": None},
    }
    receipt_payload = {
        "receipts": [
            {
                "receipt_id": "rcp_1",
                "vendor": "Hotel",
                "amount": "$100.00",
                "line_items": "[{'name': 'Room Charge', 'amount': '$100.00'}]",
            }
        ]
    }
    reconciliation_payload = {
        "reconciliation": [
            {
                "transaction_id": "txn_1",
                "receipt_id": "rcp_1",
                "match_status": "matched",
                "confidence": "high",
                "comment": "Good",
            }
        ],
        "report_summary": {"reconciliation_comment": None},
    }

    merged = _merge_concur_stage_payloads(
        transaction_payload,
        receipt_payload,
        reconciliation_payload,
    )
    record = ConcurRecord.model_validate(merged)

    assert record.cardholder_name == "DOE, JANE"
    assert record.receipts[0].line_items == [{"name": "Room Charge", "amount": "$100.00"}]
    assert record.matched_count == 1


def test_reconciliation_prompt_embeds_extracted_context():
    prompt = _build_reconciliation_prompt(
        {"transactions": [{"transaction_id": "txn_1"}], "employee_report": {}, "approval_log": []},
        {"receipts": [{"receipt_id": "rcp_1"}]},
    )

    assert "EXTRACTED INPUT DATA FOR RECONCILIATION" in prompt
    assert '"transaction_id": "txn_1"' in prompt
    assert '"receipt_id": "rcp_1"' in prompt

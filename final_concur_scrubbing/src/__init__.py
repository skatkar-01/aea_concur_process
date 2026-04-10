from __future__ import annotations

from importlib import import_module

from .models import (
    Cardholder,
    ConcurApprovalEntry,
    ConcurEmployeeReport,
    ConcurReceipt,
    ConcurReconEntry,
    ConcurRecord,
    ConcurTransaction,
    ConcurTransactionRow,
    Statement,
    Transaction,
)

_LAZY_EXPORTS = {
    "extract_statement": ("src.extractor", "extract_statement"),
    "extract_concur_record": ("src.concur_extractor", "extract_concur_record"),
    "write_xlsx": ("src.writer", "write_xlsx"),
    "reconcile": ("src.reconciler", "reconcile"),
    "reconcile_amex_only": ("src.reconciler", "reconcile_amex_only"),
    "TrackerRow": ("src.reconciler", "TrackerRow"),
    "run_job": ("src.runner", "run_job"),
    "RunResult": ("src.runner", "RunResult"),
}

__all__ = [
    "Cardholder",
    "Statement",
    "Transaction",
    "ConcurRecord",
    "ConcurTransactionRow",
    "ConcurEmployeeReport",
    "ConcurApprovalEntry",
    "ConcurReceipt",
    "ConcurReconEntry",
    "ConcurTransaction",
    *_LAZY_EXPORTS,
]


def __getattr__(name: str):
    if name not in _LAZY_EXPORTS:
        raise AttributeError(name)
    module_name, attr_name = _LAZY_EXPORTS[name]
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value

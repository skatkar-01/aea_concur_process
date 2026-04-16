"""
transaction_memory.py - Workbook-backed transaction memory.

Loads historical Concur exports from a memory folder, parses the transaction
and receipt sheets, and returns matching rows for the scrubbing engine.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd


log = logging.getLogger(__name__)


class TransactionMemory:
    def __init__(self, memory_folder: Path = None):
        self.memory_folder = Path(memory_folder) if memory_folder else None
        self.transactions_df = pd.DataFrame()
        self.receipts_df = pd.DataFrame()
        self.reconciliation_df = pd.DataFrame()
        self.memory_df = pd.DataFrame()
        self.transactions: List[Dict] = []
        self.receipts: Dict[str, Dict] = {}
        self._loaded_files = set()
        if self.memory_folder:
            self.load_path(self.memory_folder)

    def load_path(self, path: Path) -> int:
        path = Path(path)
        if path.is_file():
            return 1 if self.load_file(path) else 0
        if path.is_dir():
            return self.load_directory(path)
        log.warning("Transaction memory path not found: %s", path)
        return 0

    def load_directory(self, directory: Path) -> int:
        directory = Path(directory)
        if not directory.is_dir():
            log.warning("Transaction memory folder not found: %s", directory)
            return 0

        files = []
        seen = set()
        for pattern in ("*.xlsx", "*.xlsm"):
            for file_path in directory.rglob(pattern):
                resolved = file_path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                files.append(file_path)

        files.sort()
        loaded = 0
        for file_path in files:
            if self.load_file(file_path):
                loaded += 1
        return loaded

    def load_file(self, path: Path) -> bool:
        path = Path(path)
        if not path.exists():
            log.warning("Transaction memory file not found: %s", path)
            return False

        resolved = str(path.resolve())
        if resolved in self._loaded_files:
            return True

        try:
            parsed = self._parse_workbook(path)
        except Exception as exc:
            log.warning("Failed to parse memory file %s: %s", path.name, exc)
            return False

        if not parsed["memory_rows"]:
            return False

        self._append_tables(parsed)
        self._loaded_files.add(resolved)
        return True

    def add_batch(self, transactions: List[Dict]) -> None:
        """Append the current batch to memory for in-run context matching."""
        if not transactions:
            return

        rows = []
        for txn in transactions:
            row = {
                "row_type": "current_batch",
                "source_file": "current_batch",
                "source_path": "current_batch",
            }
            row.update(self._normalize_transaction(txn))
            row.update(self._receipt_to_context({}))
            rows.append(row)

        self.memory_df = self._concat_frame(self.memory_df, rows)
        self.transactions = self._records_from_frame(self.memory_df)

    def get_by_employee_id(self, emp_id: str) -> List[Dict]:
        if self.memory_df.empty or "employee_id" not in self.memory_df.columns:
            return []
        key = self._normalize_text(emp_id).upper()
        if not key:
            return []
        mask = self.memory_df["employee_id"].fillna("").astype(str).str.upper() == key
        return self._records_from_frame(self.memory_df.loc[mask])

    def get_by_name(self, last_name: str, first_name: str = "") -> List[Dict]:
        if self.memory_df.empty:
            return []
        last_key = self._normalize_text(last_name).lower()
        first_key = self._normalize_text(first_name).lower()
        if not last_key and not first_key:
            return []

        df = self.memory_df.copy()
        if last_key and "employee_last_name" in df.columns:
            df = df[df["employee_last_name"].fillna("").astype(str).str.lower().str.contains(last_key, na=False)]
        if first_key and "employee_first_name" in df.columns and not df.empty:
            df = df[df["employee_first_name"].fillna("").astype(str).str.lower().str.contains(first_key, na=False)]
        return self._records_from_frame(df)

    def get_all_transactions(self, emp_id: str) -> List[Dict]:
        return self.get_by_employee_id(emp_id)

    def get_receipt_data(self, transaction_id: str = None) -> Optional[Dict]:
        if not transaction_id or self.memory_df.empty:
            return None
        txn_id = self._normalize_text(transaction_id)
        if "transaction_id" in self.memory_df.columns:
            match = self.memory_df[self.memory_df["transaction_id"].fillna("").astype(str) == txn_id]
            if not match.empty:
                return self._clean_record(match.iloc[0].to_dict())
        if "receipt_id" in self.receipts_df.columns:
            match = self.receipts_df[self.receipts_df["receipt_id"].fillna("").astype(str) == txn_id]
            if not match.empty:
                return self._clean_record(match.iloc[0].to_dict())
        return None

    def find_similar(self, txn: Dict, top_k: int = 5) -> List[Dict]:
        if self.memory_df.empty:
            return []
        query = self._normalize_transaction(txn)
        candidates = self._candidate_frame(query)
        if candidates.empty:
            return []

        rows = []
        for _, row in candidates.iterrows():
            record = self._clean_record(row.to_dict())
            if self._is_self_match(query, record):
                continue
            record["_match_score"] = round(self._score_match(query, record), 4)
            rows.append(record)
        rows.sort(key=lambda item: item.get("_match_score", 0), reverse=True)
        return rows[: max(1, min(top_k, len(rows)))]

    def find_original_for_refund(self, refund_txn: Dict) -> Optional[Dict]:
        amount = self._to_float(refund_txn.get("amount"))
        if amount >= 0 or self.memory_df.empty:
            return None

        query = self._normalize_transaction(refund_txn)
        candidates = self._candidate_frame(query)
        if candidates.empty:
            candidates = self.memory_df.copy()

        best = None
        best_score = -1.0
        target = abs(amount)
        for _, row in candidates.iterrows():
            record = self._clean_record(row.to_dict())
            if self._is_self_match(query, record):
                continue
            cand_amount = self._to_float(record.get("amount"))
            if cand_amount <= 0 or abs(cand_amount - target) > 5.0:
                continue
            score = self._score_match(query, record)
            if score > best_score:
                best = record
                best_score = score
        return best

    def group_by_trip(self, employee: str, date_range: Optional[tuple] = None) -> List[List[Dict]]:
        if self.memory_df.empty:
            return []
        employee_key = self._normalize_text(employee).lower()
        if not employee_key:
            return []
        df = self.memory_df.copy()
        if "employee_name" in df.columns:
            df = df[df["employee_name"].fillna("").astype(str).str.lower().str.contains(employee_key, na=False)]
        if date_range and not df.empty and "transaction_date" in df.columns:
            start, end = date_range
            df = df[df["transaction_date"].fillna("").astype(str).between(str(start), str(end))]
        rows = self._records_from_frame(df)
        return [rows] if rows else []

    def _append_tables(self, parsed: Dict) -> None:
        self.transactions_df = self._concat_frame(self.transactions_df, parsed["transaction_rows"])
        self.receipts_df = self._concat_frame(self.receipts_df, parsed["receipt_rows"])
        self.reconciliation_df = self._concat_frame(
            self.reconciliation_df, parsed["reconciliation_rows"]
        )
        self.memory_df = self._concat_frame(self.memory_df, parsed["memory_rows"])

        if not self.memory_df.empty:
            subset = [c for c in ("source_path", "transaction_id") if c in self.memory_df.columns]
            if subset:
                self.memory_df = self.memory_df.drop_duplicates(subset=subset, keep="last")

        self.transactions = self._records_from_frame(self.transactions_df)
        self.receipts = {
            str(row.get("receipt_id", "")): row
            for row in self._records_from_frame(self.receipts_df)
            if str(row.get("receipt_id", "")).strip()
        }

    def _concat_frame(self, current: pd.DataFrame, rows: List[Dict]) -> pd.DataFrame:
        if not rows:
            return current if current is not None else pd.DataFrame()
        frame = pd.DataFrame(rows)
        if current is None or current.empty:
            return frame
        return pd.concat([current, frame], ignore_index=True)

    def _parse_workbook(self, path: Path) -> Dict[str, List[Dict]]:
        employee_meta = self._parse_employee_report(path)
        transactions = self._parse_transactions_sheet(path)
        receipts = self._parse_receipts_sheet(path)
        reconciliation = self._parse_reconciliation_sheet(path)

        receipt_lookup = {
            str(row.get("receipt_id", "")).strip(): row
            for row in receipts
            if str(row.get("receipt_id", "")).strip()
        }
        txn_to_receipt = {
            str(row.get("transaction_id", "")).strip(): str(row.get("receipt_id", "")).strip()
            for row in reconciliation
            if str(row.get("transaction_id", "")).strip() and str(row.get("receipt_id", "")).strip()
        }
        reconciliation_by_receipt = {
            str(row.get("receipt_id", "")).strip(): row
            for row in reconciliation
            if str(row.get("receipt_id", "")).strip()
        }

        memory_rows = []
        for txn in transactions:
            record = {
                "row_type": "transaction",
                "source_file": path.name,
                "source_path": str(path),
            }
            record.update(employee_meta)
            record.update(txn)

            receipt_id = txn_to_receipt.get(str(txn.get("transaction_id", "")).strip(), "")
            receipt = receipt_lookup.get(receipt_id)
            if receipt:
                record.update(self._receipt_to_context(receipt))
            else:
                record.update(self._best_receipt_fallback(txn, receipts))

            reconciled = reconciliation_by_receipt.get(receipt_id)
            if reconciled:
                record["match_status"] = reconciled.get("match_status", "")
                record["match_confidence"] = reconciled.get("confidence", "")

            memory_rows.append(record)

        return {
            "transaction_rows": transactions,
            "receipt_rows": receipts,
            "reconciliation_rows": reconciliation,
            "memory_rows": memory_rows,
        }

    def _parse_employee_report(self, path: Path) -> Dict:
        df = self._read_sheet(path, "Employee Report")
        if df.empty:
            return {}

        meta = {}
        for _, row in df.iterrows():
            key = self._normalize_text(row.iloc[1] if len(row) > 1 else "")
            value = row.iloc[2] if len(row) > 2 else ""
            if key and not self._is_empty(value):
                meta.update(self._map_employee_report_field(key, value))
        employee_name = meta.get("employee_name", "")
        if employee_name:
            first_name, last_name = self._split_name(employee_name)
            meta.setdefault("employee_first_name", first_name)
            meta.setdefault("employee_last_name", last_name)
        return meta

    def _map_employee_report_field(self, key: str, value) -> Dict:
        normalized = key.lower().strip()
        text_value = self._normalize_text(value)
        float_value = self._to_float(value)
        mapping = {
            "employee name": {"employee_name": text_value},
            "employee id": {"employee_id": text_value},
            "report id": {"report_id": text_value},
            "report date": {"report_date": text_value},
            "approval status": {"approval_status": text_value},
            "payment status": {"payment_status": text_value},
            "currency": {"currency": text_value},
            "report total": {"report_total": float_value},
            "total amount claimed": {"total_amount_claimed": float_value},
            "amount approved": {"amount_approved": float_value},
            "personal expenses": {"personal_expenses": float_value},
            "amount due employee": {"amount_due_employee": float_value},
            "amount due company card": {"amount_due_company_card": float_value},
            "total paid by company": {"total_paid_by_company": float_value},
            "amount due from employee": {"amount_due_from_employee": float_value},
        }
        return mapping.get(normalized, {})

    def _parse_transactions_sheet(self, path: Path) -> List[Dict]:
        df = self._read_sheet(path, "Transactions")
        header_row = self._find_header_row(df, "transaction id")
        if header_row is None:
            return []

        headers = self._row_headers(df.iloc[header_row])
        rows = []
        for idx in range(header_row + 1, len(df)):
            values = df.iloc[idx].tolist()
            if self._is_blank_row(values):
                continue
            raw = self._row_as_dict(headers, values)
            transaction_id = self._normalize_text(raw.get("Transaction Id"))
            if not transaction_id:
                continue
            rows.append(
                {
                    "transaction_id": transaction_id,
                    "transaction_date": self._normalize_text(raw.get("Transaction Date")),
                    "expense_type": self._normalize_text(raw.get("Expense Type")),
                    "business_purpose": self._normalize_text(raw.get("Business Purpose")),
                    "vendor_desc": self._normalize_text(raw.get("Vendor Description")),
                    "payment_type": self._normalize_text(raw.get("Payment Type")),
                    "amount": self._to_float(raw.get("Amount")),
                    "cost_center": self._normalize_text(raw.get("Cost Center")),
                    "project": self._normalize_text(raw.get("Project")),
                    "attendees": self._normalize_text(raw.get("Attendees")),
                    "comments": self._normalize_text(raw.get("Comments")),
                }
            )
        return rows

    def _parse_receipts_sheet(self, path: Path) -> List[Dict]:
        df = self._read_sheet(path, "Receipts")
        header_row = self._find_header_row(df, "receipt id")
        if header_row is None:
            return []

        headers = self._row_headers(df.iloc[header_row])
        rows = []
        for idx in range(header_row + 1, len(df)):
            values = df.iloc[idx].tolist()
            if self._is_blank_row(values):
                continue
            raw = self._row_as_dict(headers, values)
            receipt_id = self._normalize_text(raw.get("Receipt Id"))
            if not receipt_id:
                continue
            summary = self._normalize_text(raw.get("Summary"))
            rows.append(
                {
                    "receipt_id": receipt_id,
                    "order_id": self._normalize_text(raw.get("Order Id")),
                    "date": self._normalize_text(raw.get("Date")),
                    "vendor": self._normalize_text(raw.get("Vendor")),
                    "amount": self._to_float(raw.get("Amount")),
                    "summary": summary,
                    "ticket_number": self._extract_ticket(summary),
                    "passenger": self._extract_passenger(summary),
                    "route": self._extract_route(summary),
                }
            )
        return rows

    def _parse_reconciliation_sheet(self, path: Path) -> List[Dict]:
        df = self._read_sheet(path, "Reconciliation")
        header_row = self._find_header_row(df, "transaction id")
        if header_row is None:
            return []

        headers = self._row_headers(df.iloc[header_row])
        rows = []
        for idx in range(header_row + 1, len(df)):
            values = df.iloc[idx].tolist()
            if self._is_blank_row(values):
                continue
            raw = self._row_as_dict(headers, values)
            transaction_id = self._normalize_text(raw.get("Transaction Id"))
            receipt_id = self._normalize_text(raw.get("Receipt Id"))
            if not transaction_id and not receipt_id:
                continue
            rows.append(
                {
                    "transaction_id": transaction_id,
                    "receipt_id": receipt_id,
                    "match_status": self._normalize_text(raw.get("Match Status")),
                    "confidence": self._normalize_text(raw.get("Confidence")),
                }
            )
        return rows

    def _candidate_frame(self, query: Dict) -> pd.DataFrame:
        df = self.memory_df
        if df.empty:
            return df

        candidate = df.copy()
        employee_id = self._normalize_text(query.get("employee_id")).upper()
        if employee_id and "employee_id" in candidate.columns:
            subset = candidate[candidate["employee_id"].fillna("").astype(str).str.upper() == employee_id]
            if not subset.empty:
                candidate = subset
        else:
            employee_name = self._normalize_text(query.get("employee_name")).lower()
            last_name = self._normalize_text(query.get("employee_last_name")).lower()
            first_name = self._normalize_text(query.get("employee_first_name")).lower()
            if employee_name and "employee_name" in candidate.columns:
                mask = candidate["employee_name"].fillna("").astype(str).str.lower().str.contains(employee_name, na=False)
                subset = candidate[mask]
                if not subset.empty:
                    candidate = subset
            if last_name and "employee_last_name" in candidate.columns:
                subset = candidate[candidate["employee_last_name"].fillna("").astype(str).str.lower().str.contains(last_name, na=False)]
                if not subset.empty:
                    candidate = subset
            if first_name and "employee_first_name" in candidate.columns:
                subset = candidate[candidate["employee_first_name"].fillna("").astype(str).str.lower().str.contains(first_name, na=False)]
                if not subset.empty:
                    candidate = subset
        return candidate

    def _normalize_transaction(self, txn: Dict) -> Dict:
        employee_name = self._normalize_text(
            txn.get("employee_name")
            or f"{txn.get('employee_first_name', '')} {txn.get('employee_last_name', '')}"
        )
        first_name, last_name = self._split_name(employee_name)
        return {
            "employee_id": self._normalize_text(txn.get("employee_id")),
            "employee_name": employee_name,
            "employee_first_name": self._normalize_text(txn.get("employee_first_name") or first_name),
            "employee_last_name": self._normalize_text(txn.get("employee_last_name") or last_name),
            "transaction_date": self._normalize_text(txn.get("transaction_date") or txn.get("date")),
            "description": self._normalize_text(txn.get("description") or txn.get("business_purpose")),
            "vendor": self._normalize_text(txn.get("vendor") or txn.get("vendor_desc")),
            "vendor_desc": self._normalize_text(txn.get("vendor_desc") or txn.get("vendor")),
            "expense_code": self._normalize_text(txn.get("expense_code") or txn.get("expense_type")),
            "expense_type": self._normalize_text(txn.get("expense_type") or txn.get("expense_code")),
            "project": self._normalize_text(txn.get("project")),
            "cost_center": self._normalize_text(txn.get("cost_center")),
            "amount": self._to_float(txn.get("amount")),
            "transaction_id": self._normalize_text(txn.get("transaction_id")),
            "receipt_id": self._normalize_text(txn.get("receipt_id")),
        }

    def _score_match(self, query: Dict, candidate: Dict) -> float:
        score = 0.0

        if query.get("employee_id") and candidate.get("employee_id"):
            if self._normalize_text(query["employee_id"]).upper() == self._normalize_text(candidate["employee_id"]).upper():
                score += 5.0

        if query.get("employee_last_name") and candidate.get("employee_last_name"):
            if self._normalize_text(query["employee_last_name"]).lower() == self._normalize_text(candidate["employee_last_name"]).lower():
                score += 2.0

        if query.get("employee_first_name") and candidate.get("employee_first_name"):
            if self._normalize_text(query["employee_first_name"]).lower() == self._normalize_text(candidate["employee_first_name"]).lower():
                score += 1.0

        q_amount = self._to_float(query.get("amount"))
        c_amount = self._to_float(candidate.get("amount"))
        if q_amount and c_amount:
            diff = abs(q_amount - c_amount)
            if diff < 0.01:
                score += 5.0
            elif diff < 1.0:
                score += 3.0
            elif diff < 5.0:
                score += 1.5
            elif diff < 20.0:
                score += 0.5

        score += 3.0 * self._text_similarity(query.get("vendor_desc") or query.get("vendor"), candidate.get("vendor_desc") or candidate.get("vendor"))
        score += 3.0 * self._text_similarity(
            query.get("description"),
            candidate.get("description") or candidate.get("business_purpose") or candidate.get("summary"),
        )

        if self._normalize_text(query.get("expense_code")).lower() == self._normalize_text(candidate.get("expense_code")).lower():
            if self._normalize_text(query.get("expense_code")):
                score += 1.5

        if self._normalize_text(query.get("project")).lower() == self._normalize_text(candidate.get("project")).lower():
            if self._normalize_text(query.get("project")):
                score += 0.5

        if self._normalize_text(query.get("cost_center")).lower() == self._normalize_text(candidate.get("cost_center")).lower():
            if self._normalize_text(query.get("cost_center")):
                score += 0.5

        if self._normalize_text(query.get("receipt_id")) and self._normalize_text(query.get("receipt_id")) == self._normalize_text(candidate.get("receipt_id")):
            score += 4.0

        return score

    def _text_similarity(self, left, right) -> float:
        left_tokens = self._tokenize(left)
        right_tokens = self._tokenize(right)
        if not left_tokens or not right_tokens:
            return 0.0
        union = left_tokens | right_tokens
        return len(left_tokens & right_tokens) / len(union) if union else 0.0

    def _best_receipt_fallback(self, txn: Dict, receipts: List[Dict]) -> Dict:
        if not receipts:
            return self._receipt_to_context({})

        txn_amount = self._to_float(txn.get("amount"))
        txn_vendor = self._normalize_text(txn.get("vendor_desc") or txn.get("vendor")).lower()
        best = {}
        best_score = -1.0
        for receipt in receipts:
            receipt_amount = self._to_float(receipt.get("amount"))
            if txn_amount and receipt_amount and abs(abs(txn_amount) - abs(receipt_amount)) > 5.0:
                continue
            if txn_amount and not receipt_amount:
                continue

            score = 0.0
            if txn_amount and receipt_amount and abs(abs(txn_amount) - abs(receipt_amount)) < 0.01:
                score += 2.0

            receipt_vendor = self._normalize_text(receipt.get("vendor")).lower()
            if txn_vendor and receipt_vendor:
                if txn_vendor == receipt_vendor:
                    score += 2.0
                elif txn_vendor in receipt_vendor or receipt_vendor in txn_vendor:
                    score += 1.0
                else:
                    score += self._text_similarity(txn_vendor, receipt_vendor)

            if score > best_score:
                best_score = score
                best = receipt
        return self._receipt_to_context(best)

    def _receipt_to_context(self, receipt: Dict) -> Dict:
        return {
            "receipt_id": self._normalize_text(receipt.get("receipt_id")),
            "order_id": self._normalize_text(receipt.get("order_id")),
            "receipt_date": self._normalize_text(receipt.get("date")),
            "receipt_vendor": self._normalize_text(receipt.get("vendor")),
            "receipt_amount": self._to_float(receipt.get("amount")),
            "receipt_summary": self._normalize_text(receipt.get("summary")),
            "receipt_ticket_number": self._normalize_text(receipt.get("ticket_number")),
            "receipt_passenger": self._normalize_text(receipt.get("passenger")),
            "receipt_route": self._normalize_text(receipt.get("route")),
        }

    def _read_sheet(self, path: Path, sheet_name: str) -> pd.DataFrame:
        try:
            return pd.read_excel(path, sheet_name=sheet_name, header=None)
        except ValueError:
            return pd.DataFrame()

    def _find_header_row(self, df: pd.DataFrame, required_text: str) -> Optional[int]:
        if df.empty:
            return None
        required = self._normalize_text(required_text).lower()
        for idx, row in df.iterrows():
            row_text = " ".join(self._normalize_text(value).lower() for value in row.tolist())
            if required in row_text:
                return idx
        return None

    def _row_headers(self, row: pd.Series) -> List[str]:
        return [self._normalize_text(value) for value in row.tolist()]

    def _row_as_dict(self, headers: List[str], values: List) -> Dict:
        row = {}
        for idx, header in enumerate(headers):
            if header and idx < len(values):
                row[header] = values[idx]
        return row

    def _records_from_frame(self, frame: pd.DataFrame) -> List[Dict]:
        if frame is None or frame.empty:
            return []
        return [self._clean_record(row) for row in frame.to_dict("records")]

    def _clean_record(self, record: Dict) -> Dict:
        cleaned = {}
        for key, value in record.items():
            if self._is_empty(value):
                cleaned[key] = ""
            elif isinstance(value, pd.Timestamp):
                cleaned[key] = value.strftime("%Y-%m-%d")
            else:
                cleaned[key] = value.item() if hasattr(value, "item") else value
        return cleaned

    def _normalize_text(self, value) -> str:
        if self._is_empty(value):
            return ""
        text = str(value).strip()
        return "" if text.lower() in {"none", "nan"} else text

    def _split_name(self, full_name: str) -> tuple:
        parts = [part for part in re.split(r"[,\s]+", self._normalize_text(full_name)) if part]
        if not parts:
            return "", ""
        if len(parts) == 1:
            return parts[0], ""
        return parts[0], parts[-1]

    def _tokenize(self, value) -> set:
        text = self._normalize_text(value).lower()
        if not text:
            return set()
        stop_words = {"the", "and", "for", "with", "from", "to", "of", "a", "an", "by", "on", "in"}
        return {token for token in re.findall(r"[a-z0-9]+", text) if token not in stop_words}

    def _field_name(self, text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")

    def _is_blank_row(self, values: List) -> bool:
        return all(self._is_empty(value) for value in values)

    def _is_empty(self, value) -> bool:
        if value is None:
            return True
        try:
            return bool(pd.isna(value))
        except Exception:
            return False

    def _to_float(self, value) -> float:
        if self._is_empty(value):
            return 0.0
        try:
            return float(str(value).replace(",", "").strip())
        except (TypeError, ValueError):
            return 0.0

    def _is_self_match(self, query: Dict, candidate: Dict) -> bool:
        """Skip exact batch rows so the current transaction does not match itself."""
        if candidate.get("source_file") != "current_batch":
            return False
        keys = ("employee_id", "amount", "description", "vendor", "transaction_date")
        return all(
            self._normalize_text(query.get(key)).lower() == self._normalize_text(candidate.get(key)).lower()
            for key in keys
        )

    @staticmethod
    def _extract_ticket(summary: str) -> str:
        match = re.search(r"Ticket\s+Number[:\s]+([A-Z0-9\-]+)", summary, re.I)
        return match.group(1) if match else ""

    @staticmethod
    def _extract_passenger(summary: str) -> str:
        match = re.search(r"Passenger\s+Name[:\s]+([^;]+)", summary, re.I)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _extract_route(summary: str) -> str:
        match = re.search(r"([A-Z]{3})\s*(?:-|to)\s*([A-Z]{3})", summary, re.I)
        return f"{match.group(1).upper()}-{match.group(2).upper()}" if match else ""

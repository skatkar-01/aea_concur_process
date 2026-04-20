"""
scrubber.py - Main Scrubber Orchestrator
Coordinates rules engine, LLM formatter, and validation
"""

import pandas as pd
from pathlib import Path
from typing import Dict, List, Tuple
from tqdm import tqdm
import time
import logging

logger = logging.getLogger(__name__)

from rules_engine import RulesEngine
from llm_formatter import LLMFormatter
from transaction_memory import TransactionMemory
from cache import ResultCache
from checkpoint import CheckpointManager


class AmExScrubber:
    """
    Main scrubber that orchestrates all components
    """
    
    def __init__(
        self,
        config_dir: Path ,
        memory_folder: Path = None,
        use_cache: bool = True,
        use_checkpoints: bool = True,
        checkpoint_interval: int = 50,
        llm_batch_size: int = 5
    ):
        """
        Initialize scrubber
        
        Args:
            config_dir: Directory containing YAML configuration files
            memory_folder: Folder with historical transaction Excel files
            use_cache: Whether to cache LLM results
            use_checkpoints: Whether to save checkpoints
            checkpoint_interval: Save checkpoint every N transactions
            llm_batch_size: Number of transactions to send to LLM per batch
        """
        print("\n" + "="*70)
        print("AmEx Expense Scrubber - Enhanced with LLM")
        print("="*70 + "\n")
        
        # Initialize components
        print("Initializing components...")
        
        self.rules_engine = RulesEngine(config_dir)
        self.llm_formatter = LLMFormatter()
        if memory_folder is None:
            raise ValueError(
                "memory_folder is required. Pass --memory-folder with a folder of historical Concur workbooks."
            )
        self.memory = TransactionMemory(memory_folder)
        
        self.use_cache = use_cache
        self.cache = ResultCache() if use_cache else None
        
        self.use_checkpoints = use_checkpoints
        self.checkpoint_mgr = CheckpointManager() if use_checkpoints else None
        self.checkpoint_interval = checkpoint_interval
        self.llm_batch_size = max(1, int(llm_batch_size))
        
        # Statistics
        self.stats = {
            'total_transactions': 0,
            'auto_approved': 0,
            'needs_review': 0,
            'flagged': 0,
            'description_changes': 0,
            'expense_code_changes': 0,
            'vendor_changes': 0,
            'processing_time': 0,
            'llm_calls': 0,
            'cache_hits': 0
        }
        
        print("\n✓ All components initialized\n")
    
    def _enrich_transaction_with_receipt_data(self, txn: Dict) -> Dict:
        """
        Enrich transaction with receipt data from memory.
        Uses composite key (employee name + date + amount) to find matching transaction in memory,
        then extracts and attaches receipt details.
        
        Args:
            txn: Transaction dictionary with transaction_date, amount, employee_first_name, employee_last_name
            
        Returns:
            Transaction dict with receipt fields added (or empty placeholders)
        """
        enriched_txn = txn.copy()
        
        # Initialize receipt fields with empty defaults
        receipt_fields = {
            'receipt_id': '',
            'order_id': '',
            'receipt_date': '',
            'receipt_vendor': '',
            'receipt_amount': 0.0,
            'receipt_summary': '',
            'receipt_ticket_number': '',
            'receipt_passenger': '',
            'receipt_route': '',
        }
        enriched_txn.update(receipt_fields)
        
        # If memory available, try to find receipt data using composite key
        if not self.memory:
            return enriched_txn
        
        try:
            # Extract components for composite key lookup
            employee_first_name = txn.get('employee_first_name') or ''
            employee_last_name = txn.get('employee_last_name') or ''
            transaction_date = txn.get('transaction_date') or ''
            amount = txn.get('amount')
            
            # Look up receipt data by composite key
            if employee_last_name and transaction_date and amount is not None:
                receipt_data = self.memory.find_receipt_data_by_composite_key(
                    employee_first_name=employee_first_name,
                    employee_last_name=employee_last_name,
                    transaction_date=transaction_date,
                    amount=amount
                )
                
                if receipt_data:
                    # Merge receipt data into transaction
                    enriched_txn.update(receipt_data)
        except Exception as e:
            # If lookup fails, just use empty defaults
            logger.debug(f"Receipt enrichment failed for {txn.get('description', 'unknown')}: {str(e)}")
        
        return enriched_txn
    
    def _build_base_result(self, txn: Dict) -> Dict:
        return {
            'original': txn.copy(),
            'scrubbed': txn.copy(),
            'changes': {},
            'flags': [],
            'confidence': 1.0,
            'reasoning': '',
            'needs_review': False
        }

    def _prepare_transaction(self, txn: Dict, vendor_list: Dict = None) -> tuple[Dict, List[Dict], bool]:
        """Apply deterministic rules and memory lookup before LLM formatting."""
        # Enrich transaction with receipt data from memory
        txn_enriched = self._enrich_transaction_with_receipt_data(txn)
        
        result = self._build_base_result(txn_enriched)

        pay_type, pay_changed = self.rules_engine.scrub_pay_type(txn_enriched.get('pay_type', ''))
        if pay_changed:
            result['scrubbed']['pay_type'] = pay_type
            result['changes']['pay_type'] = (txn_enriched.get('pay_type'), pay_type)

        desc, desc_changed = self.rules_engine.scrub_description(
            txn_enriched.get('description', ''),
            txn_enriched.get('expense_code', '')
        )
        if desc_changed:
            result['scrubbed']['description'] = desc
            result['changes']['description'] = (txn_enriched.get('description'), desc)
            self.stats['description_changes'] += 1

        exp_code, exp_changed = self.rules_engine.scrub_expense_code(
            txn_enriched.get('expense_code', ''),
            result['scrubbed']['description']
        )
        if exp_changed:
            result['scrubbed']['expense_code'] = exp_code
            result['changes']['expense_code'] = (txn_enriched.get('expense_code'), exp_code)
            self.stats['expense_code_changes'] += 1

        should_use_llm = self._should_use_llm(result['scrubbed'])
        similar_txns = self.memory.find_similar(result['scrubbed'], top_k=3) if should_use_llm else []
        result['memory_match'] = self._select_memory_match(similar_txns)
        result['llm_result'] = {}
        return result, similar_txns, should_use_llm

    def _apply_llm_result(self, result: Dict, txn: Dict, llm_result: Dict) -> None:
        """Merge a single LLM result into a partially processed transaction."""
        # Ensure llm_result has all required fields with defaults
        full_llm_result = {
            'transaction_type': llm_result.get('transaction_type', ''),
            'formatted_description': llm_result.get('formatted_description', ''),
            'description_changed': llm_result.get('description_changed', False),
            'expense_code': llm_result.get('expense_code', ''),
            'expense_code_changed': llm_result.get('expense_code_changed', False),
            'confidence': llm_result.get('confidence', 0.5),
            'reasoning': llm_result.get('reasoning', ''),
            'flags': llm_result.get('flags', []),
            'is_refund': llm_result.get('is_refund', False),
            'error': llm_result.get('error', ''),
        }
        result['llm_result'] = full_llm_result

        if full_llm_result.get('confidence', 0) >= 0.50:
            if full_llm_result.get('description_changed'):
                llm_desc = full_llm_result.get('formatted_description', '')
                if llm_desc and llm_desc != result['scrubbed']['description']:
                    result['scrubbed']['description'] = llm_desc
                    if 'description' not in result['changes']:
                        result['changes']['description'] = (txn.get('description'), llm_desc)

            if full_llm_result.get('expense_code_changed'):
                llm_exp = full_llm_result.get('expense_code', '')
                if llm_exp and llm_exp != result['scrubbed']['expense_code']:
                    result['scrubbed']['expense_code'] = llm_exp
                    if 'expense_code' not in result['changes']:
                        result['changes']['expense_code'] = (txn.get('expense_code'), llm_exp)

        result['confidence'] = full_llm_result.get('confidence', 0.5)
        result['reasoning'] = full_llm_result.get('reasoning', '')
        result['flags'].extend(full_llm_result.get('flags', []))

    def _finalize_result(self, result: Dict) -> Dict:
        """Run validation, build note text, and update summary stats."""
        validation_flags = self.rules_engine.validate_transaction(result['scrubbed'])
        result['flags'].extend(validation_flags)

        result['needs_review'] = (
            result['confidence'] < 0.85 or
            len(result['flags']) > 0
        )

        note_parts = []
        if result['changes'] or result['flags']:
            if result['changes']:
                change_bits = []
                if 'description' in result['changes']:
                    before, after = result['changes']['description']
                    change_bits.append(f"Description: {before} -> {after}")
                if 'expense_code' in result['changes']:
                    before, after = result['changes']['expense_code']
                    change_bits.append(f"Expense code: {before} -> {after}")
                if 'pay_type' in result['changes']:
                    before, after = result['changes']['pay_type']
                    change_bits.append(f"Payment type: {before} -> {after}")
                note_parts.append("; ".join(change_bits))
            if result['reasoning']:
                note_parts.append(result['reasoning'])
            if result['flags']:
                note_parts.append("Flags: " + " | ".join(result['flags']))
            note_parts.append(f"Confidence: {result['confidence']:.2f}")
            result['note'] = " ".join(part for part in note_parts if part)
        else:
            result['note'] = ''

        if result['confidence'] >= 0.95 and len(result['flags']) == 0:
            self.stats['auto_approved'] += 1
        elif result['confidence'] >= 0.80:
            self.stats['needs_review'] += 1
        else:
            self.stats['flagged'] += 1

        return result

    def process_transaction(
        self,
        txn: Dict,
        vendor_list: Dict = None
    ) -> Dict:
        """
        Process a single transaction through the pipeline
        
        Pipeline:
        1. Rules engine (deterministic)
        2. LLM formatter (if needed)
        3. Validation
        
        Args:
            txn: Transaction dictionary
            vendor_list: Optional vendor lookup dictionary
            
        Returns:
            Processed transaction with scrubbing results
        """
        result, similar_txns, should_use_llm = self._prepare_transaction(txn, vendor_list)

        if should_use_llm:
            llm_result = None
            if self.cache:
                llm_result = self.cache.get(result['scrubbed'])
                if llm_result:
                    self.stats['cache_hits'] += 1

            if not llm_result:
                llm_result = self.llm_formatter.format_description(
                    result['scrubbed'],
                    similar_txns
                )
                self.stats['llm_calls'] += 1
                if self.cache:
                    self.cache.set(result['scrubbed'], llm_result)

            self._apply_llm_result(result, txn, llm_result)
        else:
            # When LLM not used, still initialize llm_result with defaults for consistency
            result['llm_result'] = {
                'transaction_type': '',
                'formatted_description': result['scrubbed'].get('description', ''),
                'description_changed': False,
                'expense_code': result['scrubbed'].get('expense_code', ''),
                'expense_code_changed': False,
                'confidence': 1.0,  # High confidence since rules-based
                'reasoning': 'Direct rules matching - no LLM needed',
                'flags': [],
                'is_refund': float(result['scrubbed'].get('amount', 0)) < 0,
                'error': '',
            }

        return self._finalize_result(result)

    def _select_memory_match(self, similar_txns: List[Dict]) -> Dict:
        """Pick the best historical match for debug output."""
        if not similar_txns:
            return {}

        historical = [
            row for row in similar_txns
            if str(row.get('source_file', '')).strip().lower() != 'current_batch'
        ]
        chosen = historical[0] if historical else similar_txns[0]

        return {
            'source_file': chosen.get('source_file', ''),
            'transaction_id': chosen.get('transaction_id', ''),
            'receipt_id': chosen.get('receipt_id', ''),
            'match_score': chosen.get('_match_score', ''),
        }
    
    def _should_use_llm(self, txn: Dict) -> bool:
        """
        Decide if we should use LLM for this transaction
        Skip LLM for simple, straightforward transactions
        """
        # Always use LLM for now (can optimize later)
        # In production, could skip for simple vendors like Starbucks, Uber
        return True
    
    def process_batch(
        self,
        transactions: List[Dict],
        batch_id: str,
        vendor_list: Dict = None,
        resume: bool = True
    ) -> List[Dict]:
        """
        Process batch of transactions with checkpointing
        
        Args:
            transactions: List of transaction dictionaries
            batch_id: Unique identifier for this batch
            vendor_list: Optional vendor lookup dictionary
            resume: Whether to resume from checkpoint if available
            
        Returns:
            List of processed results
        """
        start_time = time.time()
        
        # Check for existing checkpoint
        checkpoint = None
        if resume and self.checkpoint_mgr:
            checkpoint = self.checkpoint_mgr.load_checkpoint(batch_id)
        
        if checkpoint:
            print(f"\nResuming from checkpoint ({checkpoint['progress']:.1%} complete)")
            results = checkpoint['processed_results']
            remaining = checkpoint['remaining_transactions']
        else:
            results = []
            remaining = transactions
        
        # Add current batch to memory
        self.memory.add_batch(transactions)
        
        # Process transactions
        print(f"\nProcessing {len(remaining)} transactions...")
        
        processed_count = 0
        next_checkpoint_at = self.checkpoint_interval
        batch_size = self.llm_batch_size

        with tqdm(total=len(remaining), desc="Processing") as pbar:
            for chunk_start in range(0, len(remaining), batch_size):
                chunk = remaining[chunk_start:chunk_start + batch_size]
                chunk_results: List[tuple[int, Dict]] = []
                pending_batch: List[Dict] = []

                for local_index, txn in enumerate(chunk):
                    result, similar_txns, should_use_llm = self._prepare_transaction(txn, vendor_list)

                    if should_use_llm:
                        llm_result = None
                        if self.cache:
                            llm_result = self.cache.get(result['scrubbed'])
                            if llm_result:
                                self.stats['cache_hits'] += 1

                        if llm_result:
                            self._apply_llm_result(result, txn, llm_result)
                            self._finalize_result(result)
                            chunk_results.append((local_index, result))
                            pbar.update(1)
                            pbar.set_postfix({
                                'Confidence': f"{result['confidence']:.2f}",
                                'Flags': len(result['flags'])
                            })
                        else:
                            pending_batch.append({
                                'index': local_index + 1,
                                'txn': txn,
                                'result': result,
                                'similar_txns': similar_txns
                            })
                    else:
                        # When LLM not used, still initialize llm_result with defaults for consistency
                        result['llm_result'] = {
                            'transaction_type': '',
                            'formatted_description': result['scrubbed'].get('description', ''),
                            'description_changed': False,
                            'expense_code': result['scrubbed'].get('expense_code', ''),
                            'expense_code_changed': False,
                            'confidence': 1.0,  # High confidence since rules-based
                            'reasoning': 'Direct rules matching - no LLM needed',
                            'flags': [],
                            'is_refund': float(result['scrubbed'].get('amount', 0)) < 0,
                            'error': '',
                        }
                        self._finalize_result(result)
                        chunk_results.append((local_index, result))
                        pbar.update(1)
                        pbar.set_postfix({
                            'Confidence': f"{result['confidence']:.2f}",
                            'Flags': len(result['flags'])
                        })

                if pending_batch:
                    llm_results = self.llm_formatter.format_description_batch(pending_batch)
                    self.stats['llm_calls'] += 1
                    for item, llm_result in zip(pending_batch, llm_results):
                        result = item['result']
                        if self.cache:
                            self.cache.set(result['scrubbed'], llm_result)
                        # Ensure llm_result is always populated (even if empty)
                        self._apply_llm_result(result, item['txn'], llm_result)
                        self._finalize_result(result)
                        chunk_results.append((item['index'] - 1, result))
                        pbar.update(1)
                        pbar.set_postfix({
                            'Confidence': f"{result['confidence']:.2f}",
                            'Flags': len(result['flags'])
                        })

                chunk_results.sort(key=lambda item: item[0])
                results.extend(result for _, result in chunk_results)

                processed_count += len(chunk)
                if self.checkpoint_mgr and processed_count >= next_checkpoint_at:
                    self.checkpoint_mgr.save_checkpoint(
                        batch_id,
                        results,
                        remaining[processed_count:],
                        {'stats': self.stats}
                    )
                    next_checkpoint_at += self.checkpoint_interval
        
        # Processing complete - delete checkpoint
        if self.checkpoint_mgr:
            self.checkpoint_mgr.delete_checkpoint(batch_id)
        
        # Update stats
        self.stats['total_transactions'] = len(results)
        self.stats['processing_time'] = time.time() - start_time
        
        return results
    
    def print_stats(self):
        """Print processing statistics"""
        print("\n" + "="*70)
        print("PROCESSING STATISTICS")
        print("="*70)
        
        total = self.stats['total_transactions']
        if total == 0:
            print("No transactions processed")
            return
        
        print(f"\nTotal Transactions: {total}")
        print(f"Processing Time: {self.stats['processing_time']:.1f}s")
        print(f"Average Time: {self.stats['processing_time']/total:.2f}s per transaction")
        
        print(f"\n📈 Automation:")
        print(f"  Auto-Approved (≥95% confidence): {self.stats['auto_approved']} ({self.stats['auto_approved']/total*100:.1f}%)")
        print(f"  Needs Review (80-95%): {self.stats['needs_review']} ({self.stats['needs_review']/total*100:.1f}%)")
        print(f"  Flagged (<80%): {self.stats['flagged']} ({self.stats['flagged']/total*100:.1f}%)")
        
        print(f"\n✏️ Changes Made:")
        print(f"  Descriptions: {self.stats['description_changes']}")
        print(f"  Expense Codes: {self.stats['expense_code_changes']}")
        print(f"  Vendors: {self.stats['vendor_changes']}")
        
        print(f"\nLLM Usage:")
        print(f"  API Calls: {self.stats['llm_calls']}")
        print(f"  Cache Hits: {self.stats['cache_hits']}")
        
        if self.cache:
            self.cache.print_stats()
        
        print("\n" + "="*70 + "\n")

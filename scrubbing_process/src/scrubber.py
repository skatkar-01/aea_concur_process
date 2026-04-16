"""
scrubber.py - Main Scrubber Orchestrator
Coordinates rules engine, LLM formatter, and validation
"""

import pandas as pd
from pathlib import Path
from typing import Dict, List, Tuple
from tqdm import tqdm
import time

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
        checkpoint_interval: int = 50
    ):
        """
        Initialize scrubber
        
        Args:
            config_dir: Directory containing YAML configuration files
            memory_folder: Folder with historical transaction Excel files
            use_cache: Whether to cache LLM results
            use_checkpoints: Whether to save checkpoints
            checkpoint_interval: Save checkpoint every N transactions
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
        result = {
            'original': txn.copy(),
            'scrubbed': txn.copy(),
            'changes': {},
            'flags': [],
            'confidence': 1.0,
            'reasoning': '',
            'needs_review': False
        }
        
        # Phase 1: Apply deterministic rules
        
        # Pay type
        pay_type, pay_changed = self.rules_engine.scrub_pay_type(txn.get('pay_type', ''))
        if pay_changed:
            result['scrubbed']['pay_type'] = pay_type
            result['changes']['pay_type'] = (txn.get('pay_type'), pay_type)
        
        # Description
        desc, desc_changed = self.rules_engine.scrub_description(
            txn.get('description', ''),
            txn.get('expense_code', '')
        )
        if desc_changed:
            result['scrubbed']['description'] = desc
            result['changes']['description'] = (txn.get('description'), desc)
            self.stats['description_changes'] += 1
        
        # Expense code
        exp_code, exp_changed = self.rules_engine.scrub_expense_code(
            txn.get('expense_code', ''),
            result['scrubbed']['description']
        )
        if exp_changed:
            result['scrubbed']['expense_code'] = exp_code
            result['changes']['expense_code'] = (txn.get('expense_code'), exp_code)
            self.stats['expense_code_changes'] += 1
        
        # Vendor is intentionally preserved as-is.
        # We do not normalize or rewrite vendor names in this flow.
        
        # Phase 2: LLM formatting (if description seems complex or low confidence)
        should_use_llm = self._should_use_llm(result['scrubbed'])
        
        if should_use_llm:
            # Check cache first
            llm_result = None
            if self.cache:
                llm_result = self.cache.get(result['scrubbed'])
                if llm_result:
                    self.stats['cache_hits'] += 1
            
            # Call LLM if not cached
            if not llm_result:
                similar_txns = self.memory.find_similar(result['scrubbed'], top_k=3)
                llm_result = self.llm_formatter.format_description(
                    result['scrubbed'],
                    similar_txns
                )
                self.stats['llm_calls'] += 1
                
                # Cache result
                if self.cache:
                    self.cache.set(result['scrubbed'], llm_result)
            
            # Apply LLM results if high confidence
            if llm_result.get('confidence', 0) >= 0.00:
                # Update description if LLM suggests change
                if llm_result.get('description_changed'):
                    llm_desc = llm_result.get('formatted_description', '')
                    if llm_desc and llm_desc != result['scrubbed']['description']:
                        result['scrubbed']['description'] = llm_desc
                        if 'description' not in result['changes']:
                            result['changes']['description'] = (txn.get('description'), llm_desc)
                
                # Update expense code if LLM suggests change
                if llm_result.get('expense_code_changed'):
                    llm_exp = llm_result.get('expense_code', '')
                    if llm_exp and llm_exp != result['scrubbed']['expense_code']:
                        result['scrubbed']['expense_code'] = llm_exp
                        if 'expense_code' not in result['changes']:
                            result['changes']['expense_code'] = (txn.get('expense_code'), llm_exp)
            
            # Record LLM metadata
            result['confidence'] = llm_result.get('confidence', 0.5)
            result['reasoning'] = llm_result.get('reasoning', '')
            result['flags'].extend(llm_result.get('flags', []))
        
        # Phase 3: Validation
        validation_flags = self.rules_engine.validate_transaction(result['scrubbed'])
        result['flags'].extend(validation_flags)
        
        # Determine if needs review
        result['needs_review'] = (
            result['confidence'] < 0.85 or
            len(result['flags']) > 0
        )
        
        # Update statistics
        if result['confidence'] >= 0.95 and len(result['flags']) == 0:
            self.stats['auto_approved'] += 1
        elif result['confidence'] >= 0.80:
            self.stats['needs_review'] += 1
        else:
            self.stats['flagged'] += 1
        
        return result
    
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
            print(f"\n📌 Resuming from checkpoint ({checkpoint['progress']:.1%} complete)")
            results = checkpoint['processed_results']
            remaining = checkpoint['remaining_transactions']
        else:
            results = []
            remaining = transactions
        
        # Add current batch to memory
        self.memory.add_batch(transactions)
        
        # Process transactions
        print(f"\n🔄 Processing {len(remaining)} transactions...")
        
        with tqdm(total=len(remaining), desc="Processing") as pbar:
            for i, txn in enumerate(remaining):
                # Process transaction
                result = self.process_transaction(txn, vendor_list)
                results.append(result)
                
                pbar.update(1)
                pbar.set_postfix({
                    'Confidence': f"{result['confidence']:.2f}",
                    'Flags': len(result['flags'])
                })
                
                # Save checkpoint
                if self.checkpoint_mgr and (i + 1) % self.checkpoint_interval == 0:
                    self.checkpoint_mgr.save_checkpoint(
                        batch_id,
                        results,
                        remaining[i+1:],
                        {'stats': self.stats}
                    )
        
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
        print("📊 PROCESSING STATISTICS")
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
        
        print(f"\n🤖 LLM Usage:")
        print(f"  API Calls: {self.stats['llm_calls']}")
        print(f"  Cache Hits: {self.stats['cache_hits']}")
        
        if self.cache:
            self.cache.print_stats()
        
        print("\n" + "="*70 + "\n")

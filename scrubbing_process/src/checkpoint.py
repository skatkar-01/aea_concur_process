"""
checkpoint.py - Checkpointing System for Resumable Processing
"""

import pickle
import json
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime


class CheckpointManager:
    """
    Save processing progress and resume from failures
    """
    
    def __init__(self, checkpoint_dir: Path = None):
        """
        Initialize checkpoint manager
        
        Args:
            checkpoint_dir: Directory to store checkpoint files
        """
        self.checkpoint_dir = Path(checkpoint_dir) if checkpoint_dir else Path("checkpoints")
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"✓ Checkpoint manager initialized at: {self.checkpoint_dir}")
    
    def save_checkpoint(
        self,
        batch_id: str,
        processed_results: List[Dict],
        remaining_transactions: List[Dict],
        metadata: Dict = None
    ):
        """
        Save current processing state
        
        Args:
            batch_id: Unique identifier for this batch
            processed_results: Results processed so far
            remaining_transactions: Transactions not yet processed
            metadata: Additional metadata to save
        """
        checkpoint = {
            'batch_id': batch_id,
            'timestamp': datetime.now().isoformat(),
            'processed_count': len(processed_results),
            'remaining_count': len(remaining_transactions),
            'progress': len(processed_results) / (len(processed_results) + len(remaining_transactions)),
            'metadata': metadata or {},
            'processed_results': processed_results,
            'remaining_transactions': remaining_transactions
        }
        
        checkpoint_file = self.checkpoint_dir / f"{batch_id}.pkl"
        
        try:
            with open(checkpoint_file, 'wb') as f:
                pickle.dump(checkpoint, f)
            
            # Also save a readable summary
            summary_file = self.checkpoint_dir / f"{batch_id}_summary.json"
            summary = {
                'batch_id': batch_id,
                'timestamp': checkpoint['timestamp'],
                'processed_count': checkpoint['processed_count'],
                'remaining_count': checkpoint['remaining_count'],
                'progress': f"{checkpoint['progress']:.1%}",
                'metadata': checkpoint['metadata']
            }
            
            with open(summary_file, 'w') as f:
                json.dump(summary, f, indent=2)
            
            return True
            
        except Exception as e:
            print(f"⚠ Error saving checkpoint: {e}")
            return False
    
    def load_checkpoint(self, batch_id: str) -> Optional[Dict]:
        """
        Load saved checkpoint
        
        Args:
            batch_id: Batch identifier
            
        Returns:
            Checkpoint dict or None if not found
        """
        checkpoint_file = self.checkpoint_dir / f"{batch_id}.pkl"
        
        if not checkpoint_file.exists():
            return None
        
        try:
            with open(checkpoint_file, 'rb') as f:
                checkpoint = pickle.load(f)
            
            return checkpoint
            
        except Exception as e:
            print(f"⚠ Error loading checkpoint: {e}")
            return None
    
    def delete_checkpoint(self, batch_id: str):
        """
        Delete checkpoint after successful completion
        
        Args:
            batch_id: Batch identifier
        """
        checkpoint_file = self.checkpoint_dir / f"{batch_id}.pkl"
        summary_file = self.checkpoint_dir / f"{batch_id}_summary.json"
        
        if checkpoint_file.exists():
            checkpoint_file.unlink()
        
        if summary_file.exists():
            summary_file.unlink()
    
    def list_checkpoints(self) -> List[Dict]:
        """
        List all available checkpoints
        
        Returns:
            List of checkpoint summaries
        """
        checkpoints = []
        
        for summary_file in self.checkpoint_dir.glob("*_summary.json"):
            try:
                with open(summary_file, 'r') as f:
                    summary = json.load(f)
                checkpoints.append(summary)
            except Exception:
                continue
        
        return sorted(checkpoints, key=lambda x: x.get('timestamp', ''), reverse=True)
    
    def print_checkpoints(self):
        """Print available checkpoints"""
        checkpoints = self.list_checkpoints()
        
        if not checkpoints:
            print("No checkpoints found")
            return
        
        print(f"\n📌 Available Checkpoints ({len(checkpoints)}):")
        for cp in checkpoints:
            print(f"\n  Batch: {cp['batch_id']}")
            print(f"  Time: {cp['timestamp']}")
            print(f"  Progress: {cp['progress']}")
            print(f"  Processed: {cp['processed_count']}")
            print(f"  Remaining: {cp['remaining_count']}")

"""
cache.py - Caching System for LLM Results
"""

import json
import hashlib
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime


class ResultCache:
    """
    Cache LLM results to avoid redundant API calls
    """
    
    def __init__(self, cache_dir: Path = None):
        """
        Initialize cache
        
        Args:
            cache_dir: Directory to store cache files
        """
        self.cache_dir = Path(cache_dir) if cache_dir else Path("cache/llm_results")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        self.hits = 0
        self.misses = 0
        
        print(f"✓ Cache initialized at: {self.cache_dir}")
    
    def _get_cache_key(self, txn: Dict) -> str:
        """
        Generate cache key from transaction
        
        Key is based on:
        - Description
        - Vendor  
        - Amount
        - Expense code
        """
        key_data = {
            'description': str(txn.get('description', '')).strip(),
            'vendor': str(txn.get('vendor', '')).strip(),
            'amount': float(txn.get('amount', 0)),
            'expense_code': str(txn.get('expense_code', '')).strip(),
        }
        
        # Create deterministic JSON string
        key_str = json.dumps(key_data, sort_keys=True)
        
        # Hash to create short key
        return hashlib.sha256(key_str.encode()).hexdigest()
    
    def get(self, txn: Dict) -> Optional[Dict]:
        """
        Get cached result for transaction
        
        Args:
            txn: Transaction dictionary
            
        Returns:
            Cached result dict or None if not found
        """
        key = self._get_cache_key(txn)
        cache_file = self.cache_dir / f"{key}.json"
        
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    cached = json.load(f)
                
                self.hits += 1
                return cached
                
            except Exception as e:
                # Cache file corrupted, remove it
                cache_file.unlink()
                self.misses += 1
                return None
        else:
            self.misses += 1
            return None
    
    def set(self, txn: Dict, result: Dict):
        """
        Cache result for transaction
        
        Args:
            txn: Transaction dictionary
            result: Result to cache
        """
        key = self._get_cache_key(txn)
        cache_file = self.cache_dir / f"{key}.json"
        
        # Add metadata
        cached_result = {
            'cached_at': datetime.now().isoformat(),
            'result': result
        }
        
        try:
            with open(cache_file, 'w') as f:
                json.dump(cached_result, f, indent=2)
        except Exception as e:
            # Silently fail - caching is optional
            pass
    
    def clear(self):
        """Clear all cached results"""
        for cache_file in self.cache_dir.glob("*.json"):
            cache_file.unlink()
        
        self.hits = 0
        self.misses = 0
        print(f"✓ Cache cleared")
    
    def get_stats(self) -> Dict:
        """Get cache statistics"""
        total = self.hits + self.misses
        hit_rate = (self.hits / total * 100) if total > 0 else 0
        
        return {
            'hits': self.hits,
            'misses': self.misses,
            'total_requests': total,
            'hit_rate': hit_rate,
            'cache_size': len(list(self.cache_dir.glob("*.json")))
        }
    
    def print_stats(self):
        """Print cache statistics"""
        stats = self.get_stats()
        print(f"\n📊 Cache Statistics:")
        print(f"  Hits: {stats['hits']}")
        print(f"  Misses: {stats['misses']}")
        print(f"  Hit Rate: {stats['hit_rate']:.1f}%")
        print(f"  Cache Size: {stats['cache_size']} entries")

"""
cache.py
────────
Disk-based LLM result cache keyed by a deterministic hash of the
row payload. Persists across runs so repeated scrubs on the same
batch skip LLM API calls entirely.

Usage:
    cache = LLMCache(Path("cache"))
    result = cache.get(payload_dict)
    if result is None:
        result = call_llm(payload_dict)
        cache.set(payload_dict, result)
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


class LLMCache:
    """
    Simple JSON-file cache keyed by SHA-256 of the serialised row payload.

    Directory layout:
        cache/
          ab/cd1234...json   ← one file per unique row hash
          _stats.json        ← hit/miss counters
    """

    def __init__(self, cache_dir: Path):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._stats_path = self.cache_dir / "_stats.json"
        self._stats = self._load_stats()

    # ── Hashing ────────────────────────────────────────────────────────────────
    @staticmethod
    def _hash(payload: dict) -> str:
        """Stable SHA-256 of the payload, sorted keys for determinism."""
        blob = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    def _path(self, key: str) -> Path:
        """Store in two-char subdirectory to avoid too many files in root."""
        sub = self.cache_dir / key[:2]
        sub.mkdir(exist_ok=True)
        return sub / f"{key}.json"

    # ── Public API ─────────────────────────────────────────────────────────────
    def get(self, payload: dict) -> Optional[dict]:
        key  = self._hash(payload)
        path = self._path(key)
        if path.exists():
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                self._stats["hits"] += 1
                self._save_stats()
                log.debug("Cache HIT  %s", key[:12])
                return data
            except (json.JSONDecodeError, OSError):
                path.unlink(missing_ok=True)
        self._stats["misses"] += 1
        self._save_stats()
        log.debug("Cache MISS %s", key[:12])
        return None

    def set(self, payload: dict, result: dict) -> None:
        key  = self._hash(payload)
        path = self._path(key)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"payload": payload, "result": result,
                           "ts": time.time()}, fh, default=str)
        except OSError as exc:
            log.warning("Cache write failed: %s", exc)

    def get_batch(self, payloads: List[dict]) -> List[Optional[dict]]:
        return [self.get(p) for p in payloads]

    def set_batch(self, payloads: List[dict], results: List[dict]) -> None:
        for p, r in zip(payloads, results):
            if r:
                self.set(p, r)

    # ── Stats ──────────────────────────────────────────────────────────────────
    def _load_stats(self) -> Dict[str, int]:
        if self._stats_path.exists():
            try:
                with open(self._stats_path, encoding="utf-8") as fh:
                    return json.load(fh)
            except (json.JSONDecodeError, OSError):
                pass
        return {"hits": 0, "misses": 0}

    def _save_stats(self) -> None:
        try:
            with open(self._stats_path, "w", encoding="utf-8") as fh:
                json.dump(self._stats, fh)
        except OSError:
            pass

    def stats(self) -> Dict[str, Any]:
        total = self._stats["hits"] + self._stats["misses"]
        hit_rate = (self._stats["hits"] / total * 100) if total else 0.0
        return {**self._stats, "total": total, "hit_rate_pct": round(hit_rate, 1)}

    def clear(self) -> None:
        import shutil
        for child in self.cache_dir.iterdir():
            if child.is_dir() and len(child.name) == 2:
                shutil.rmtree(child, ignore_errors=True)
        self._stats = {"hits": 0, "misses": 0}
        self._save_stats()
        log.info("Cache cleared")

    def size(self) -> int:
        return sum(1 for _ in self.cache_dir.rglob("*.json")
                   if _.name != "_stats.json")

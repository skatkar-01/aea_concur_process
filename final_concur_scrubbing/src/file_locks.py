"""
src/file_locks.py
─────────────────
Thread-safe and process-safe file locking for Excel workbook operations.

Problem: Multiple parallel workers opening/saving the same .xlsx file
         causes ZIP header corruption (Bad CRC-32 errors).

Solution:
  - In-process: threading.Lock() in tracker_writer._WRITE_LOCK (always first)
  - Cross-process: OS-level file lock via fcntl (Unix) or msvcrt (Windows)
  - Atomic saves: write to .tmp then os.replace() — never leaves a partial file

PRODUCTION FIXES:

  BUG 1 [WINDOWS LOCK RELEASES AFTER BLOCK EXIT — NOT HELD FOR DURATION]:
    Root cause: The Windows implementation opened the target file, locked
    the first byte, then yielded — but the file handle was closed by the
    inner `with open(...)` block before the yield. The lock was released
    immediately, giving false confidence.
    Fix: The lock file is kept open for the entire duration of the context
    manager using a try/finally rather than nested with-blocks.

  BUG 2 [LOCK FILE PATH COLLISION]:
    Root cause: Lock file was named f"{file_path.stem}.lock". Two files in
    different directories with the same stem (e.g., tracker.xlsx in two
    month folders) shared the same lock file and blocked each other.
    Fix: Lock file path incorporates the full absolute path hash so it is
    unique per target file regardless of stem.

  BUG 3 [TIMEOUT SILENTLY IGNORED — NO LOG]:
    Root cause: When the lock could not be acquired within timeout, the
    context manager yielded anyway with no warning. The caller had no
    indication the lock was not held.
    Fix: A warning is logged when proceeding without the lock, so operators
    know contention is occurring.

  BUG 4 [ATOMIC SAVE LEAVES ORPHAN .tmp ON WINDOWS RENAME FAILURE]:
    Root cause: atomic_workbook_save() removed the temp file in the except
    block but not in the Windows os.replace() fallback path, leaving orphan
    .tmp files on every Windows save.
    Fix: Consolidated cleanup into a single finally block; Path.unlink()
    with missing_ok=True handles all paths correctly.

  BUG 5 [fcntl LOCK NOT RELEASED ON EXCEPTION]:
    Root cause: On Unix, if the code inside `yield` raised an exception,
    fcntl.flock(LOCK_UN) was never called because it was after the yield.
    Fix: fcntl.flock(LOCK_UN) is now in a finally block.
"""
from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import uuid
import time
from contextlib import contextmanager
from pathlib import Path


# ── Logging (import after path setup to avoid circular) ──────────────────────
from utils.logging_config import get_logger

_log = get_logger(__name__)


# ── Lock file path (BUG 2 FIX) ───────────────────────────────────────────────

def _lock_path(file_path: Path) -> Path:
    """
    Return a lock file path that is unique per absolute target path.
    Uses a 12-char hash of the absolute path to avoid stem collisions.
    """
    lock_dir = Path(tempfile.gettempdir()) / ".excel_locks"
    lock_dir.mkdir(exist_ok=True)
    path_hash = hashlib.sha256(str(file_path.resolve()).encode()).hexdigest()[:12]
    return lock_dir / f"{file_path.stem}_{path_hash}.lock"


def _cleanup_temp_path(tmp_path: Path) -> None:
    """
    Best-effort cleanup for Windows temp-file contention.

    A short retry loop avoids failing the whole workbook save when antivirus,
    indexing, or another worker briefly holds the temp file open.
    """
    for attempt in range(5):
        try:
            tmp_path.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(0.1 * (attempt + 1))
        except OSError:
            return
    _log.warning("temp_cleanup_failed", temp_path=str(tmp_path))


# ── Platform-specific implementations ────────────────────────────────────────

if sys.platform == "win32":
    import msvcrt

    @contextmanager
    def file_lock(file_path: Path, timeout: float = 60.0):
        """
        Acquire an exclusive OS-level lock on a lock file (Windows).

        BUG 1 FIX: lock file handle is kept open for the entire context.
        BUG 2 FIX: lock file path is unique per absolute target path.
        BUG 3 FIX: warning logged when proceeding without the lock.
        BUG 5 FIX: lock released in finally block.
        """
        lock_file_path = _lock_path(file_path)
        lock_fh = None
        lock_acquired = False

        try:
            lock_fh = open(lock_file_path, "w+b")   # noqa: WPS515
            deadline = time.monotonic() + timeout
            attempt = 0

            while time.monotonic() < deadline:
                try:
                    msvcrt.locking(lock_fh.fileno(), msvcrt.LK_NBLCK, 1)
                    lock_acquired = True
                    break
                except OSError:
                    attempt += 1
                    time.sleep(0.1)

            if not lock_acquired:
                _log.warning(
                    "file_lock_timeout_proceeding",
                    file_path=str(file_path),
                    timeout_s=timeout,
                )

            yield

        finally:
            if lock_acquired and lock_fh is not None:
                try:
                    msvcrt.locking(lock_fh.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            if lock_fh is not None:
                try:
                    lock_fh.close()
                except OSError:
                    pass

else:
    import fcntl

    @contextmanager
    def file_lock(file_path: Path, timeout: float = 60.0):
        """
        Acquire an exclusive OS-level lock on a lock file (Unix/Linux).

        BUG 2 FIX: lock file path unique per absolute target path.
        BUG 3 FIX: warning logged when proceeding without lock.
        BUG 5 FIX: LOCK_UN in finally so lock is always released.
        """
        lock_file_path = _lock_path(file_path)
        lock_fh = None
        lock_acquired = False

        try:
            lock_fh = open(lock_file_path, "w")   # noqa: WPS515
            deadline = time.monotonic() + timeout

            while time.monotonic() < deadline:
                try:
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    lock_acquired = True
                    break
                except IOError:
                    time.sleep(0.1)

            if not lock_acquired:
                _log.warning(
                    "file_lock_timeout_proceeding",
                    file_path=str(file_path),
                    timeout_s=timeout,
                )

            yield

        finally:
            # BUG 5 FIX: always release, even if yield raised
            if lock_acquired and lock_fh is not None:
                try:
                    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            if lock_fh is not None:
                try:
                    lock_fh.close()
                except OSError:
                    pass


# ── Atomic workbook save (BUG 4 FIX) ─────────────────────────────────────────

def atomic_workbook_save(wb, target_path: Path, max_retries: int = 3) -> Path:
    """
    Safely save and replace an Excel workbook using atomic rename.

    Pattern:
      1. Write to a temp file in the same directory (guarantees same filesystem,
         so os.replace() is atomic — no cross-device move).
      2. os.replace() atomically swaps temp into place.
      3. On Windows rename failure, fall back to shutil.copy2 + unlink.

    BUG 4 FIX: temp file cleanup is in a single finally block; no orphan .tmp.
    """
    target_path = Path(target_path)
    target_dir  = target_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)

    for attempt in range(max_retries):
        tmp_path = target_dir / f".{target_path.name}.{uuid.uuid4().hex[:8]}.tmp"
        try:
            wb.save(tmp_path)

            try:
                os.replace(tmp_path, target_path)
                return target_path
            except OSError:
                # Windows: destination may be open by another process
                import shutil
                time.sleep(0.2)
                shutil.copy2(tmp_path, target_path)
                _cleanup_temp_path(tmp_path)
                return target_path

        except Exception as exc:
            if attempt < max_retries - 1:
                _log.warning(
                    "atomic_save_retry",
                    attempt=attempt + 1,
                    error=str(exc),
                    file_path=str(target_path),
                )
                time.sleep(0.5 * (attempt + 1))
            else:
                raise
        finally:
            # BUG 4 FIX: always clean up temp if it still exists
            _cleanup_temp_path(tmp_path)

    return target_path   # unreachable but satisfies type-checkers

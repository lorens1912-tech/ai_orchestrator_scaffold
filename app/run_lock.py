from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

ROOT_DIR = Path(__file__).resolve().parent.parent
LOCKS_DIR = ROOT_DIR / "locks"


class LockError(RuntimeError):
    pass


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


@contextmanager
def acquire_book_lock(book_id: str, timeout_sec: int = 10, stale_after_sec: int = 3600) -> Iterator[Path]:
    """
    Cross-platform lock via atomic create (O_EXCL).
    Windows-friendly, no external deps.
    """
    if not book_id or not book_id.strip():
        raise LockError("book_id is empty")

    _ensure_dir(LOCKS_DIR)
    lock_path = LOCKS_DIR / f"book_{book_id}.lock"
    deadline = time.time() + timeout_sec

    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, f"pid={os.getpid()}\nts={time.time()}\n".encode("utf-8"))
            finally:
                os.close(fd)
            break
        except FileExistsError:
            # stale lock?
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > stale_after_sec:
                    try:
                        lock_path.unlink()
                        continue
                    except Exception:
                        pass
            except Exception:
                pass

            if time.time() >= deadline:
                raise LockError(f"Lock busy for book_id={book_id} (timeout {timeout_sec}s): {lock_path}")
            time.sleep(0.2)

    try:
        yield lock_path
    finally:
        try:
            lock_path.unlink()
        except Exception:
            pass

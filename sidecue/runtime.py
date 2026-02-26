from __future__ import annotations

from contextlib import contextmanager
import fcntl
from pathlib import Path


@contextmanager
def app_lock(path: str | Path | None):
    """Share one advisory lock between the app and its local bundle builder."""
    if path is None:
        yield
        return
    with Path(path).open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Sidecue is already running or being built. Close the existing window first.") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)

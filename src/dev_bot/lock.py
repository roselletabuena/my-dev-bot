"""Single-instance run lock so overlapping scheduler runs cannot double-process tickets."""

from __future__ import annotations

import os
import time
from pathlib import Path


class LockHeld(RuntimeError):
    pass


class RunLock:
    def __init__(self, path: Path, stale_after_s: int = 2 * 3600):
        self.path = path
        self.stale_after_s = stale_after_s

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and time.time() - self.path.stat().st_mtime > self.stale_after_s:
            self.path.unlink(missing_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise LockHeld(f"another run holds {self.path}") from None
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return self

    def __exit__(self, *exc: object) -> None:
        self.path.unlink(missing_ok=True)

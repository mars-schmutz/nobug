"""Thread-safe breakpoint store.

The UI thread mutates this while the debuggee thread reads it from inside the
trace hook, hence the lock. Files are keyed by real path so a breakpoint set
from the UI matches the filename CPython reports for a frame.
"""

from __future__ import annotations

import os
import threading


def normalize(path: str) -> str:
    """Canonical key for a source file. Every filename comparison goes through it."""
    return os.path.realpath(path)


class Breakpoints:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._points: dict[tuple[str, int], str | None] = {}

    def toggle(self, file: str, line: int, condition: str | None = None) -> bool:
        """Add or remove a breakpoint. Returns True if it now exists."""
        key = (normalize(file), line)
        with self._lock:
            if key in self._points:
                del self._points[key]
                return False
            self._points[key] = condition
            return True

    def set(self, file: str, line: int, condition: str | None = None) -> None:
        with self._lock:
            self._points[(normalize(file), line)] = condition

    def clear(self, file: str, line: int) -> None:
        with self._lock:
            self._points.pop((normalize(file), line), None)

    def lines_for(self, file: str) -> set[int]:
        """Line numbers with a breakpoint in *file* (for the source gutter)."""
        target = normalize(file)
        with self._lock:
            return {line for (f, line), _ in self._points.items() if f == target}

    def snapshot(self) -> dict[tuple[str, int], str | None]:
        with self._lock:
            return dict(self._points)

    def restore(self, points: dict[tuple[str, int], str | None]) -> None:
        with self._lock:
            self._points = dict(points)

    def hits(self, file: str, line: int, frame) -> bool:
        """Whether execution at (file, line) should break in this *frame*."""
        key = (normalize(file), line)
        with self._lock:
            if key not in self._points:
                return False
            condition = self._points[key]
        if condition is None:
            return True
        try:
            return bool(eval(condition, frame.f_globals, frame.f_locals))
        except Exception:
            # A broken condition shouldn't silently swallow the breakpoint.
            return True

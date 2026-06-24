"""Thread-safe store of pinned watch expressions (pdb/gdb ``display``)."""

from __future__ import annotations

import threading

from .state import WatchValue, describe


class Watches:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._exprs: list[str] = []

    def add(self, expr: str) -> None:
        expr = expr.strip()
        if not expr:
            return
        with self._lock:
            if expr not in self._exprs:
                self._exprs.append(expr)

    def remove(self, expr: str) -> None:
        with self._lock:
            if expr in self._exprs:
                self._exprs.remove(expr)

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self._exprs)

    def restore(self, exprs: list[str]) -> None:
        with self._lock:
            self._exprs = list(exprs)

    def evaluate(self, frame) -> list[WatchValue]:
        """Resolve every pinned expression against *frame*'s namespaces."""
        with self._lock:
            exprs = list(self._exprs)
        results: list[WatchValue] = []
        for expr in exprs:
            try:
                value = eval(expr, frame.f_globals, frame.f_locals)
            except Exception as exc:
                results.append(WatchValue(expr=expr, error=f"{exc.__class__.__name__}: {exc}"))
            else:
                results.append(WatchValue(expr=expr, value=describe(value)))
        return results

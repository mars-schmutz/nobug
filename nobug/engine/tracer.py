"""The stepping core, built on ``sys.settrace``.

settrace works on every supported CPython (3.9+) and on Linux and macOS alike.
Moving to ``sys.monitoring`` (3.12+) would cut overhead, and it could hide
behind this same class, but it isn't done yet.

Stepping is expressed in terms of frame depth, measured by walking ``f_back``
to the bottom frame. That keeps the logic independent of which frames we trace:

* step into: stop at the next user line anywhere
* step over: stop at the next user line at this depth or shallower
* step out:  stop at the next user line strictly shallower than this one
* continue:  stop only at a breakpoint (or a one-shot "run to" target)
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable

from .breakpoints import Breakpoints, normalize
from .commands import Command


class DebuggeeExit(BaseException):
    """Raised inside the debuggee to unwind it on QUIT.

    A ``BaseException`` rather than ``Exception``, so the target program's own
    ``except Exception`` handlers can't swallow the teardown. ``KeyboardInterrupt``
    and ``SystemExit`` pass through for the same reason.
    """


def _prefix_dirs() -> tuple[str, ...]:
    seen = []
    for base in (sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix):
        rp = os.path.realpath(base)
        if rp not in seen:
            seen.append(rp)
    return tuple(seen)


class Tracer:
    """Drives ``sys.settrace`` for a single (single-threaded) debuggee."""

    def __init__(
        self,
        on_pause: Callable,  # (frame, event) -> Command
        breakpoints: Breakpoints,
        package_dir: str,
    ) -> None:
        self.on_pause = on_pause
        self.breakpoints = breakpoints
        self._prefixes = _prefix_dirs()
        self._package_dir = os.path.realpath(package_dir)

        self.mode = Command.STEP_INTO  # stop on the first user line
        self.cmd_depth = 0
        self.botframe = None
        self.temp_stop: tuple[str, int] | None = None  # one-shot "run to"
        self._computing = False  # re-entrancy guard while building a PauseState

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        sys.settrace(self._global_trace)

    def stop(self) -> None:
        sys.settrace(None)

    # -- file classification --------------------------------------------
    def is_user_file(self, path: str) -> bool:
        """User code = not stdlib, not site-packages, not nobug itself."""
        if not path or path.startswith("<"):
            return False
        rp = os.path.realpath(path)
        if rp.startswith(self._package_dir):
            return False
        return not any(rp.startswith(p) for p in self._prefixes)

    # -- depth -----------------------------------------------------------
    def _depth(self, frame) -> int:
        depth = 0
        f = frame
        while f is not None:
            depth += 1
            if f is self.botframe:
                break
            f = f.f_back
        return depth

    # -- trace callbacks -------------------------------------------------
    def _global_trace(self, frame, event, arg):
        if self._computing:
            return None
        if event != "call":
            return None
        if not self.is_user_file(frame.f_code.co_filename):
            # Don't trace into stdlib/3rd-party frames; we still see them as
            # depth when user code calls back out, via _depth's f_back walk.
            return None
        if self.botframe is None:
            self.botframe = frame
        return self._local_trace

    def _local_trace(self, frame, event, arg):
        if self._computing:
            return self._local_trace
        if event == "line":
            self._maybe_stop(frame)
        return self._local_trace

    def _should_stop(self, frame, file: str, line: int) -> bool:
        if self.temp_stop is not None and normalize(file) == self.temp_stop[0] and line == self.temp_stop[1]:
            self.temp_stop = None
            return True
        if self.breakpoints.hits(file, line, frame):
            return True
        depth = self._depth(frame)
        if self.mode == Command.STEP_INTO:
            return True
        if self.mode == Command.STEP_OVER:
            return depth <= self.cmd_depth
        if self.mode == Command.STEP_OUT:
            return depth < self.cmd_depth
        return False  # CONTINUE

    def _maybe_stop(self, frame) -> None:
        file = frame.f_code.co_filename
        line = frame.f_lineno
        if not self._should_stop(frame, file, line):
            return

        # Build the PauseState and block for the user's command with tracing
        # suppressed, so evaluating watches / reprs / sub-expressions (which may
        # run user __repr__ code) cannot re-enter the tracer.
        self._computing = True
        try:
            command = self.on_pause(frame, "line")
        finally:
            self._computing = False

        if command == Command.QUIT:
            # Unwind the debuggee's stack so the thread ends cleanly: its
            # finally in DebugSession._run restores sys.stdout/argv. This serves
            # both app-quit and in-app restart.
            raise DebuggeeExit
        self.mode = command
        self.cmd_depth = self._depth(frame)

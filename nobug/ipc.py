"""The thread-safe bridge between the debuggee thread and the UI thread.

The debuggee runs in its own thread. When the tracer pauses, it puts a
:class:`~nobug.engine.state.PauseState` on a queue and blocks the debuggee
thread on an event (a blocked thread releases the GIL, so the UI stays
responsive). The UI drains the queue, renders, and when the user steps it sends
the next command back, which wakes the debuggee.
"""

from __future__ import annotations

import queue
import threading

from .engine.commands import Command
from .engine.state import PauseState


class Bridge:
    def __init__(self) -> None:
        self.states: "queue.Queue[PauseState]" = queue.Queue()
        self._resume = threading.Event()
        self._next: Command | None = None

    # -- debuggee thread -------------------------------------------------
    def pause(self, state: PauseState) -> Command:
        """Publish a pause and block until the UI sends a resume command."""
        self.states.put(state)
        self._resume.wait()
        self._resume.clear()
        cmd, self._next = self._next, None
        return cmd if cmd is not None else Command.CONTINUE

    def finish(self, state: PauseState) -> None:
        """Publish a terminal state (program ended / crashed). No resume."""
        self.states.put(state)

    # -- UI thread -------------------------------------------------------
    def send(self, command: Command) -> None:
        self._next = command
        self._resume.set()

    def poll(self) -> PauseState | None:
        """Non-blocking: the next pending state, or None."""
        try:
            return self.states.get_nowait()
        except queue.Empty:
            return None

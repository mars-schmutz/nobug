"""Resume commands the UI sends back to a paused debug engine."""

from __future__ import annotations

from enum import Enum, auto


class Command(Enum):
    """How execution should proceed from the current pause.

    These map onto the canonical pdb/gdb stepping verbs (see PROJECT_PLAN §6).
    Anything that does *not* resume execution (setting a breakpoint, adding a
    watch, evaluating an expression) is applied to shared state directly and
    does not flow through here.
    """

    STEP_INTO = auto()   # pdb `s` / gdb `s`  — stop at the next line anywhere
    STEP_OVER = auto()   # pdb `n` / gdb `n`  — stop at the next line in this frame or shallower
    STEP_OUT = auto()    # pdb `r` / gdb `finish` — run until this frame returns
    CONTINUE = auto()    # pdb `c` / gdb `c`  — run until a breakpoint (or temp stop)
    QUIT = auto()        # stop debugging

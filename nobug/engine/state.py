"""Plain data passed from the engine to the UI at each pause.

These dataclasses are the whole engine-to-UI interface. They carry already
rendered values (see :class:`ValueRepr`), computed while the engine still holds
the live objects, so the UI never touches a raw debuggee object. That also lets
an out-of-process frontend serialize these structures unchanged later on.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Rendering limits. Keep captures bounded so a huge or cyclic object can't
# stall the debugger or blow up the UI.
MAX_STR = 150
MAX_ITEMS = 50
MAX_DEPTH = 2


def short_repr(value: object, limit: int = MAX_STR) -> str:
    """A safe, length-bounded ``repr`` that never raises."""
    try:
        text = repr(value)
    except Exception as exc:  # a debuggee's __repr__ may be broken
        return f"<unreprable: {exc.__class__.__name__}>"
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


@dataclass
class ValueRepr:
    """A depth-limited, eager rendering of a Python value."""

    type_name: str
    text: str
    children: list[tuple[str, "ValueRepr"]] | None = None


def _container_items(value: object) -> list[tuple[str, object]] | None:
    """The (label, child) pairs to recurse into, or None if *value* is a leaf."""
    if isinstance(value, dict):
        return [(short_repr(k, 40), v) for k, v in list(value.items())[:MAX_ITEMS]]
    if isinstance(value, (list, tuple)) and not isinstance(value, (str, bytes, bytearray)):
        return [(str(i), v) for i, v in enumerate(list(value)[:MAX_ITEMS])]
    if isinstance(value, (set, frozenset)):
        return [("•", v) for v in list(value)[:MAX_ITEMS]]
    return None


def describe(value: object, depth: int = 0, _seen: frozenset | None = None) -> ValueRepr:
    """Render *value* into a :class:`ValueRepr`, one container level at a time.

    Locals, watches, and on-demand evaluation all render through here, so a
    value looks the same wherever it shows up.
    """
    if _seen is None:
        _seen = frozenset()
    vr = ValueRepr(type_name=type(value).__name__, text=short_repr(value))
    if depth >= MAX_DEPTH:
        return vr

    vid = id(value)
    children: list[tuple[str, ValueRepr]] | None = None
    try:
        items = _container_items(value)
        if items is not None:
            if vid in _seen:
                return vr  # cycle: stop at the leaf repr
            seen = _seen | {vid}
            children = [(label, describe(v, depth + 1, seen)) for label, v in items]
    except Exception:
        children = None  # iteration over a hostile object: degrade to the leaf repr

    if children:
        vr.children = children
    return vr


@dataclass
class FrameSnapshot:
    """One frame in the call stack at a pause, with its own locals.

    Per-frame locals let the UI walk the stack with ``u``/``d`` and show each
    frame's variables, the way pdb does.
    """

    func: str
    file: str
    line: int
    locals: list[tuple[str, "ValueRepr"]] = field(default_factory=list)
    changed: set[str] = field(default_factory=set)


@dataclass
class ExprStep:
    """One resolved sub-expression on the current line (the Thonny-style view).

    ``col``/``end_col`` are character offsets into the source line, so the UI
    can underline the exact span being explained.
    """

    text: str
    value_text: str
    type_name: str
    col: int
    end_col: int
    # A recorded value the live prediction can't produce (a call, comprehension,
    # f-string, ...). Used to decide when the post-run collapse is worth showing.
    opaque: bool = False


@dataclass
class WatchValue:
    """A pinned expression and its current value (or the error evaluating it)."""

    expr: str
    value: ValueRepr | None = None
    error: str | None = None
    changed: bool = False


@dataclass
class PauseState:
    """Everything the UI needs to render a single pause."""

    event: str = "line"               # "line" | "finished" | "exception"
    file: str = ""
    line: int = 0
    func: str = ""
    line_text: str = ""
    stack: list[FrameSnapshot] = field(default_factory=list)
    watches: list[WatchValue] = field(default_factory=list)
    expr_steps: list[ExprStep] = field(default_factory=list)
    # The predicted "⇒ target = value" final step for an assignment line, if any.
    line_result: ExprStep | None = None
    # The line a step-over just executed, resolved with real values (incl. call
    # results). Set only when that line contained a call, empty otherwise.
    ran_line_text: str = ""
    ran_steps: list[ExprStep] = field(default_factory=list)
    message: str = ""                 # used by "finished"/"exception"

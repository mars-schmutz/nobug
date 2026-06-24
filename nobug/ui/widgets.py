"""Panel widgets and the shared value-formatting helpers.

``InfoPanel`` is the reusable scrollable, titled panel behind the watch,
stack/locals, and expression views, each fed different rendered text.
``SourceView`` is the one bespoke panel, since it needs a gutter
(breakpoint/current/cursor markers) and a movable cursor.

The ``format_*`` functions turn engine dataclasses into Rich renderables. Every
panel goes through them, so values render the same way everywhere.
"""

from __future__ import annotations

from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static

from ..engine.state import ExprStep, FrameSnapshot, ValueRepr, WatchValue


# Distinct colour for values already resolved on the in-line ("e") view, so
# evaluated pieces stand out from the un-evaluated yellow rest of the line.
EVALUATED_INLINE_STYLE = "bold green"


# --------------------------------------------------------------------------
# Formatting helpers (pure functions -> rich.Text)
# --------------------------------------------------------------------------
def _paint_spans(text: Text, line: str, base: str, spans: list[tuple[int, int, str]]) -> None:
    """Append *line* to *text*, painting each ``(start, end, style)`` span over
    *base*. Later spans win where they overlap, so callers append the primary
    highlight last."""
    styles = [base] * len(line)
    for start, end, style in spans:
        for i in range(max(0, start), min(len(line), end)):
            styles[i] = style
    i = 0
    while i < len(line):
        j = i + 1
        while j < len(line) and styles[j] == styles[i]:
            j += 1
        text.append(line[i:j], style=styles[i])
        i = j


def _append_value(text: Text, label: str, vr: ValueRepr, indent: int, highlight: bool = False) -> None:
    pad = "  " * indent
    text.append(f"{pad}{label}", style="bold yellow" if highlight else "cyan")
    text.append(" = ")
    text.append(vr.text, style="bold yellow" if highlight else "white")
    text.append(f"  {vr.type_name}\n", style="dim")
    if vr.children:
        for child_label, child_vr in vr.children:
            _append_value(text, child_label, child_vr, indent + 1)


def format_locals(local_items: list[tuple[str, ValueRepr]], changed: set[str] = frozenset()) -> Text:
    text = Text()
    if not local_items:
        return text.append("(no locals)", style="dim")
    for name, vr in local_items:
        _append_value(text, name, vr, 0, highlight=name in changed)
    return text


def format_watches(watches: list[WatchValue]) -> Text:
    text = Text()
    if not watches:
        return text.append("(no pinned expressions — :display EXPR)", style="dim")
    for w in watches:
        if w.error is not None:
            text.append(w.expr, style="yellow")
            text.append("  ")
            text.append(w.error + "\n", style="red")
        else:
            _append_value(text, w.expr, w.value, 0, highlight=w.changed)
    return text


def format_stack(stack: list[FrameSnapshot], selected: int) -> Text:
    text = Text()
    for i, frame in enumerate(stack):
        marker = "▶ " if i == selected else "  "
        style = "bold yellow" if i == selected else "white"
        loc = frame.file.rsplit("/", 1)[-1]
        text.append(f"{marker}{frame.func}()", style=style)
        text.append(f"  {loc}:{frame.line}\n", style="dim")
    return text


def _append_steps(text: Text, steps: list[ExprStep], active: int = -1) -> None:
    """Render an innermost-first step list, with the "→" column aligned.

    When *active* >= 0 it's the count of steps already resolved by the in-line
    ("e") walk, so the panel tracks the same position: resolved steps get a
    ``✓`` and the one about to be evaluated a ``▶`` pointer. A negative *active*
    turns the markers off (used for the already-executed "last evaluated" list).
    """
    width = max((len(s.text) for s in steps), default=0)
    for i, step in enumerate(steps, 1):
        if active >= 0 and i == active + 1:
            marker, gutter_style, text_style = "▶", "bold yellow", "bold yellow"
        elif active >= 0 and i <= active:
            marker, gutter_style, text_style = "✓", "green", "cyan"
        else:
            marker, gutter_style, text_style = " ", "dim", "cyan"
        text.append(f"{marker} {i}. ", style=gutter_style)
        text.append(step.text.ljust(width), style=text_style)
        text.append(" → ", style="dim")
        text.append(step.value_text, style="green")
        text.append(f"  {step.type_name}\n", style="dim")


def format_expr(
    line_text: str,
    steps: list[ExprStep],
    result: ExprStep | None = None,
    ran_line_text: str = "",
    ran_steps: list[ExprStep] | None = None,
    active: int = -1,
) -> Text:
    """Render the line and the steps that resolve it, left to right.

    The plain source line sits on top. Each numbered step shows one
    sub-expression resolving to its value (``nums → ...``, ``i → 2``, then
    ``nums[i] → 30``), so the reader can follow the build-up to the whole
    expression. For an assignment, a final ``⇒ target = value`` line shows what
    the line produces.

    *active* is the in-line ("e") progress (count of steps resolved), so the
    panel can point a ``▶`` at the sub-expression about to be evaluated and tick
    off the resolved ones. A negative value leaves the list unmarked.

    When *ran_steps* is given, a "last evaluated" section shows the line a
    step-over just ran, collapsed to its real values (including call results).
    """
    text = Text()
    text.append(line_text.strip() + "\n", style="bold")
    if not steps and result is None:
        text.append("(no resolvable sub-expressions on this line)\n", style="dim")
    else:
        _append_steps(text, steps, active)
        if result is not None:
            on_result = active >= 0 and active == len(steps) and bool(steps)
            text.append("▶ ⇒ " if on_result else "  ⇒ ", style="bold yellow" if on_result else "bold")
            text.append(result.text, style="magenta")
            text.append(" = ", style="dim")
            text.append(result.value_text, style="bold green")
            text.append(f"  {result.type_name}\n", style="dim")

    if ran_steps:
        text.append("\nlast evaluated\n", style="bold")
        text.append(ran_line_text.strip() + "\n", style="dim")
        _append_steps(text, ran_steps)
    return text


def substitute_inline(
    line_text: str, steps: list[ExprStep], count: int
) -> tuple[str, tuple[int, int] | None, str | None, list[tuple[int, int]]]:
    """Apply the first *count* resolved steps to *line_text*, Thonny-style.

    The in-line counterpart of :func:`format_expr`; both render the same
    innermost-first ``ExprStep`` list. Each step replaces its source span with
    its ``value_text``, and as *count* grows the line collapses outward until
    the widest span (the whole right-hand side) replaces everything.

    Returns the substituted line, a ``(start, end)`` span into it to highlight,
    a *kind*, and the spans of values already resolved on the line (so the UI
    can colour evaluated values apart from the rest). *kind* is ``"next"`` while
    the highlight span is the sub-expression ``e`` will resolve on the following
    press (previewed before it runs, like the source line), and ``"result"``
    once every resolvable step has been applied and the span is the final value.
    Returns ``(line_text, None, None, [])`` when nothing applied.
    """
    applied = steps[:count]
    if not applied:
        return line_text, None, None, []

    # Spans are nested or disjoint; drop any applied step contained in a wider
    # one, whose value already subsumes it (once "a * b" resolves, a and b go).
    def subsumed(s: ExprStep) -> bool:
        return any(
            o is not s
            and o.col <= s.col
            and o.end_col >= s.end_col
            and (o.end_col - o.col) > (s.end_col - s.col)
            for o in applied
        )

    visible = sorted((s for s in applied if not subsumed(s)), key=lambda s: s.col)

    # The newest step is the widest applied so far, so never subsumed.
    newest = applied[-1]
    out: list[str] = []
    pos = 0
    spans: list[tuple[ExprStep, tuple[int, int]]] = []
    for s in visible:
        out.append(line_text[pos : s.col])
        start = sum(len(part) for part in out)
        out.append(s.value_text)
        spans.append((s, (start, start + len(s.value_text))))
        pos = s.end_col
    out.append(line_text[pos:])
    text = "".join(out)

    # More steps remain: preview where the next press lands. Map its source span
    # through the substitutions already applied (each visible step shifts every
    # later column by the difference between its value and the text it replaced).
    if count < len(steps):
        nxt = steps[count]
        shift = lambda col: col + sum(
            len(s.value_text) - (s.end_col - s.col) for s in visible if s.end_col <= col
        )
        done = [span for _, span in spans]
        return text, (shift(nxt.col), shift(nxt.end_col)), "next", done
    result_span = next(span for s, span in spans if s is newest)
    done = [span for s, span in spans if s is not newest]
    return text, result_span, "result", done


# --------------------------------------------------------------------------
# Panels
# --------------------------------------------------------------------------
class InfoPanel(VerticalScroll):
    """A scrollable, titled panel that displays a Rich renderable."""

    can_focus = True

    def __init__(self, title: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.border_title = title
        self._body = Static("", expand=True)

    def compose(self):
        yield self._body

    def set_content(self, renderable) -> None:
        self._body.update(renderable)


class SourceView(VerticalScroll):
    """Source panel with a gutter (breakpoints, current line, cursor)."""

    can_focus = True

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.border_title = "Source"
        self._body = Static("", expand=True)
        self.file: str = ""
        self.lines: list[str] = []
        self.current_line: int | None = None
        self.cursor_line: int = 1
        self._breakpoints: set[int] = set()
        self._inline_line: int | None = None
        self._inline_text: str = ""
        self._inline_hl: tuple[int, int] | None = None
        self._inline_hl_kind: str | None = None
        self._inline_done: list[tuple[int, int]] = []
        self._next_span: tuple[int, int] | None = None

    def compose(self):
        yield self._body

    def load(self, file: str, lines: list[str]) -> None:
        self.file = file
        self.lines = [ln.rstrip("\n") for ln in lines]
        self.cursor_line = min(self.cursor_line, max(1, len(self.lines)))
        self.render_source()

    def set_current(self, line: int | None) -> None:
        self.current_line = line
        self._next_span = None
        if line is not None:
            self.cursor_line = line
        self.render_source()
        self._scroll_to_focus()

    def set_next_eval(self, span: tuple[int, int] | None) -> None:
        """Highlight the sub-expression ``e`` resolves first on the current line."""
        self._next_span = span
        self.render_source()

    def set_breakpoints(self, lines: set) -> None:
        self._breakpoints = lines
        self.render_source()

    def set_inline(
        self,
        line: int,
        text: str,
        highlight: tuple[int, int] | None,
        kind: str | None = None,
        done: list[tuple[int, int]] | None = None,
    ) -> None:
        """Overlay *line* with its resolved-in-line text (the 'e' key).

        *kind* is ``"next"`` to flag the span as pending (yellow, like the
        source preview) or ``"result"`` for a resolved value (green). *done* are
        the spans of values already resolved, painted in a distinct colour.
        """
        self._inline_line = line
        self._inline_text = text
        self._inline_hl = highlight
        self._inline_hl_kind = kind
        self._inline_done = done or []
        self.render_source()

    def clear_inline(self) -> None:
        if self._inline_line is None:
            return
        self._inline_line = None
        self._inline_text = ""
        self._inline_hl = None
        self._inline_done = []
        self.render_source()

    def move_cursor(self, delta: int) -> None:
        if not self.lines:
            return
        self.cursor_line = max(1, min(len(self.lines), self.cursor_line + delta))
        self.render_source()
        self._scroll_to_focus()

    def goto(self, line: int) -> None:
        if not self.lines:
            return
        self.cursor_line = max(1, min(len(self.lines), line))
        self.render_source()
        self._scroll_to_focus()

    def render_source(self) -> None:
        text = Text()
        width = len(str(len(self.lines))) if self.lines else 1
        for idx, line in enumerate(self.lines, start=1):
            bp = "●" if idx in self._breakpoints else " "
            cur = "▶" if idx == self.current_line else " "
            gutter = f"{bp}{cur} {idx:>{width}} "
            on_cursor = idx == self.cursor_line
            gutter_style = "red" if idx in self._breakpoints else "dim"
            text.append(gutter, style=gutter_style)

            if idx == self._inline_line:
                inline = self._inline_text or " "
                spans = [(s, e, EVALUATED_INLINE_STYLE) for s, e in self._inline_done]
                if self._inline_hl is not None:
                    s, e = self._inline_hl
                    hl = "bold black on yellow" if self._inline_hl_kind == "next" else "bold black on bright_green"
                    spans.append((s, e, hl))
                _paint_spans(text, inline, "yellow", spans)
                text.append("\n")
                continue

            line_style = "reverse" if on_cursor else ""
            if idx == self.current_line and not on_cursor:
                line_style = "yellow"
            if idx == self.current_line and self._next_span is not None:
                col, end = self._next_span
                if 0 <= col < end <= len(line):
                    _paint_spans(text, line, line_style, [(col, end, "bold black on yellow")])
                    text.append("\n")
                    continue
            # Pad empty lines with a space so the cursor/current highlight is
            # still visible on otherwise-blank lines.
            text.append((line or " ") + "\n", style=line_style)
        self._body.update(text)

    def _scroll_to_focus(self) -> None:
        target = self.current_line or self.cursor_line
        height = self.size.height or 20
        self.scroll_to(y=max(0, target - 1 - height // 2), animate=False)

"""The nobug Textual application."""

from __future__ import annotations

from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, RichLog, Static

from ..engine.session import DebugSession
from ..engine.state import PauseState
from . import keys
from .widgets import (
    InfoPanel,
    SourceView,
    format_expr,
    format_locals,
    format_stack,
    format_watches,
    substitute_inline,
)


class NobugApp(App):
    CSS = """
    Screen { layout: vertical; }
    #status { height: 1; background: $primary; color: $text; padding: 0 1; }
    #main { height: 1fr; }
    #left { width: 2fr; }
    #right { width: 3fr; }
    #source { height: 1fr; }
    #expr { height: 14; }
    #watch { height: 1fr; }
    #stack { height: 1fr; }
    #console { height: 8; border: round $panel-darken-1; padding: 0 1; }
    #console:focus { border: round $accent; }
    #cmd { dock: bottom; display: none; border: round $accent; }
    .panel { border: round $panel-darken-1; }
    .panel:focus { border: round $accent; }
    """

    def __init__(self, session: DebugSession) -> None:
        super().__init__()
        self.session = session
        self.state: PauseState | None = None
        self.running = True
        self.frame_index = 0
        self._inline_step = 0

        self._pending: str | None = None      # 'g' or 'ctrl+w'
        self._input_mode: str | None = None    # 'command' | 'search' | 'stdin'
        self._search_term = ""
        self._matches: list[int] = []
        self._match_idx = 0

    # -- layout ----------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static("", id="status")
        with Horizontal(id="main"):
            with Vertical(id="left"):
                yield InfoPanel("Stack / Locals", id="stack", classes="panel")
                yield InfoPanel("Watches", id="watch", classes="panel")
            with Vertical(id="right"):
                yield SourceView(id="source", classes="panel")
                yield InfoPanel("Expression", id="expr", classes="panel")
        yield RichLog(id="console", highlight=False, markup=False, wrap=True)
        yield Input(id="cmd")

    def on_mount(self) -> None:
        self.source.border_title = "Source"
        self.out.border_title = "Console"
        lines = self.session.source_lines(self.session.path)
        self.source.load(self.session.path, lines)
        self._focus_order = [self.source, self.expr, self.stack, self.watch, self.out]
        self.session.start()
        self.set_interval(0.03, self._tick)
        self.source.focus()
        self._set_status("Running…")

    # -- handy lookups ---------------------------------------------------
    @property
    def source(self) -> SourceView:
        return self.query_one("#source", SourceView)

    @property
    def expr(self) -> InfoPanel:
        return self.query_one("#expr", InfoPanel)

    @property
    def watch(self) -> InfoPanel:
        return self.query_one("#watch", InfoPanel)

    @property
    def stack(self) -> InfoPanel:
        return self.query_one("#stack", InfoPanel)

    @property
    def out(self) -> RichLog:
        return self.query_one("#console", RichLog)

    @property
    def cmd(self) -> Input:
        return self.query_one("#cmd", Input)

    # -- the poll loop ---------------------------------------------------
    def _tick(self) -> None:
        output = self.session.drain_output()
        if output:
            self.out.write(output.rstrip("\n"))

        state = self.session.poll_state()
        if state is not None:
            self._apply_state(state)

        if self.running and self.session.wants_input() and self._input_mode is None:
            self._open_input("stdin", "stdin> ", "")

    def _apply_state(self, state: PauseState) -> None:
        if state.event != "line":
            self.running = False
            self.state = None
            self.source.set_current(None)
            if state.message:
                self.out.write(state.message)
            self._set_status(state.message.splitlines()[0] if state.message else "Finished")
            return

        self.state = state
        self.frame_index = 0
        self._inline_step = 0
        self.source.clear_inline()
        if state.file != self.source.file:
            self.source.load(state.file, self.session.source_lines(state.file))
        self.source.set_breakpoints(self.session.breakpoints.lines_for(state.file))
        self.source.set_current(state.line)
        self._set_next_eval()
        self._render_panels()
        self._set_status("Paused")

    def _render_expr(self) -> None:
        """Render the expression panel, pointing ▶ at the step ``e`` resolves
        next. Tracking shows only on the executing line (frame 0, same file),
        matching the source view's next-eval marker."""
        state = self.state
        if state is None:
            return
        on_exec_line = self.frame_index == 0 and self.source.file == state.file
        self.expr.set_content(
            format_expr(
                state.line_text,
                state.expr_steps,
                state.line_result,
                state.ran_line_text,
                state.ran_steps,
                active=self._inline_step if on_exec_line else -1,
            )
        )

    def _render_panels(self) -> None:
        state = self.state
        if state is None:
            return
        frame = state.stack[self.frame_index] if state.stack else None
        self._render_expr()
        self.watch.set_content(format_watches(state.watches))
        stack_text = format_stack(state.stack, self.frame_index)
        stack_text.append("\nlocals:\n", style="bold")
        stack_text.append(format_locals(frame.locals if frame else [], frame.changed if frame else frozenset()))
        self.stack.set_content(stack_text)

    def _set_next_eval(self) -> None:
        """Mark what ``e`` resolves first; only on the executing frame's line."""
        state = self.state
        if (state is not None and self.frame_index == 0 and state.expr_steps
                and self.source.file == state.file):
            first = state.expr_steps[0]
            self.source.set_next_eval((first.col, first.end_col))
        else:
            self.source.set_next_eval(None)

    def _set_status(self, mode: str) -> None:
        if self.state is not None:
            loc = f"{self.state.file.rsplit('/', 1)[-1]}:{self.state.line} {self.state.func}()"
        else:
            loc = self.session.path.rsplit("/", 1)[-1]
        self.query_one("#status", Static).update(
            f" {loc}   [{mode}]   s:into n:over r:out c:cont b:bp  :cmd  ?:help q:quit"
        )

    # -- key handling ----------------------------------------------------
    def on_key(self, event: events.Key) -> None:
        if self._input_mode is not None:
            if event.key == "escape":
                self._close_input()
                event.stop()
            return  # let the Input widget handle everything else

        token = event.character if (event.character and event.character.isprintable()
                                    and not event.character.isspace()) else event.key

        if self._pending == keys.PREFIX_GG:
            self._pending = None
            if token == "g":
                self.source.goto(1)
                event.stop()
            return
        if self._pending == keys.PREFIX_WINDOW:
            self._pending = None
            if token in ("h", "k"):
                self._cycle_focus(-1)
            elif token in ("l", "j"):
                self._cycle_focus(1)
            event.stop()
            return

        if token == keys.PREFIX_GG:
            self._pending = keys.PREFIX_GG
            event.stop()
            return
        if event.key == keys.PREFIX_WINDOW:
            self._pending = keys.PREFIX_WINDOW
            event.stop()
            return
        if token == keys.ENTER_COMMAND:
            self._open_input("command", ":", "")
            event.stop()
            return
        if token == keys.ENTER_SEARCH:
            self._open_input("search", "/", "")
            event.stop()
            return

        action = keys.DEBUG_KEYS.get(token) or keys.NAV_KEYS.get(token) or keys.NAV_KEYS.get(event.key)
        if action and hasattr(self, f"_act_{action}"):
            getattr(self, f"_act_{action}")()
            event.stop()

    # -- actions: debug control -----------------------------------------
    def _act_step_into(self) -> None:
        self.session.step_into()

    def _act_step_over(self) -> None:
        self.session.step_over()

    def _act_step_out(self) -> None:
        self.session.step_out()

    def _act_continue_(self) -> None:
        self.session.cont()

    def _act_quit(self) -> None:
        self.session.quit()
        self.exit()

    def _act_toggle_breakpoint(self) -> None:
        if not self.source.file:
            return
        added = self.session.toggle_breakpoint(self.source.file, self.source.cursor_line)
        self.source.set_breakpoints(self.session.breakpoints.lines_for(self.source.file))
        verb = "set" if added else "cleared"
        self.out.write(f"breakpoint {verb} at {self.source.file.rsplit('/', 1)[-1]}:{self.source.cursor_line}")

    def _act_print_expr(self) -> None:
        self._open_input("command", ":", "print ")

    def _act_pin_expr(self) -> None:
        self._open_input("command", ":", "display ")

    def _act_eval_inline(self) -> None:
        """Resolve the current line in-line, one sub-expression per press.

        One press past the last step clears the overlay back to the raw line.
        """
        if not (self.running and self.state is not None and self.state.event == "line"):
            return
        steps = self.state.expr_steps
        if not steps:
            self.out.write("nothing to resolve in-line on this line")
            return
        self._inline_step = 0 if self._inline_step >= len(steps) else self._inline_step + 1
        self._update_inline()

    def _update_inline(self) -> None:
        state = self.state
        self._render_expr()  # advance the panel's ▶ pointer in step with the overlay
        if self._inline_step == 0 or state is None or self.source.file != state.file:
            self.source.clear_inline()
            if state is not None:
                self._set_status("Paused")
            return
        text, highlight, kind, done = substitute_inline(state.line_text, state.expr_steps, self._inline_step)
        self.source.set_inline(state.line, text, highlight, kind, done)
        self._set_status(f"inline {self._inline_step}/{len(state.expr_steps)}")

    def _act_frame_up(self) -> None:
        if self.state and self.frame_index < len(self.state.stack) - 1:
            self.frame_index += 1
            self._goto_frame()

    def _act_frame_down(self) -> None:
        if self.state and self.frame_index > 0:
            self.frame_index -= 1
            self._goto_frame()

    def _goto_frame(self) -> None:
        self._inline_step = 0
        self.source.clear_inline()
        frame = self.state.stack[self.frame_index]
        if frame.file != self.source.file:
            self.source.load(frame.file, self.session.source_lines(frame.file))
            self.source.set_breakpoints(self.session.breakpoints.lines_for(frame.file))
        self.source.goto(frame.line)
        self._set_next_eval()
        self._render_panels()

    def _act_help(self) -> None:
        self.out.write(keys.HELP_TEXT)

    # -- actions: navigation --------------------------------------------
    def _act_cursor_down(self) -> None:
        if self.focused is self.source:
            self.source.move_cursor(1)
        else:
            self._scroll_focused(1)

    def _act_cursor_up(self) -> None:
        if self.focused is self.source:
            self.source.move_cursor(-1)
        else:
            self._scroll_focused(-1)

    def _act_collapse(self) -> None:
        self._scroll_focused_x(-2)

    def _act_expand(self) -> None:
        self._scroll_focused_x(2)

    def _act_goto_bottom(self) -> None:
        if self.focused is self.source:
            self.source.goto(len(self.source.lines))

    def _act_half_down(self) -> None:
        self._scroll_focused(self._half_page())

    def _act_half_up(self) -> None:
        self._scroll_focused(-self._half_page())

    def _act_search_next(self) -> None:
        self._step_match(1)

    def _act_search_prev(self) -> None:
        self._step_match(-1)

    def _half_page(self) -> int:
        return max(1, (self.focused.size.height if self.focused else 10) // 2)

    def _scroll_focused(self, delta: int) -> None:
        target = self.focused
        if target is self.source:
            self.source.move_cursor(delta)
        elif hasattr(target, "scroll_relative"):
            target.scroll_relative(y=delta, animate=False)

    def _scroll_focused_x(self, delta: int) -> None:
        if self.focused and hasattr(self.focused, "scroll_relative"):
            self.focused.scroll_relative(x=delta, animate=False)

    def _cycle_focus(self, direction: int) -> None:
        order = self._focus_order
        try:
            idx = order.index(self.focused)
        except ValueError:
            idx = 0
        order[(idx + direction) % len(order)].focus()

    # -- search ----------------------------------------------------------
    def _do_search(self, term: str) -> None:
        self._search_term = term
        self._matches = [i + 1 for i, line in enumerate(self.source.lines) if term in line]
        if not self._matches:
            self.out.write(f"no match for {term!r}")
            return
        after = [m for m in self._matches if m > self.source.cursor_line]
        self._match_idx = self._matches.index(after[0]) if after else 0
        self.source.goto(self._matches[self._match_idx])

    def _step_match(self, direction: int) -> None:
        if not self._matches:
            return
        self._match_idx = (self._match_idx + direction) % len(self._matches)
        self.source.goto(self._matches[self._match_idx])

    # -- command bar -----------------------------------------------------
    def _open_input(self, mode: str, prefix: str, initial: str) -> None:
        self._input_mode = mode
        cmd = self.cmd
        cmd.styles.display = "block"
        cmd.placeholder = {"command": "pdb/gdb command", "search": "search text", "stdin": "program input"}.get(mode, "")
        cmd.value = initial
        cmd.focus()
        # Stash the visual prefix as the border title so the user sees the mode.
        cmd.border_title = prefix

    def _close_input(self) -> None:
        if self._input_mode == "stdin":
            # Cancelling stdin feeds an empty line so the program isn't wedged.
            self.session.provide_input("")
        self._input_mode = None
        self.cmd.value = ""
        self.cmd.styles.display = "none"
        self.source.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        mode = self._input_mode
        text = event.value
        if mode == "stdin":
            self.session.provide_input(text)
            self.out.write(f"<input> {text}")
            self._input_mode = None
            self.cmd.value = ""
            self.cmd.styles.display = "none"
            self.source.focus()
            return
        if mode == "search":
            self._input_mode = None
            self.cmd.value = ""
            self.cmd.styles.display = "none"
            self.source.focus()
            if text:
                self._do_search(text)
            return
        self._input_mode = None
        self.cmd.value = ""
        self.cmd.styles.display = "none"
        self.source.focus()
        if text.strip():
            self._run_command(text.strip())

    def _run_command(self, text: str) -> None:
        parts = text.split()
        cmd, args = parts[0], parts[1:]
        file = self.source.file

        try:
            if cmd in ("break", "b"):
                line = int(args[0])
                cond = text.split(" if ", 1)[1].strip() if " if " in text else None
                self.session.breakpoints.set(file, line, cond)
                self.source.set_breakpoints(self.session.breakpoints.lines_for(file))
                self.out.write(f"breakpoint at {file.rsplit('/', 1)[-1]}:{line}" + (f" if {cond}" if cond else ""))
            elif cmd in ("clear", "cl"):
                line = int(args[0])
                self.session.breakpoints.clear(file, line)
                self.source.set_breakpoints(self.session.breakpoints.lines_for(file))
                self.out.write(f"cleared breakpoint at {line}")
            elif cmd in ("until", "unt", "u"):
                line = int(args[0]) if args else self.source.cursor_line
                self.session.run_to(file, line)
            elif cmd in ("display", "watch"):
                expr = text.split(None, 1)[1]
                self.session.add_watch(expr)
                if self.state:
                    self._render_panels()
                self.out.write(f"watching {expr}")
            elif cmd in ("undisplay", "unwatch"):
                expr = text.split(None, 1)[1]
                self.session.remove_watch(expr)
                if self.state:
                    self._render_panels()
            elif cmd in ("print", "p", "eval"):
                expr = text.split(None, 1)[1]
                self.out.write(f"{expr} = {self.session.evaluate(expr)}")
            elif cmd in ("where", "bt", "backtrace"):
                self.stack.focus()
            elif cmd in ("run", "restart"):
                self._restart()
            elif cmd == "open":
                path = args[0]
                self.source.load(path, self.session.source_lines(path))
                self.source.set_breakpoints(self.session.breakpoints.lines_for(path))
            elif cmd in ("q", "quit"):
                self._act_quit()
            else:
                self.out.write(f"unknown command: {cmd}")
        except (IndexError, ValueError) as exc:
            self.out.write(f"bad command: {text}  ({exc})")

    def _restart(self) -> None:
        old = self.session
        if self.running:
            old.terminate()  # unwind the in-flight run before launching a fresh one
        self.session = DebugSession(old.path, old.argv[1:], instrument=old.instrument)
        # Carry breakpoints and watches across the restart.
        with old.breakpoints._lock, self.session.breakpoints._lock:
            self.session.breakpoints._points = dict(old.breakpoints._points)
        self.session.watches._exprs = list(old.watches._exprs)
        self.state = None
        self.running = True
        self.frame_index = 0
        self._inline_step = 0
        self.source.clear_inline()
        self.out.write("--- restart ---")
        self.session.start()
        self._set_status("Running…")

"""DebugSession: owns the debuggee thread and assembles pauses.

Public surface used by the UI:

    start()                         launch the program (stops at the first line)
    poll_state() -> PauseState|None pending pause, if any (non-blocking)
    step_into/over/out/cont()       resume commands (only while paused)
    run_to(file, line)              continue until a line (one-shot)
    toggle_breakpoint / clear       via .breakpoints
    add_watch / remove_watch        via .watches
    evaluate(expr) -> str           one-off eval in the current frame
    drain_output() -> str           captured stdout/stderr since last drain
    wants_input() / provide_input() program stdin, fed from the UI
"""

from __future__ import annotations

import os
import threading
import traceback

from ..ipc import Bridge
from .breakpoints import Breakpoints, normalize
from .commands import Command
from .exprstep import RECORDER_NAME, instrument_source, resolve_line, resolve_line_result
from .state import FrameSnapshot, PauseState, WatchValue, describe
from .tracer import DebuggeeExit, Tracer
from .venv import find_venv, site_packages
from .watches import Watches


class _StreamProxy:
    """Routes the debuggee's stdout/stderr into the session's output buffer."""

    def __init__(self, session: "DebugSession") -> None:
        self._session = session

    def write(self, text: str) -> int:
        self._session._emit_output(text)
        return len(text)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


class _StdinProxy:
    """Blocks the debuggee on input() until the UI supplies a line."""

    def __init__(self, session: "DebugSession") -> None:
        self._session = session

    def readline(self, *_args) -> str:
        return self._session._request_input()

    def read(self, *_args) -> str:
        return self._session._request_input()

    def isatty(self) -> bool:
        return False


class DebugSession:
    def __init__(
        self,
        path: str,
        argv: list[str] | None = None,
        instrument: bool = True,
    ) -> None:
        self.path = os.path.abspath(path)
        self.argv = [self.path] + list(argv or [])
        self.instrument = instrument
        self.venv_root = find_venv(self.path)

        self.breakpoints = Breakpoints()
        self.watches = Watches()
        self.bridge = Bridge()
        self._recorder = None         # set in _run when instrumentation is enabled

        package_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._tracer = Tracer(self._on_pause, self.breakpoints, package_dir)

        self._frame = None            # current frame while paused (UI evals against it)
        self._paused = False
        self._finished = False
        self._source_cache: dict[str, list[str]] = {}
        self._thread: threading.Thread | None = None
        self._terminate = threading.Event()  # set by terminate() to unwind the run

        # For the post-run collapse: the previous pause location and the command
        # that resumed from it, so a step-over can resolve the line it just ran.
        self._prev_pause: tuple[str, int] | None = None
        self._last_resume: Command | None = None

        # Rendered values from the previous pause, to flag what changed.
        self._prev_locals: dict[tuple[str, str], dict[str, str]] = {}
        self._prev_watch: dict[str, str] = {}

        # stdout/stderr capture
        self._out_lock = threading.Lock()
        self._out_buffer: list[str] = []

        # stdin handshake
        self._input_event = threading.Event()
        self._want_input = False
        self._input_line: str | None = None

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="nobug-debuggee", daemon=True)
        self._thread.start()

    def terminate(self, timeout: float = 2.0) -> None:
        """Unwind the running debuggee thread and wait for it to exit.

        Used by in-app restart. Wakes the thread whether it's paused at a line
        (via a QUIT command) or blocked on stdin (via the input event), so its
        ``finally`` in ``_run`` runs and restores sys.stdout/argv before a fresh
        session installs its own, which keeps each restart from leaking a thread.
        """
        if self._finished or self._thread is None:
            return
        self._terminate.set()
        self.bridge.send(Command.QUIT)   # wake a thread blocked at a pause
        self._input_event.set()          # wake a thread blocked on input()
        self._thread.join(timeout)

    def _run(self) -> None:
        import sys

        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                source = fh.read()
            code = compile(source, self.path, "exec")
        except Exception as exc:
            self.bridge.finish(PauseState(event="exception", message=f"Could not load {self.path}: {exc}"))
            return

        globs = {"__name__": "__main__", "__file__": self.path, "__builtins__": __builtins__}

        # Rewrite the AST so sub-expressions report their real values as the
        # program runs. If instrumentation trips over a construct we mishandle,
        # fall back to the original code rather than fail the run.
        if self.instrument:
            try:
                code, self._recorder = instrument_source(source, self.path)
                globs[RECORDER_NAME] = self._recorder
            except Exception as exc:
                self._recorder = None
                self._emit_output(f"[nobug] expression instrumentation disabled: {exc}\n")

        old_out, old_err, old_in = sys.stdout, sys.stderr, sys.stdin
        old_argv = sys.argv
        old_path = list(sys.path)
        sys.stdout = _StreamProxy(self)      # type: ignore[assignment]
        sys.stderr = _StreamProxy(self)      # type: ignore[assignment]
        sys.stdin = _StdinProxy(self)        # type: ignore[assignment]
        sys.argv = list(self.argv)
        self._activate_venv()
        sys.path.insert(0, os.path.dirname(os.path.abspath(self.path)))  # resolve sibling imports

        message = "Program finished."
        self._tracer.start()
        try:
            exec(code, globs)
        except DebuggeeExit:
            message = "Program terminated."
        except SystemExit as exc:
            message = f"Program exited (code {exc.code})."
        except BaseException:
            message = "Program raised an unhandled exception:\n" + traceback.format_exc()
        finally:
            self._tracer.stop()
            sys.stdout, sys.stderr, sys.stdin = old_out, old_err, old_in
            sys.argv = old_argv
            sys.path[:] = old_path
            self._finished = True
            self.bridge.finish(PauseState(event="finished", message=message))

    def _activate_venv(self) -> None:
        """Put the detected venv's site-packages ahead of ours on sys.path so the
        debuggee's imports resolve against it. Restored in _run's finally."""
        if self.venv_root is None:
            return
        import site
        import sys

        dirs = site_packages(self.venv_root)
        if not dirs:
            return
        for d in dirs:
            site.addsitedir(d)        # process .pth (editable installs, namespace pkgs)
            if d in sys.path:
                sys.path.remove(d)
            sys.path.insert(0, d)     # venv wins over our own environment

        self._emit_output(f"[nobug] using virtualenv: {self.venv_root}\n")
        running = f"python{sys.version_info.major}.{sys.version_info.minor}"
        versioned = [d for d in dirs if f"{os.sep}python" in d]  # posix lib/pythonX.Y layout
        if versioned and not any(f"{os.sep}{running}{os.sep}" in d for d in versioned):
            self._emit_output(
                f"[nobug] warning: this venv was not built for the running {running}; "
                "compiled packages may fail to import\n"
            )

    # -- pause assembly (runs on the debuggee thread) -------------------
    def _on_pause(self, frame, event: str) -> Command:
        self._frame = frame
        state = self._build_state(frame, event)
        self._paused = True
        command = self.bridge.pause(state)
        self._paused = False
        return command

    def _build_state(self, frame, event: str) -> PauseState:
        file = frame.f_code.co_filename
        line = frame.f_lineno
        line_text = self._source_line(file, line)

        stack: list[FrameSnapshot] = []
        f = frame
        while f is not None:
            locs = [(name, describe(value)) for name, value in f.f_locals.items()]
            snap = FrameSnapshot(
                func=f.f_code.co_name,
                file=f.f_code.co_filename,
                line=f.f_lineno,
                locals=locs,
            )
            if f is frame:
                snap.changed = self._changed_locals(snap)
            stack.append(snap)
            if f is self._tracer.botframe:
                break
            f = f.f_back

        watches = self.watches.evaluate(frame)
        self._mark_watch_changes(watches)

        ran_line_text, ran_steps = self._ran_collapse()
        self._prev_pause = (file, line)

        return PauseState(
            event=event,
            file=file,
            line=line,
            func=frame.f_code.co_name,
            line_text=line_text,
            stack=stack,
            watches=watches,
            expr_steps=self._expr_steps(line_text, line, frame),
            line_result=resolve_line_result(line_text, frame) if line_text else None,
            ran_line_text=ran_line_text,
            ran_steps=ran_steps,
        )

    def _changed_locals(self, snap: FrameSnapshot) -> set[str]:
        """Names in *snap* whose rendered value differs from this frame's last
        pause. Keyed per (file, func) so returning to a function compares against
        the last time we were in it; the first visit highlights nothing.
        """
        key = (snap.file, snap.func)
        current = {name: vr.text for name, vr in snap.locals}
        previous = self._prev_locals.get(key)
        self._prev_locals[key] = current
        if previous is None:
            return set()
        return {name for name, text in current.items() if previous.get(name) != text}

    def _mark_watch_changes(self, watches: list[WatchValue]) -> None:
        for w in watches:
            text = w.value.text if w.value is not None else (w.error or "")
            if w.expr in self._prev_watch and self._prev_watch[w.expr] != text:
                w.changed = True
            self._prev_watch[w.expr] = text

    def _ran_collapse(self):
        """The line a step-over just executed, resolved with real values.

        The recorder captures values while a line runs, so right after stepping
        over a line its entries are current. We surface the full collapse only
        when that line held a call (or some other expression the live prediction
        can't show); otherwise the predictions already covered it.
        """
        if (
            self._last_resume != Command.STEP_OVER
            or self._recorder is None
            or self._prev_pause is None
            or self._prev_pause[0] != self.path
        ):
            return "", []
        ran_steps = self._recorder.steps_for_line(self._prev_pause[1])
        if not any(s.opaque for s in ran_steps):
            return "", []
        return self._source_line(*self._prev_pause), ran_steps

    def _expr_steps(self, line_text: str, line: int, frame):
        """Sub-expressions of the line about to run, evaluated live.

        We pause before a line runs, so the recorder's captures for this line
        are stale: they come from its previous execution (a prior loop
        iteration). Only the live prediction reflects the line about to run, so
        the per-step panel uses it alone. Recorded call results, which exist
        only after the line runs, surface in the post-execution view instead.
        """
        if not line_text:
            return []
        return resolve_line(line_text, frame)

    def _source_line(self, file: str, line: int) -> str:
        lines = self.source_lines(file)
        if 1 <= line <= len(lines):
            return lines[line - 1].rstrip("\n")
        return ""

    def source_lines(self, file: str) -> list[str]:
        key = normalize(file)
        if key not in self._source_cache:
            try:
                with open(file, "r", encoding="utf-8") as fh:
                    self._source_cache[key] = fh.readlines()
            except Exception:
                self._source_cache[key] = []
        return self._source_cache[key]

    # -- resume commands (UI thread) ------------------------------------
    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def finished(self) -> bool:
        return self._finished

    def poll_state(self) -> PauseState | None:
        return self.bridge.poll()

    def step_into(self) -> None:
        if self._paused:
            self._last_resume = Command.STEP_INTO
            self.bridge.send(Command.STEP_INTO)

    def step_over(self) -> None:
        if self._paused:
            self._last_resume = Command.STEP_OVER
            self.bridge.send(Command.STEP_OVER)

    def step_out(self) -> None:
        if self._paused:
            self._last_resume = Command.STEP_OUT
            self.bridge.send(Command.STEP_OUT)

    def cont(self) -> None:
        if self._paused:
            self._last_resume = Command.CONTINUE
            self.bridge.send(Command.CONTINUE)

    def run_to(self, file: str, line: int) -> None:
        if self._paused:
            self._last_resume = Command.CONTINUE
            self._tracer.temp_stop = (normalize(file), line)
            self.bridge.send(Command.CONTINUE)

    def quit(self) -> None:
        if self._paused:
            self.bridge.send(Command.QUIT)

    # -- breakpoints / watches (thin pass-throughs) ---------------------
    def toggle_breakpoint(self, file: str, line: int, condition: str | None = None) -> bool:
        return self.breakpoints.toggle(file, line, condition)

    def add_watch(self, expr: str) -> None:
        self.watches.add(expr)

    def remove_watch(self, expr: str) -> None:
        self.watches.remove(expr)

    def evaluate(self, expr: str) -> str:
        """One-off eval against the current frame (used by :print). UI thread."""
        if self._frame is None:
            return "<no frame>"
        try:
            value = eval(expr, self._frame.f_globals, self._frame.f_locals)
        except Exception as exc:
            return f"{exc.__class__.__name__}: {exc}"
        return describe(value).text

    # -- stdout/stderr capture ------------------------------------------
    def _emit_output(self, text: str) -> None:
        with self._out_lock:
            self._out_buffer.append(text)

    def drain_output(self) -> str:
        with self._out_lock:
            if not self._out_buffer:
                return ""
            text = "".join(self._out_buffer)
            self._out_buffer.clear()
        return text

    # -- stdin handshake -------------------------------------------------
    def _request_input(self) -> str:
        self._input_line = None
        self._input_event.clear()
        self._want_input = True
        self._input_event.wait()
        self._want_input = False
        if self._terminate.is_set():
            raise DebuggeeExit
        line = self._input_line if self._input_line is not None else ""
        self._input_line = None
        return line

    def wants_input(self) -> bool:
        return self._want_input

    def provide_input(self, text: str) -> None:
        self._input_line = text if text.endswith("\n") else text + "\n"
        self._input_event.set()

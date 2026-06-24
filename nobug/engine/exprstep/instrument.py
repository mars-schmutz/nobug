"""Expression resolution via AST instrumentation.

Rewrites the program so every interesting sub-expression reports its real value
as the line executes. This is how we capture function-call results, which
evaluating side-effect-free fragments against a paused frame can't reach.

Each chosen node ``N`` is replaced by ``__nobug_record__(N, id)``, a call that
stores the value and returns it unchanged. Passing the value straight through
preserves evaluation order, ``and``/``or`` short-circuiting, and exceptions: a
fragment that never runs simply never records. Captures are keyed by source
span, so the resolver turns them into :class:`~nobug.engine.state.ExprStep`s.

Some constructs are recorded only at their top level and never descended into,
because wrapping inside them would change meaning or fail to compile:

* comprehension/generator/lambda bodies have their own scopes
* f-string internals would break format-spec quoting
* a ``Call`` is not a valid ``match`` pattern

Assignment and deletion targets are never wrapped. If a construct still breaks
compilation, that's the caller's problem to catch; it falls back to the
original, uninstrumented code.
"""

from __future__ import annotations

import ast
import inspect

from ..state import ExprStep, short_repr

# The name the recorder is injected under in the debuggee's globals.
RECORDER_NAME = "__nobug_record__"

# (lineno, col_offset, end_col_offset) into the original source.
Span = tuple[int, int, int]

# Node types the live predictor (cheap.resolve_line) can't show, like calls and
# other opaque expressions. A recorded value for one of these is new
# information the prediction couldn't have given.
_OPAQUE_TYPES = (
    ast.Call,
    ast.IfExp,
    ast.ListComp,
    ast.SetComp,
    ast.DictComp,
    ast.GeneratorExp,
    ast.JoinedStr,
)


class _Instrumentor(ast.NodeTransformer):
    """Wraps interesting Load-context nodes in a recorder call.

    Builds ``self.spans``: ``node_id -> (Span, source_text)``.
    """

    def __init__(self, source_lines: list[str]) -> None:
        self._lines = source_lines
        self.spans: dict[int, tuple[Span, str, bool]] = {}
        self._next_id = 0

    # -- helpers ---------------------------------------------------------
    def _slice(self, node: ast.AST) -> str:
        """Exact source text for *node*, or "" if it spans multiple lines."""
        lineno = getattr(node, "lineno", None)
        end_lineno = getattr(node, "end_lineno", None)
        col = getattr(node, "col_offset", None)
        end = getattr(node, "end_col_offset", None)
        if None in (lineno, end_lineno, col, end) or lineno != end_lineno:
            return ""
        if not (1 <= lineno <= len(self._lines)):
            return ""
        return self._lines[lineno - 1][col:end]

    def _wrap(self, node: ast.AST) -> ast.AST:
        """Replace *node* with ``__nobug_record__(node, node_id)``."""
        lineno = getattr(node, "lineno", None)
        col = getattr(node, "col_offset", None)
        end = getattr(node, "end_col_offset", None)
        if None in (lineno, col, end):
            return node

        node_id = self._next_id
        self._next_id += 1
        self.spans[node_id] = ((lineno, col, end), self._slice(node), isinstance(node, _OPAQUE_TYPES))

        call = ast.Call(
            func=ast.Name(id=RECORDER_NAME, ctx=ast.Load()),
            args=[node, ast.Constant(value=node_id)],
            keywords=[],
        )
        # Keep line numbers in the instrumented code matching the original.
        ast.copy_location(call, node)
        ast.fix_missing_locations(call)
        return call

    def _maybe_wrap(self, node: ast.AST) -> ast.AST:
        """generic_visit *node*, then wrap it if it loads a value."""
        self.generic_visit(node)
        ctx = getattr(node, "ctx", None)
        if ctx is not None and not isinstance(ctx, ast.Load):
            return node  # a Store/Del target; its Load children were handled
        return self._wrap(node)

    # -- nodes we instrument --------------------------------------------
    visit_Name = _maybe_wrap
    visit_Attribute = _maybe_wrap
    visit_Subscript = _maybe_wrap
    visit_BinOp = _maybe_wrap
    visit_UnaryOp = _maybe_wrap
    visit_BoolOp = _maybe_wrap
    visit_Compare = _maybe_wrap
    visit_IfExp = _maybe_wrap
    visit_Call = _maybe_wrap

    # -- constructs we record but don't descend into --------------------
    def _record_opaque(self, node: ast.AST) -> ast.AST:
        return self._wrap(node)

    visit_ListComp = _record_opaque
    visit_SetComp = _record_opaque
    visit_DictComp = _record_opaque
    visit_GeneratorExp = _record_opaque
    visit_JoinedStr = _record_opaque

    def visit_Lambda(self, node: ast.Lambda) -> ast.AST:
        return node  # a function object: noise, and a separate scope

    if hasattr(ast, "match_case"):  # Python 3.10+

        def visit_match_case(self, node: "ast.match_case") -> ast.AST:
            # A Call is not a valid pattern; instrument only the guard and body.
            if node.guard is not None:
                node.guard = self.visit(node.guard)
            node.body = [self.visit(stmt) for stmt in node.body]
            return node


class Recorder:
    """Holds the latest recorded value per node id, and renders ExprSteps.

    Injected into the debuggee's globals under :data:`RECORDER_NAME`.
    ``__call__`` does no ``repr`` or iteration, so it stays cheap and can't
    re-enter the tracer through a user ``__repr__``. Values are rendered lazily,
    at pause time, under the tracer's re-entrancy guard.
    """

    def __init__(self, spans: dict[int, tuple[Span, str, bool]]) -> None:
        self._spans = spans
        self._values: dict[int, object] = {}

    def __call__(self, value: object, node_id: int) -> object:
        # Latest write wins, i.e. the most recent execution of this span.
        self._values[node_id] = value
        return value

    def steps_for_line(self, lineno: int) -> list[ExprStep]:
        """ExprSteps for every recorded span on *lineno*, innermost first."""
        out: list[ExprStep] = []
        seen: set[tuple[int, int]] = set()
        for node_id, ((ln, col, end), text, is_opaque) in self._spans.items():
            if ln != lineno or node_id not in self._values:
                continue
            if (col, end) in seen:
                continue
            value = self._values[node_id]
            if callable(value) or inspect.ismodule(value):
                continue  # functions/classes/modules are noise
            seen.add((col, end))
            out.append(
                ExprStep(
                    text=text,
                    value_text=short_repr(value),
                    type_name=type(value).__name__,
                    col=col,
                    end_col=end,
                    opaque=is_opaque,
                )
            )
        out.sort(key=lambda s: (s.end_col - s.col, s.col))
        return out


def instrument_source(source: str, filename: str) -> tuple[object, Recorder]:
    """Parse, instrument, and compile *source*.

    Returns ``(code_object, recorder)``; the recorder must be injected into the
    execution globals under :data:`RECORDER_NAME` before the code runs. Raises
    on malformed input, like ``ast.parse``/``compile`` do, so callers should
    catch and fall back to the original source.
    """
    transformer = _Instrumentor(source.splitlines())
    new_tree = transformer.visit(ast.parse(source, filename=filename, mode="exec"))
    ast.fix_missing_locations(new_tree)
    code = compile(new_tree, filename, "exec")
    return code, Recorder(transformer.spans)

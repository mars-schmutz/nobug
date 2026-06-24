"""Cheap expression resolution: evaluate side-effect-free fragments.

Parse the current source line, find every side-effect-free sub-expression, and
evaluate it against the live frame. This shows how the pieces of the line
resolve with the current state, before the line executes, which is the point a
step debugger pauses at. It won't run anything that could have side effects (no
awaits, yields, or walrus assignments), so inspecting never changes the program.
The one exception: calls to the whitelisted pure builtins in
:data:`_PURE_BUILTIN_NAMES` are run, so a line like ``str(x / y)`` can finish
resolving (see README "Pure-builtin previews").
"""

from __future__ import annotations

import ast
import builtins
import inspect

from ..state import ExprStep, short_repr

# Node types worth explaining. A general call is absent: we show its arguments
# (their own nodes), not the call itself. A whitelisted pure-builtin call counts
# as interesting too (see _is_pure_call) so it can resolve.
_INTERESTING = (
    ast.Name,
    ast.Attribute,
    ast.Subscript,
    ast.BinOp,
    ast.UnaryOp,
    ast.BoolOp,
    ast.Compare,
)

# Node types that always make a fragment unsafe to evaluate: they suspend,
# yield, or bind, so re-running one to preview the line could change behaviour.
# Calls are judged separately (a pure-builtin call is allowed; any other isn't).
_UNSAFE = (ast.Await, ast.Yield, ast.YieldFrom, ast.NamedExpr)

# Builtins cheap eval may call when previewing a line. Each computes a value
# from its arguments without mutating them and takes no callback, so running one
# to show its result is normally free of side effects.
#
# Two caveats, also in the README so an override doesn't surprise anyone:
#   * Shadowing one of these names (``str = my_func``) disables its preview. The
#     identity check in _is_pure_call fails, so cheap eval skips the call rather
#     than run the override.
#   * A value's own ``__str__``/``__repr__``/``__len__``/``__format__``/... is
#     user code that these builtins still invoke, so a side-effecting dunder will
#     run during a preview.
_PURE_BUILTIN_NAMES = frozenset({
    "abs", "ascii", "bin", "bool", "chr", "float", "format",
    "hex", "int", "len", "oct", "ord", "repr", "round", "str",
})
_PURE_BUILTINS = tuple(
    getattr(builtins, name) for name in _PURE_BUILTIN_NAMES if hasattr(builtins, name)
)


def _is_pure_call(node: ast.Call, frame) -> bool:
    """True if *node* calls a whitelisted pure builtin, checked by identity.

    Only a bare ``Name`` callee qualifies: resolving it is a plain namespace
    lookup with no side effects, whereas an attribute callee would trigger
    ``__getattribute__``. The match uses ``is`` rather than ``==``/``in``, so
    testing a shadowed name can't run a user ``__eq__``/``__hash__``.
    """
    if not isinstance(node.func, ast.Name):
        return False
    try:
        code = compile(ast.Expression(body=node.func), "<nobug-expr>", "eval")
        func = eval(code, frame.f_globals, frame.f_locals)
    except Exception:
        return False
    return any(func is b for b in _PURE_BUILTINS)


def _has_side_effects(node: ast.AST, frame) -> bool:
    for child in ast.walk(node):
        if isinstance(child, _UNSAFE):
            return True
        if isinstance(child, ast.Call) and not _is_pure_call(child, frame):
            return True
    return False


# A sentinel distinct from any real value, so callers can tell "evaluated to
# None" apart from "could not evaluate / not worth showing".
_UNRESOLVED = object()


def _safe_eval(node: ast.expr, frame) -> object:
    """Evaluate *node* against *frame*, or return ``_UNRESOLVED``.

    Returns ``_UNRESOLVED`` if evaluation raises (undefined name, bad index,
    ...) or if the result is a function/class/builtin/module. Those are noise
    in the teaching panel rather than a resolved step.
    """
    try:
        code = compile(ast.Expression(body=node), "<nobug-expr>", "eval")
        value = eval(code, frame.f_globals, frame.f_locals)
    except Exception:
        return _UNRESOLVED
    if callable(value) or inspect.ismodule(value):
        return _UNRESOLVED
    return value


def resolve_line(source_line: str, frame) -> list[ExprStep]:
    """Return the resolvable sub-expressions of *source_line* in *frame*."""
    stripped = source_line.lstrip()
    if not stripped or stripped.startswith("#"):
        return []
    indent = len(source_line) - len(stripped)

    try:
        tree = ast.parse(stripped.rstrip(), mode="exec")
    except SyntaxError:
        # A continuation of a multi-line statement won't parse alone, so skip it.
        return []

    steps: list[ExprStep] = []
    seen_spans = set()
    for node in ast.walk(tree):
        if not isinstance(node, _INTERESTING) and not (
            isinstance(node, ast.Call) and _is_pure_call(node, frame)
        ):
            continue
        # Don't try to read assignment / deletion targets.
        if isinstance(node, (ast.Name, ast.Attribute, ast.Subscript)) and not isinstance(
            node.ctx, ast.Load
        ):
            continue
        if _has_side_effects(node, frame):
            continue

        col = getattr(node, "col_offset", None)
        end = getattr(node, "end_col_offset", None)
        if col is None or end is None:
            continue
        span = (col, end)
        if span in seen_spans:
            continue

        # Undefined name, index error, or a noise value (func/module) → skip.
        value = _safe_eval(node, frame)
        if value is _UNRESOLVED:
            continue

        seen_spans.add(span)
        steps.append(
            ExprStep(
                text=stripped[col:end],
                value_text=short_repr(value),
                type_name=type(value).__name__,
                col=col + indent,
                end_col=end + indent,
            )
        )

    # Left-to-right, operands before their result, matching Python's own
    # evaluation order. End column sorts post-order, width breaks ties (inner
    # first).
    steps.sort(key=lambda s: (s.end_col, s.end_col - s.col))
    return steps


# Target node types we can name in the result line. The text is sliced from the
# source, so a subscript/attribute target ("d[k]", "obj.x") reads correctly too.
_RESULT_TARGETS = (ast.Name, ast.Attribute, ast.Subscript)


def resolve_line_result(source_line: str, frame) -> ExprStep | None:
    """The predicted result of an assignment line, as a single ``ExprStep``.

    For ``target = <expr>`` (and the annotated/augmented forms), this evaluates
    the right-hand side against the live frame and returns what ``target`` will
    become once the line runs: the final ``⇒ target = value`` step. Like
    ``resolve_line``, it only predicts side-effect-free right-hand sides, so it
    never executes calls and never changes the program. Returns ``None`` when
    the line isn't a single-target assignment with a resolvable value.
    """
    stripped = source_line.lstrip()
    if not stripped or stripped.startswith("#"):
        return None
    indent = len(source_line) - len(stripped)

    try:
        tree = ast.parse(stripped.rstrip(), mode="exec")
    except SyntaxError:
        return None
    if len(tree.body) != 1:
        return None
    stmt = tree.body[0]

    # Identify the target and the expression whose value the target receives.
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
        target, value_expr = stmt.targets[0], stmt.value
    elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
        target, value_expr = stmt.target, stmt.value
    elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.target, ast.Name):
        # "x += 1" resolves to "x + 1": read the target, then apply the op.
        target = stmt.target
        load = ast.copy_location(ast.Name(id=stmt.target.id, ctx=ast.Load()), stmt.target)
        value_expr = ast.fix_missing_locations(
            ast.copy_location(ast.BinOp(left=load, op=stmt.op, right=stmt.value), stmt)
        )
    else:
        return None

    if not isinstance(target, _RESULT_TARGETS) or _has_side_effects(value_expr, frame):
        return None

    value = _safe_eval(value_expr, frame)
    if value is _UNRESOLVED:
        return None

    col = getattr(target, "col_offset", None)
    end = getattr(target, "end_col_offset", None)
    if col is None or end is None:
        return None

    return ExprStep(
        text=stripped[col:end],
        value_text=short_repr(value),
        type_name=type(value).__name__,
        col=col + indent,
        end_col=end + indent,
    )

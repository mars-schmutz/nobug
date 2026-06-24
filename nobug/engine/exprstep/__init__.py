"""Expression-resolution strategies for the Thonny-style panel.

``cheap`` evaluates side-effect-free sub-expressions of the current line against
the live frame, before it runs. ``instrument`` AST-rewrites the target to
capture real sub-expression values *as the line executes*, including call
results. Both produce :class:`~nobug.engine.state.ExprStep` lists, so the panel
and the rest of the engine treat the two interchangeably.
"""

from .cheap import resolve_line, resolve_line_result
from .instrument import RECORDER_NAME, Recorder, instrument_source

__all__ = [
    "resolve_line",
    "resolve_line_result",
    "instrument_source",
    "Recorder",
    "RECORDER_NAME",
]

"""The debug engine: tracing, breakpoints, watches, and expression resolution.

Nothing in this package imports the UI. The engine speaks only in the
dataclasses from ``state.py`` and the ``Command`` enum from ``commands.py``, so
a different frontend could sit on top of it later.
"""

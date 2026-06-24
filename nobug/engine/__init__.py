"""The debug engine: tracing, breakpoints, watches, and expression resolution.

Nothing in this package imports the UI. The engine speaks only in the
dataclasses defined in ``state.py`` and the ``Command`` enum in ``commands.py``,
which keeps the door open to an alternative frontend later.
"""

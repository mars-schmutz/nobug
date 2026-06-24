"""Entry point: ``python -m nobug script.py [args...]``."""

from __future__ import annotations

import os
import sys

from .engine.session import DebugSession


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(
            "usage: python -m nobug [--no-instrument] SCRIPT [args...]\n"
            "  --no-instrument   skip AST instrumentation of sub-expressions\n"
            "                    (faster; disables real call-result capture)"
        )
        return 0 if argv else 1

    instrument = True
    if argv[0] == "--no-instrument":
        instrument = False
        argv = argv[1:]
    if not argv:
        print("nobug: no script given", file=sys.stderr)
        return 1

    script, script_args = argv[0], argv[1:]
    if not os.path.isfile(script):
        print(f"nobug: no such file: {script}", file=sys.stderr)
        return 1

    # Import the UI lazily so the engine can be used / tested without Textual.
    from .ui.app import NobugApp

    session = DebugSession(script, script_args, instrument=instrument)
    NobugApp(session).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Locate a virtual environment so the debuggee's imports resolve against it.

The debuggee runs in-process (``DebugSession`` execs it in this interpreter), so
"using" a venv means adding its ``site-packages`` to ``sys.path`` for the run
rather than switching interpreters. Switching would mean nobug itself had to be
installed in the target venv, on a possibly-incompatible Python.
"""

from __future__ import annotations

import glob
import os


def find_venv(script_path: str) -> str | None:
    """Return a venv root to use, or None to run with the plain interpreter.

    Priority: an activated venv (``$VIRTUAL_ENV``), then a ``.venv``/``venv``
    beside the script, then beside the current working directory.
    """
    active = os.environ.get("VIRTUAL_ENV")
    if active and _is_venv(active):
        return active

    seen: set[str] = set()
    for base in (os.path.dirname(os.path.abspath(script_path)), os.getcwd()):
        if base in seen:
            continue
        seen.add(base)
        for name in (".venv", "venv"):
            candidate = os.path.join(base, name)
            if _is_venv(candidate):
                return candidate
    return None


def site_packages(venv_root: str) -> list[str]:
    """The ``site-packages`` directories inside *venv_root* (posix + Windows)."""
    dirs = glob.glob(os.path.join(venv_root, "lib", "python*", "site-packages"))
    dirs += glob.glob(os.path.join(venv_root, "Lib", "site-packages"))
    return [d for d in dirs if os.path.isdir(d)]


def _is_venv(path: str) -> bool:
    return os.path.isfile(os.path.join(path, "pyvenv.cfg"))

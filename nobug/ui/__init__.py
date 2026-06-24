"""The Textual frontend.

The per-panel widgets aren't separate modules. Every panel except the source
view is the same scrollable, titled ``InfoPanel`` fed different rendered text,
so they share ``widgets.py`` rather than living in near-duplicate files. The
keymap lives in ``keys.py``.
"""

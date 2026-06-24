"""The Textual frontend.

Per PROJECT_PLAN §6, the per-panel widgets are intentionally *not* separate
modules: every panel except the source view is the same scrollable, titled
``InfoPanel`` fed different rendered text, so they live together in
``widgets.py`` rather than as near-duplicate files. The keymap is centralized
in ``keys.py`` as the single source of truth for bindings.
"""

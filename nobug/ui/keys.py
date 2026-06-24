"""The keymap — single source of truth for every binding.

Philosophy (PROJECT_PLAN §6): debugger shortcuts first, vim for navigation.
The debug-control keys come straight from pdb's single letters so the muscle
memory transfers to pdb/gdb; vim fills only the navigation gaps.

Each map is ``token -> action name``. A *token* is the printable character
(so case is preserved: ``G`` vs ``g``) or, for control keys, the Textual key
name (``ctrl+d``). The App turns action names into method calls, so rebinding
is just editing these dicts.
"""

from __future__ import annotations

# Debug control — pdb/gdb single-letter commands. These never collide with the
# vim motion keys below.
DEBUG_KEYS = {
    "s": "step_into",          # pdb s
    "n": "step_over",          # pdb n
    "r": "step_out",           # pdb r (gdb finish)
    "c": "continue_",          # pdb c
    "b": "toggle_breakpoint",  # pdb b — at the source cursor line
    "p": "print_expr",         # pdb p — opens ':print '
    "P": "pin_expr",           # opens ':display '
    "e": "eval_inline",        # step the current line's sub-exprs in-line (Thonny-style)
    "u": "frame_up",           # pdb u — toward the caller
    "d": "frame_down",         # pdb d — toward the callee
    "q": "quit",               # pdb q
    "?": "help",
}

# Navigation — vim, filling the gaps the debugger letters leave.
NAV_KEYS = {
    "j": "cursor_down",
    "k": "cursor_up",
    "h": "collapse",
    "l": "expand",
    "G": "goto_bottom",
    "ctrl+d": "half_down",
    "ctrl+u": "half_up",
    "ctrl+n": "search_next",   # 'n' is step-over, so search-repeat moves here
    "ctrl+p": "search_prev",
}

# Multi-key prefixes handled specially by the App.
PREFIX_GG = "g"        # gg -> goto_top
PREFIX_WINDOW = "ctrl+w"  # ctrl+w h/j/k/l -> move panel focus

# Mode-entry tokens.
ENTER_COMMAND = ":"
ENTER_SEARCH = "/"

HELP_TEXT = """\
nobug keys — debugger commands (pdb/gdb), then vim navigation

  DEBUG CONTROL
    s  step into       n  step over      r  step out (return)
    c  continue        b  toggle breakpoint at cursor line
    u  frame up        d  frame down
    p  print/eval expr P  pin (display) expr      q  quit
    e  resolve current line in-line, one value per press (Thonny-style)

  NAVIGATION (vim)
    j/k  down/up       h/l  collapse/expand   gg/G  top/bottom
    ctrl+d / ctrl+u    half page down/up
    ctrl+w h/j/k/l     move focus between panels   Tab  cycle focus
    /  search          ctrl+n / ctrl+p  next/prev match

  COMMAND BAR (:) — real pdb/gdb words
    :break N [if C]   :clear N     :until [N]    :display E
    :undisplay E      :print E     :where /:bt   :run /:restart
    :open PATH        :q
"""

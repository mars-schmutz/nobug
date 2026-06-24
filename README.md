# nobug

A terminal debugger for Python that mixes a little bit of pdb and Thonny.

It works like kind of like `pdb` where you set breakpoints and step through the code.
It also borrows the expression evaluation feature from Thonny, where you can see how
a line will evaluate (with some caveats).

The main purpose of this project is to help teach students Python basics as well as
help them become familiar with the terminal.

## Install

Dependencies:

- uv Python project manager
- Python 3.14 or newer

Once you have those installed you can clone the repo,
navigate into it and install the tool:

```
git clone <repo>
cd <repo>
uv tool install .
```

To install updates, just pull the changes and then reinstall with uv:

```
git pull
uv tool install . --reinstall
```

## UI

- **Source**: File to debug.
- **Expression**: the current line broken into its sub-expressions and their
  values (e.g. `subtotal + price → 100`), updated every step. Press `e` to
  collapse the line in place.
- **Watches**: pinned variables that re-evaluate each step.
- **Stack / Locals**: the call stack and the selected frame's variables.
- **Console**: the program's own stdout/stdin.

## Keys

nobug mostly uses pdb commands and uses vim commands for navigation
gaps. Press `?` in the app for the full list.

| Key                                      | Action                       |
| ---------------------------------------- | ---------------------------- |
| `s`                                      | step into                    |
| `n`                                      | step over                    |
| `r`                                      | step out                     |
| `c`                                      | continue                     |
| `b`                                      | breakpoint at cursor         |
| `u`/`d`                                  | stack frame up/down          |
| `p` / `P`                                | print / pin expression       |
| `e`                                      | resolve current line in-line |
| `j`/`k`                                  | move cursor down/up          |
| `gg`/`G`                                 | top / bottom                 |
| `ctrl+d`/`ctrl+u`                        | half-page                    |
| `ctrl+w h/j/k/l`, `TAB`, or mouse clicks | move panel focus             |
| `/` then `ctrl+n`/`ctrl+p`               | search                       |
| `q`                                      | quit                         |
| `:`                                      | command bar                  |

The `:` command bar accepts other pdb commands: `:break 42 if x > 3`, `:until`,
`:display EXPR`, `:undisplay EXPR`, `:print EXPR`, `:clear N`, `:where`/`:bt`,
`:run`/`:restart`, `:open PATH`, `:q`.

## Evaluation Caveats

The expression panel resolves a line before it runs, so it has to avoid
changing your program just by looking at it. It only evaluates sub-expressions
that can't have side effects, so it won't evaluate `await`, `yield`, walrus (`:=`), or function
calls.

The one exception is a short list of builtin functions, so a line like
`x = str((4 + 7) / (6 + 8))` resolves all the way down instead of stopping at the
inner float:

```
abs  ascii  bin  bool  chr  float  format  hex  int  len  oct  ord  repr  round  str
```

Two cases worth knowing about if the preview isn't what you expect:

- **Shadowing disables the preview.** A builtin is matched by identity, so if
  you rebind one of these names (`str = my_formatter`, a local `len`, an import,
  etc.), nobug sees that the name no longer points at the real builtin and
  skips evaluating that call rather than running your version.
- **User-defined dunder functions/methods still run.** These builtins call into a value's `__str__`,
  `__repr__`, `__len__`, `__format__`, `__bool__`, and so on. So previewing `str(obj)`
  runs `obj.__str__`, and if that method has side
  effects, they happen during the preview. Calls whose arguments have
  side effects (`str(items.pop())`) aren't previewed.

Everything else (user-defined functions, non-whitelisted builtins) isn't run early.
Their real return values show up in the last evaluated view after you step
over the line.

## Virtual Environments

nobug runs your program in its own interpreter but resolves your program's
imports against a virtual environment if it finds one, so your project's
dependencies (`fastapi`, etc.) work. In order it looks for:

1. the activated venv (`$VIRTUAL_ENV`), else
2. a `.venv`/`venv` beside the script, else
3. a `.venv`/`venv` in the current directory.

If it finds none, it runs against the plain interpreter. Whichever it picks is
noted in the console at startup. Because nobug borrows the venv's packages rather
than relaunching under its interpreter, a venv built for a different Python
version than the one running nobug may fail to import compiled packages.

## Other Limitations

- Single-threaded target programs only (the tracer assumes one thread).
- Stepping stays in your code; it doesn't descend into the stdlib.

## Planned Features

- It would be nice to support different themes
- Syntax highlighting
- Save breakpoints to an sqlite db for convenience?
- Evaluation support for `list` and `set` builtins

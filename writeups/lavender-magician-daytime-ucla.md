# `_curses.window.derwin()` can segfault on a large row coordinate

## Summary

`_curses.window.derwin()` and `subwin()` pass user-controlled C `int`
coordinates to ncurses without checking that they fit within the parent
window.  With `begin_y == INT_MAX` and a positive height, ncurses' bounds
check overflows and it indexes far beyond the parent's line table, crashing a
normal release build instead of raising `curses.error`.

## Reproducer

The following does not require a real terminal:

```python
import curses
import os
import pty

master, slave = pty.openpty()
output = os.fdopen(os.dup(slave), "w")
input = os.fdopen(os.dup(slave), "r")
screen = curses.newterm("xterm", output, input)
screen.stdscr.derwin(1, 1, 2**31 - 1, 0)
```

On AArch64 Linux with ncursesw this exits with `SIGSEGV`.  The crash was
confirmed in a non-debug build of current CPython main.  The same `derwin()`
call also crashes the system CPython 3.14.6 release when its window is created
with `curses.initscr()`.  Replacing `derwin` with `subwin` produces the same
result.  An ordinary out-of-range coordinate, such as row 100 on a 24-line
terminal, correctly raises:

```text
_curses.error: derwin() returned NULL
```

The large coordinate should take the same error path rather than terminating
the process.  The failure is not debug-build-specific and does not depend on
destroying the screen or subsequently using the returned window.

## Root cause

Argument Clinic accepts each argument as a C `int`, so `2**31 - 1` reaches
`_curses_window_derwin_impl()` as `INT_MAX`.  The implementation forwards all
four values directly to ncurses:

```c
static PyObject *
_curses_window_derwin_impl(PyCursesWindowObject *self, int group_left_1,
                           int nlines, int ncols, int begin_y, int begin_x)
{
    WINDOW *win;

    win = derwin(self->win,nlines,ncols,begin_y,begin_x);
    ...
}
```

The relevant ncurses `derwin()` validation is:

```c
if (begy < 0 || begx < 0 || orig == 0 || num_lines < 0 || num_columns < 0)
    returnWin(0);
if (begy + num_lines > orig->_maxy + 1
    || begx + num_columns > orig->_maxx + 1)
    returnWin(0);
```

For `begy == INT_MAX` and `num_lines == 1`, `begy + num_lines` wraps to
`INT_MIN` on the affected build.  The signed comparison therefore does not
reject the request.  After allocating a one-line child window, ncurses assigns
its line pointer from the parent:

```c
for (i = 0; i < num_lines; i++)
    win->_line[i].text = &orig->_line[begy++].text[begx];
```

That evaluates `orig->_line[INT_MAX]` and faults while loading the invalid line
record.  `subwin()` reaches the same code by converting its screen-relative
coordinates and calling `derwin()`.

On AArch64, the failing ncurses instructions make the overflow and invalid
index explicit:

```text
add     w4, w19, w1             # INT_MAX + 1 -> INT_MIN
cmp     w4, w3
b.gt    error                   # signed comparison is false
...
add     x19, x2, w19, uxtw #4   # orig->_line + INT_MAX * 16
ldr     x5, [x19]               # SIGSEGV
```

## Relevant stack

```text
frame #0: libncursesw.so.6`derwin + 256
frame #1: _curses_window_derwin_impl at Modules/_cursesmodule.c:2971
frame #2: _curses_window_derwin at Modules/clinic/_cursesmodule.c.h:997
frame #3: method_vectorcall_VARARGS at Objects/descrobject.c:325
```

The fault address is unrelated to the stack pointer, and the stack pointer is
inside its normal mapped region.  This rules out a stack-growth failure.

## Possible fix direction

The ncurses bounds check should be written without addition overflow, for
example by first proving `begy` is within the parent and then comparing
`num_lines` with `orig->_maxy + 1 - begy`.  The corresponding column check has
the same issue.

CPython should also avoid passing invalid Python-provided coordinates into a
library routine known to index the parent window.  `_curses` can obtain the
parent origin and dimensions with the curses accessors and validate both
`derwin()` and `subwin()` using subtraction-based bounds checks before calling
ncurses.  A regression test should cover `INT_MAX` for both row and column and
assert that `curses.error` is raised for both methods.

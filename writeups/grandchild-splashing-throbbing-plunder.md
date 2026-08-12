# Crash report: parser overflow plus invalid f-string escape aborts warnings

## Summary

Compiling a deeply recursive invalid expression that also contains an invalid
f-string escape can abort a debug build while emitting `SyntaxWarning`:

```python
compile(
    '(x""f"\\{0}"<' * 176,
    "<seed>",
    "exec",
)
```

Observed stderr:

```text
Fatal Python error: _Py_CheckFunctionResult: a function returned a result with an exception set
MemoryError: Parser stack overflowed - Python source too complex to parse

The above exception was the direct cause of the following exception:

SystemError: <class 'SyntaxWarning'> returned a result with an exception set
```

The representative crash input is `grandchild-splashing-throbbing-plunder`,
with the linked core `souffl-placing-techno-boudoir`.

This is a minimized form of the original saved string.  The source passed to
`compile()` is still large after multiplication, but the Python reproducer is
small and the repeated unit is stripped down to the relevant pieces:

- `(` keeps the generated parser in a deeply nested expression parse.
- `x""f"\\{0}"` puts an expression name next to adjacent string literals,
  including an f-string with the invalid `\{` escape.
- `<` and the repeated unterminated string boundary keep parsing from
  resolving cleanly before the parser stack overflows.

## What is going on

The repeated source string drives the PEG parser into a very deep expression
parse.  Generated parser rules guard their recursion with:

```c
if (p->level++ == MAXSTACK ||
    _Py_ReachedRecursionLimitWithMargin(PyThreadState_Get(), 1)) {
    _Pypegen_stack_overflow(p);
}
```

`_Pypegen_stack_overflow()` marks the parser as failed and sets:

```c
PyErr_SetString(PyExc_MemoryError,
    "Parser stack overflowed - Python source too complex to parse");
```

While the parser is in that failed state, tokenization still reaches an f-string
fragment containing `\{`.  The f-string lexer handles backslash-before-brace as
an invalid escape warning:

```c
if (peek == '{' || peek == '}') {
    if (!current_tok->raw) {
        if (_PyTokenizer_warn_invalid_escape_sequence(tok, peek)) {
            return MAKE_TOKEN(ERRORTOKEN);
        }
    }
    tok_backup(tok, peek);
    continue;
}
```

`_PyTokenizer_warn_invalid_escape_sequence()` then calls:

```c
PyErr_WarnExplicitObject(PyExc_SyntaxWarning, msg, tok->filename,
                         tok->lineno, tok->module, NULL)
```

The warnings machinery normalizes the warning message by constructing an
instance of the category:

```c
message = PyObject_CallOneArg(category, message);
```

At this point the previous parser `MemoryError` is still set.  The call to
`SyntaxWarning(message)` returns a valid warning object, but it returns it while
an exception is already pending.  `_Py_CheckFunctionResult()` treats that as a
C API contract violation, replaces it with a `SystemError`, and debug builds
abort.

## Why this matters

The input is ordinary source passed to `compile()`.  It does not depend on
ctypes, bad pickle data, signals, explicit process termination, or changing the
recursion limit.  The parser stack overflow itself is a normal handled error;
the bug is that the tokenizer/parser warning path can run while that error is
pending and can then call back into Python object construction without first
preserving or clearing the pending exception.

In a non-debug build, this path should still be treated as a bug even if it
does not abort immediately: it produces a `SystemError` from warning
normalization instead of cleanly reporting the parser overflow, or cleanly
reporting/raising the invalid escape warning according to the active warnings
filters.

## Relationship to gh-151238

This is closely related to
[python/cpython#151238](https://github.com/python/cpython/issues/151238), but it
does not appear to be the same immediate bug.

In gh-151238, the invalid escape warning is what first creates the pending
exception: the reproducer deliberately breaks `builtins.__import__`, warning
formatting raises `TypeError`, `_PyPegen_decode_fstring_part()` fails, and
`_get_resized_exprs()` returns `NULL`.  The missing local check was that
`_PyPegen_joined_str()` and `_PyPegen_template_str()` still passed that `NULL`
sequence to `_PyAST_JoinedStr()`/`_PyAST_TemplateStr()`, creating a temporary
invalid AST node with an exception still set.  The merged fix in
[python/cpython#151259](https://github.com/python/cpython/pull/151259) added
those `resized_exprs == NULL` checks, with backports in
[python/cpython#151344](https://github.com/python/cpython/pull/151344) and
[python/cpython#151345](https://github.com/python/cpython/pull/151345).

Here the pending exception exists before warning normalization runs.  The deep
expression parse hits `_Pypegen_stack_overflow()` and sets the parser overflow
`MemoryError`; after that, the lexer still reaches the invalid f-string escape
and calls `PyErr_WarnExplicitObject()`.  `SyntaxWarning(message)` can then return
a warning instance while the parser overflow exception is already pending, which
trips `_Py_CheckFunctionResult()` directly.  The stack for this reproducer
reaches the warning path through `_PyTokenizer_warn_invalid_escape_sequence()`
and `tok_get_fstring_mode()`, not through `_get_resized_exprs()`.

So the two crashes share the same broader invariant violation: parser/tokenizer
warning code is allowed to run while an unrelated exception is already pending.
The older fix removes one downstream way that a warning failure could be
converted into an invalid joined/template string AST node.  It does not by
itself address this reproducer's root cause: warning emission after parser stack
overflow has already set `PyErr_Occurred()`.

## Relevant stack

```text
frame #6:  _Py_CheckFunctionResult(...) at Objects/call.c:65
frame #7:  PyObject_CallOneArg(SyntaxWarning, message) at Objects/call.c:395
frame #8:  warn_explicit(...) at Python/_warnings.c:806
frame #9:  PyErr_WarnExplicitObject(...) at Python/_warnings.c:1439
frame #10: _PyTokenizer_warn_invalid_escape_sequence(...) at Parser/tokenizer/helpers.c:131
frame #11: tok_get_fstring_mode(...) at Parser/lexer/lexer.c:1581
frame #14: _PyPegen_fill_token(...) at Parser/pegen.c:249
frame #16: fstring_rule(...) at Parser/parser.c:17010
```

The deeper stack is a long repetition of generated parser expression rules,
ending in:

```text
_PyPegen_parse(...) at Parser/parser.c:39645
_PyPegen_run_parser(...) at Parser/pegen.c:961
_PyPegen_run_parser_from_string(...) at Parser/pegen.c:1087
_Py_CompileStringObjectWithModule(...) at Python/pythonrun.c:1538
builtin_compile(...) at Python/bltinmodule.c.h:472
```

## Possible fix direction

The warning emission path should not call `PyErr_WarnExplicitObject()` with an
unrelated exception already set.  The gh-151238 fix is useful precedent for
checking all fallible joined/template string helper results before building AST
nodes, but this report probably needs an earlier guard.  Plausible fixes
include:

- stop tokenizing and suppress additional invalid-escape warnings once
  `p->error_indicator` or `PyErr_Occurred()` records parser stack overflow;
- save and clear the pending parser exception around warning normalization,
  then restore it if warning emission succeeds;
- make the f-string invalid-escape warning path mirror parser error handling so
  a pre-existing parser failure wins deterministically.

A regression test should compile a source string that combines enough repeated
expression structure to trigger parser stack overflow with an invalid f-string
brace escape such as `f"\\{"`, and should assert that the result is a normal
Python exception rather than a fatal debug abort or `SystemError` from
`SyntaxWarning`.

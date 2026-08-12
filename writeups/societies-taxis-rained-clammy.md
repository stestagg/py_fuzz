# Stateful recursion crash in `maybe_lltrace_resume_frame()`

## Short answer

`societies-taxis-rained-clammy` and `uniformed-sevens-canine-civic` are the
same crash shape.  Both cores stop while entering/resuming a deeply recursive
Python call, in the debug-only low-level tracing check:

```text
PyDict_Contains()
maybe_lltrace_resume_frame() at Python/ceval.h:291
_PyEval_EvalFrameDefault() at Python/generated_cases.c.h:13857
```

I do not see enough evidence to call this an allowed stack-growth crash.
The stack-fault classifier only scored both artifacts as `1`, with the sole
factor being that the process stopped on SIGSEGV/SIGBUS.  In both cores the
saved stack pointer is inside a mapped read/write stack region, not in an
unmapped guard page or adjacent stack-growth hole.  The final input also does
not raise the recursion limit or explicitly crash the process; run by itself
under the same project Python it raises the expected `RecursionError`.

This currently looks like a real stateful interpreter crash triggered by a
normal recursion failure after earlier persistent-process history, not one of
the listed allowed-crash categories.

## Artifacts

Both artifacts are core artifacts:

```text
societies-taxis-rained-clammy
  worker: w4
  pid: 2396
  inputs_run: 823
  stackalloc_score: 1
  stackalloc_factors: +process stopped on SIGSEGV/SIGBUS

uniformed-sevens-canine-civic
  worker: w8
  pid: 2425
  inputs_run: 538
  stackalloc_score: 1
  stackalloc_factors: +process stopped on SIGSEGV/SIGBUS
```

The saved last input is identical in both:

```python
class C:
    def __call__(self, x):
        return self(x**18446744073709551616)
C()(0)
```

Running just that input under the `prs-6` project Python gives a normal
recursion failure:

```text
RecursionError: maximum recursion depth exceeded
```

So the crash depends on earlier state in the long-lived fuzzing process.  The
existing tracked script,
`projects/prs-6/scratch/reproducers/societies-track.py`, replays 823 inputs,
but running it as an ordinary Python script did not reproduce the crash.  That
script is not an exact reproduction of the C harness path: the original
`fuzz_python` harness compiles each input, evaluates it with a fresh globals
dict, then clears any pending C-level exception before moving to the next
iteration.

## Core evidence

The LLDB backtraces for both cores are identical in the relevant frames:

```text
* thread #1, name = 'fuzz_python', stop reason = SIGSEGV: sent by tkill system call
  frame #3: PyDict_Contains(...) at Objects/dictobject.c:5296 [inlined]
  frame #4: maybe_lltrace_resume_frame(...) at Python/ceval.h:291 [inlined]
  frame #5: _PyEval_EvalFrameDefault(...) at Python/generated_cases.c.h:13857
```

The relevant source is:

```c
static int
maybe_lltrace_resume_frame(_PyInterpreterFrame *frame, PyObject *globals)
{
    if (globals == NULL) {
        return 0;
    }
    if (frame->owner >= FRAME_OWNED_BY_INTERPRETER) {
        return 0;
    }
    int r = PyDict_Contains(globals, &_Py_ID(__lltrace__));
    ...
}
```

`GLOBALS()` is `frame->f_globals`, and `LLTRACE_RESUME_FRAME()` calls this
debug-only helper when frames are entered/resumed.  The crash is not in user
code directly; it happens while the eval loop is checking whether low-level
tracing is enabled for the frame.

Faulthandler printed a deep Python recursion stack immediately before the core:

```text
Fatal Python error: Segmentation fault

Current thread ... (most recent call first):
  File "<fuzz>", line 2 in __call__
  File "<fuzz>", line 3 in __call__
  File "<fuzz>", line 3 in __call__
  ...
```

The C stack repeats this pattern:

```text
dict_contains
_PyEval_EvalFrameDefault
_PyObject_MakeTpCall
_Py_VectorCall_StackRefSteal
_PyEval_EvalFrameDefault
...
```

That is consistent with recursive calls through a Python `__call__` method.
However, the standalone reproducer proves that this call pattern is normally
handled by the recursion guard.

## Why this is not established as an allowed crash

The only allowed recursion-related category that looks nearby is:

> Cases where the kernel cannot allocate a stack page due to memory pressure
> and thus triggering a stack overflow.

The current evidence does not meet that bar:

- Neither artifact has a stack-near fault address recorded.
- The saved `$sp` memory region is mapped `rw-`, not unmapped.
- The classifier did not find deep-stack/stack-growth signals beyond the
  generic SIGSEGV/SIGBUS stop.
- The input does not call `sys.setrecursionlimit()`.
- The input does not intentionally raise a signal, call `abort()`, or exit with
  a crash code.
- The same project Python raises `RecursionError` for the final input when run
  outside the prior fuzz history.

The run history does include memory-pressure symptoms: several earlier tracked
inputs hit `MemoryError` while recursively calling a function around 540 frames.
That may be relevant state or pressure, but it is not proof of a failed stack
page allocation at the final crash.

## Current hypothesis

The best current hypothesis is that earlier persistent-process history leaves
the eval/recursion machinery in a bad state, and the final recursive
`__call__` input then reaches `LLTRACE_RESUME_FRAME()` with frame state that is
not safe for `PyDict_Contains(frame->f_globals, "__lltrace__")`.

The suspicious code-level boundary is the handoff between:

1. specialized Python-call opcodes decrementing `tstate->py_recursion_remaining`
   while pushing inlined Python frames, and
2. the central `start_frame` path, which calls `_Py_EnterRecursivePy()` and then
   immediately performs the debug-only `maybe_lltrace_resume_frame()` check.

That path should produce `RecursionError` before unsafe frame evaluation.  The
fact that both cores stop in the lltrace check during recursive frame entry is
the actionable clue.

## Reproduction status

Confirmed:

- both named artifacts are duplicate core signatures;
- both are stateful and linked to recursive final input;
- standalone final input raises `RecursionError` in the same project build;
- the crash site is the debug-only lltrace resume check in the eval loop;
- there is not enough evidence for an allowed-crash classification.

Not yet reduced:

- the minimal earlier input/state that poisons the process before the final
  recursive call;
- whether the crash requires the exact `fuzz_python` C harness exception-clear
  path, memory limit, prior `MemoryError`, or some combination of those.

I saved the small inspection artifacts used for this analysis under
`projects/prs-6/scratch/lldb/` and the standalone final-input check under
`projects/prs-6/scratch/reproducers/societies-last-input.py`.

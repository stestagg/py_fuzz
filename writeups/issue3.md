# Crash report: caught deque repeat `MemoryError` followed by recursive import segfaults

### What happened?

This crashes a debug build for me on Linux/aarch64:

```python
import resource
import unicodedata

limit = 512 * 1024 * 1024
resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

try:
    from collections import deque
    (6148914691236517205) * deque([0])
except MemoryError:
    pass

try:
    __import__("." + ("x." * 256) + "y")
except ModuleNotFoundError:
    pass
```

Observed result:

```text
Segmentation fault
```

The two operations are independently recoverable in the same build:

* The deque repeat alone raises `MemoryError`.
* The import alone raises `ModuleNotFoundError`.

Together, after both exceptions are caught, the process segfaults.

### Expected behavior

The repeated deque allocation should either succeed or raise `MemoryError`.

The invalid dotted import should raise an import exception. Since both expected
exceptions are caught, the script should exit normally.

### What is going on

The evidence points away from `deque_repeat()` corrupting the deque or the
interpreter. The deque repeat is the address-space pressure source; the crash
is in the later recursive import path when the C stack needs to grow after the
process has already hit `RLIMIT_AS`.

The first stage goes through the single-element fast path in
`deque_inplace_repeat_lock_held()`:

```c
if (size == 1) {
    PyObject *item = deque->leftblock->data[deque->leftindex];
    ...
    for (i = 0 ; i < n-1 ; ) {
        if (deque->rightindex == BLOCKLEN - 1) {
            block *b = newblock(deque);
            if (b == NULL) {
                Py_SET_SIZE(deque, Py_SIZE(deque) + i);
                return NULL;
            }
            ...
        }
        ...
        while (m--) {
            deque->rightindex++;
            deque->rightblock->data[deque->rightindex] = Py_NewRef(item);
        }
    }
    Py_SET_SIZE(deque, Py_SIZE(deque) + i);
    return Py_NewRef(deque);
}
```

In the non-in-place repeat path, `deque_repeat()` then decrefs the partially
expanded new deque after `deque_inplace_repeat_lock_held()` returns `NULL`.

I tried targeted allocation-failure runs against the 528-byte deque block
allocation size. Failing `newblock()` directly, including after 100,000
successful deque block allocations, produces a clean `MemoryError`; the later
deep import then raises `ModuleNotFoundError` normally. That makes a malformed
deque or bad `deque_repeat()` cleanup unlikely.

The reproducing case differs because it is real `RLIMIT_AS` exhaustion. After
the caught deque `MemoryError`, the process is still close enough to the address
space limit that it cannot reliably map more memory. As a sanity check, trying
to import `_ctypes` after the deque failure failed with:

```text
ImportError: .../_ctypes...so: failed to map segment from shared object
```

The second stage then enters importlib's recursive parent import logic in
`Lib/importlib/_bootstrap.py`:

```python
parent = name.rpartition('.')[0]
if parent:
    if parent not in sys.modules:
        _call_with_frames_removed(import_, parent)
```

For a name like `"." + ("x." * 256) + "y"`, this creates a deep chain of
recursive imports through:

```text
_find_and_load_unlocked()
_call_with_frames_removed(import_, parent)
builtins.__import__()
PyImport_ImportModuleLevelObject()
```

Under LLDB, the standalone reproducer stops with a fault address near the stack:

```text
_PyEval_EvalFrameDefault(...) at Python/ceval.c:1229
stop reason = SIGSEGV: address not mapped to object
```

The backtrace is a long repetition of:

```text
_PyEval_EvalFrameDefault
_PyObject_VectorcallDictTstate
slot_tp_new
type_call
...
PyImport_ImportModuleLevelObject
builtin___import__
...
```

`_PyEval_EvalFrameDefault()` does check the C stack at function entry:

```c
if (_Py_EnterRecursiveCallTstate(tstate, "")) {
    assert(frame->owner != FRAME_OWNED_BY_INTERPRETER);
    _PyEval_FrameClearAndPop(tstate, frame);
    return NULL;
}
```

But in this failure mode the process segfaults at entry to the evaluator before
that check can turn the condition into a Python-level exception. The recursive
import is therefore exposing an address-space/stack-growth failure after a
recoverable `MemoryError`.

### Why the import matters

The import name is not special beyond forcing recursive parent imports. The
original input used a malformed import string, but it reduces to the deep
dotted import above.

A shallow invalid import after the deque `MemoryError` exits cleanly. A deep
dotted import without the preceding deque allocation failure also exits cleanly.
The crash needs both:

1. A caught allocation failure that leaves the process near `RLIMIT_AS`.
2. A following import that recursively grows the C stack.

### Why this is probably not deque corruption

I do not see evidence that `deque_repeat()` leaves a corrupted deque behind.
The targeted failure tests make the same C error path return cleanly when the
process is not otherwise address-space exhausted.

Other allocation failures also do not reproduce the same crash in my reduced
tests. For example, replacing the deque stage with large `bytearray` or `list`
allocation failures made the subsequent import raise normally. That suggests
the exact allocator/address-space shape matters, not that every caught
`MemoryError` poisons the interpreter.

### Possible fix direction

The dangerous code path is the recursive parent import in importlib combined
with the evaluator's C-stack guard being reached too late when stack growth
itself fails under `RLIMIT_AS`.

Possible directions to audit:

* Make the parent import walk iterative, or otherwise avoid one C/Python
  recursive import per dotted component.
* Add an explicit recursion/C-stack check before the recursive
  `_call_with_frames_removed(import_, parent)` call.
* Check whether `_Py_EnterRecursiveCallTstate()` can reserve enough headroom for
  this import path under constrained address space, or whether this is a case
  where the guard cannot run before the stack faults.

The important behavioral expectation is that a recoverable `MemoryError`
followed by a recoverable `ModuleNotFoundError` should not be able to terminate
the process with SIGSEGV.

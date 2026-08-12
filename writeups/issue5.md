# Huge recursion limit can segfault while freeing a deep traceback

### What happened?

This can crash CPython instead of reporting a normal Python exception:

```python
import resource
import sys

resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,
                                        512 * 1024 * 1024))
sys.setrecursionlimit(2**26)

def f():
    return f()

f()
```

The `resource.setrlimit()` call is only there to make the reproducer finish
quickly and avoid relying on system-wide memory exhaustion. The same crash shape
also occurs with a much larger address-space limit.

Observed behavior on a debug CPython 3.16 build is a SIGSEGV while clearing the
traceback attached to a `MemoryError`. Expected behavior is a clean
`RecursionError`, `MemoryError`, or fatal error with a controlled diagnostic,
not an uncontrolled native stack overflow in object deallocation.

### What is going on

`sys.setrecursionlimit()` accepts the very large limit because it only rejects
values below 1 and values below the current Python recursion depth:

```c
if (new_limit < 1) {
    ...
}
int depth = tstate->py_recursion_limit - tstate->py_recursion_remaining;
if (depth >= new_limit) {
    ...
}
Py_SetRecursionLimit(new_limit);
```

`Py_SetRecursionLimit()` then updates `tstate->py_recursion_limit` and
`tstate->py_recursion_remaining` to that large value. After this, recursive
Python calls do not hit the normal Python recursion-depth guard before the
process runs into address-space pressure.

When allocation fails in the deeply recursive call chain, CPython raises
`MemoryError`. While building or unwinding the exception state,
`_PyFrame_MakeAndSetFrameObject()` preserves and later decrefs the raised
exception if frame allocation fails:

```c
PyObject *exc = PyErr_GetRaisedException();
PyFrameObject *f = _PyFrame_New_NoTrack(_PyFrame_GetCode(frame));
if (f == NULL) {
    Py_XDECREF(exc);
    return NULL;
}
```

That decref can destroy the `MemoryError`. `MemoryError_dealloc()` calls
`BaseException_clear()`, which clears the exception traceback:

```c
Py_CLEAR(self->traceback);
```

The traceback is enormous because it records the failed recursive call chain.
Traceback destruction is recursive:

```c
static void
tb_dealloc(PyObject *op)
{
    PyTracebackObject *tb = _PyTracebackObject_CAST(op);
    PyObject_GC_UnTrack(tb);
    Py_XDECREF(tb->tb_next);
    Py_XDECREF(tb->tb_frame);
    PyObject_GC_Del(tb);
}
```

Each traceback object decrefs `tb_next`, which enters `_Py_Dealloc()`, which
calls `tb_dealloc()` for the next traceback, and so on. With an approximately
18k to 19k element traceback chain, this produces an equally deep native call
chain of `_Py_Dealloc()` and `tb_dealloc()`.

CPython has a generic delayed-deletion path in `_Py_Dealloc()` for GC objects:

```c
intptr_t margin = _Py_RecursionLimit_GetMargin(tstate);
if (margin < 2 && gc_flag) {
    _PyTrash_thread_deposit_object(tstate, (PyObject *)op);
    return;
}
...
(*dealloc)(op);
```

But in this failure mode the recursive traceback chain can fault while entering
the next `_Py_Dealloc()` call, before the new `_Py_Dealloc()` frame reaches this
margin check. In other words, the guard that is meant to turn recursive
container destruction into delayed deletion is reached too late for this
traceback destruction path.

One plausible fix direction is to make traceback deallocation non-recursive, for
example by walking and releasing a traceback chain iteratively, or otherwise to
ensure the trashcan/delayed-deletion decision is made before another
`_Py_Dealloc()` stack frame is entered.

### Debugging notes

The same signature was seen in these stored core artifacts:

```text
escape-thirteenth-anja-concocted
hoses-alaric-galore-scents
insulin-emailed-maximum-gentleness
skinned-harrowing-bleedin-plucking
straightaway-comply-entertained-fierce
tattoos-bustin-network-sputters
```

Their final inputs all reduce to:

```python
import sys
sys.setrecursionlimit(<huge integer>)

def f():
    return f()

f()
```

or the same function wrapped in a `try`/`finally`.

<details>
<summary>Representative LLDB stack</summary>

```text
Process 75 stopped
* thread #1: tid = 75, 0x0000aaaaaaff2fa0 python3.16`_Py_Dealloc(op=0x0000fffff7a1d680) at object.c:3291, name = 'python3', stop reason = signal SIGSEGV: address not mapped to object (fault address=0xfffffff48fb0)

* frame #0: 0x0000aaaaaaff2fa0 python3.16`_Py_Dealloc(op=0x0000fffff7a1d680) at object.c:3291
  frame #1: 0x0000aaaaab4e6fc8 python3.16`Py_DECREF(...) at refcount.h:410:9 [inlined]
  frame #2: 0x0000aaaaab4e6fa8 python3.16`Py_XDECREF(...) at refcount.h:520:9 [inlined]
  frame #3: 0x0000aaaaab4e6fa8 python3.16`tb_dealloc(op=0x0000fffff7a1d6d0) at traceback.c:245:5
  frame #4: 0x0000aaaaaaff32e0 python3.16`_Py_Dealloc(op=<unavailable>) at object.c:3319:5
  frame #5: 0x0000aaaaab4e6fc8 python3.16`Py_DECREF(...) at refcount.h:410:9 [inlined]
  frame #6: 0x0000aaaaab4e6fa8 python3.16`Py_XDECREF(...) at refcount.h:520:9 [inlined]
  frame #7: 0x0000aaaaab4e6fa8 python3.16`tb_dealloc(op=0x0000fffff7a1d720) at traceback.c:245:5
  ...
  frame #18542: 0x0000aaaaaaee1bac python3.16`BaseException_clear(op=0x0000fffff7c27690) at exceptions.c:133:5
  frame #18543: 0x0000aaaaaaef20dc python3.16`MemoryError_dealloc(op=0x0000fffff7c27690) at exceptions.c:4171:11
  frame #18544: 0x0000aaaaaaff32e0 python3.16`_Py_Dealloc(op=<unavailable>) at object.c:3319:5
  frame #18545: 0x0000aaaaab372ea0 python3.16`Py_DECREF(...) at refcount.h:410:9 [inlined]
  frame #18546: 0x0000aaaaab372e80 python3.16`Py_XDECREF(...) at refcount.h:520:9 [inlined]
  frame #18547: 0x0000aaaaab372e80 python3.16`_PyFrame_MakeAndSetFrameObject(frame=0x0000ffffd93e4378) at frame.c:28:9
  frame #18548: 0x0000aaaaab2c8054 python3.16`_PyFrame_GetFrameObject(frame=<unavailable>) at pycore_interpframe.h:351:12 [inlined]
  frame #18549: 0x0000aaaaab2c7ff0 python3.16`_PyEval_EvalFrameDefault(...) at generated_cases.c.h:13764:36
```

</details>

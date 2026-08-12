# Crash report: `deque.extend()` double-decrefs an iterator item after block allocation failure

## Short answer

The tactical patch in `tactical-patches/deque.diff` appears to expose a
different deque bug rather than causing the new negative-refcount crash.

The failing assertion is not in the patched `deque_clear()` path.  It is at
`Modules/_collectionsmodule.c:515`, inside `deque_extend_impl()`:

```c
while ((item = iternext(it)) != NULL) {
    if (deque_append_lock_held(deque, item, maxlen) == -1) {
        Py_DECREF(item);
        Py_DECREF(it);
        return NULL;
    }
}
```

On an allocation failure, `deque_append_lock_held()` has already decref'ed
`item` before returning `-1`:

```c
if (deque->rightindex == BLOCKLEN - 1) {
    block *b = newblock(deque);
    if (b == NULL) {
        Py_DECREF(item);
        return -1;
    }
    ...
}
```

So the caller's `Py_DECREF(item)` is a second decref of the same new reference
returned by the iterator.  With a heap `int` from `range(...)`, the first decref
drops it to zero and the second decref trips `_Py_NegativeRefcount`.

The tactical patch only changes exception preservation around `newblock()` in
`deque_clear()`:

```diff
+    PyObject *old_exc = PyErr_GetRaisedException();
     b = newblock(deque);
+    PyErr_SetRaisedException(old_exc);
     if (b == NULL) {
-        PyErr_Clear();
         goto alternate_method;
     }
```

It does not touch `deque_extend_impl()` or `deque_append_lock_held()`.  The
double-decref code is also present in the unpatched source as shown by
`git -C projects/prs-1/cpython show HEAD:Modules/_collectionsmodule.c`.

## Artifact shape

Project `prs-1` has 13 artifacts, all core artifacts rather than saved crash
inputs:

```text
saved_crashes: 0
core_dumps: 13
py_debug: true
track_inputs: true
fuzz_mem_limit: 2048
```

All 13 imported artifacts report the same signature in `harness_stderr.txt`:

```text
./Modules/_collectionsmodule.c:515: _Py_NegativeRefcount: Assertion failed: object has negative ref count
object type name: int
object repr     : <refcnt 0 at ...>
MemoryError:
```

The artifacts differ in worker and crash phase (`module_cleanup`,
`gc_collect`, or no recorded phase), but the C assertion and object type are the
same.

The generated track reproducers reduce to large deque construction under memory
pressure.  The minimized examples are:

- `projects/prs-1/scratch/reproducers/deque-repro-1.min.py`
- `projects/prs-1/scratch/reproducers/deque-repro-4.min.py`

`deque-repro-4.min.py` reduces to two executions of this shape:

```python
from collections import deque

class I(int):
    def __index__(self):
        return i.t(self)

d = deque(range(8888888888888))
...
```

Running it under a 512 MB cap reproduces the same abort:

```sh
env UV_CACHE_DIR=/private/tmp/uv-cache uv run ./pfx --project prs-1 \
  run-cmd --pfrun --image lldb -- \
  env MEM_LIMIT_MB=512 /pfm/tools/mem_limit_exec \
  /pfm/py/bin/python3 /pfm/scratch/reproducers/deque-repro-4.min.py
```

Relevant output:

```text
./Modules/_collectionsmodule.c:515: _Py_NegativeRefcount: Assertion failed: object has negative ref count
object type name: int
MemoryError
Current thread ...:
  File "<input-32>", line 5 in <module>
  File "/pfm/scratch/reproducers/deque-repro-4.min.py", line 16 in <module>
```

## Deterministic repro

The huge `range(...)` inputs are just a way to make the next deque block
allocation fail.  A targeted `pfalloc` reproducer confirms that no long fuzzing
history and no `deque_clear()` call are needed:

```python
from collections import deque

import pfalloc

d = deque()

pfalloc.enable()
pfalloc.set_counter(1, 528)
d.extend(range(1000, 1100))
```

This was saved as:

```text
projects/prs-1/scratch/reproducers/deque-pfalloc-repro.py
```

Running it in the `prs-1` build:

```sh
env UV_CACHE_DIR=/private/tmp/uv-cache uv run ./pfx --project prs-1 \
  run-cmd --pfrun --image lldb -- \
  /pfm/py/bin/python3 /pfm/scratch/reproducers/deque-pfalloc-repro.py
```

reproduces:

```text
./Modules/_collectionsmodule.c:515: _Py_NegativeRefcount: Assertion failed: object has negative ref count
object type name: int
MemoryError
Current thread ...:
  File "/pfm/scratch/reproducers/deque-pfalloc-repro.py", line 10 in <module>
```

The `528` byte filter matches `sizeof(block)` on this build:

```c
typedef struct BLOCK {
    struct BLOCK *leftlink;
    PyObject *data[BLOCKLEN];  /* BLOCKLEN == 64 */
    struct BLOCK *rightlink;
} block;
```

On 64-bit, that is 66 pointers, or 528 bytes.

## What is going on

1. `deque(range(...))` constructs an empty deque and then extends it from the
   `range` iterator.

2. For an empty deque, `deque_extend_impl()` starts filling the initial block
   from the left:

   ```c
   deque->leftindex = 1;
   deque->rightindex = 0;
   ```

3. Once the current block is full, the next item from the iterator is held in
   `item` with a new reference.

4. `deque_extend_impl()` passes that reference to:

   ```c
   deque_append_lock_held(deque, item, maxlen)
   ```

   On success, this helper consumes ownership by storing `item` into the deque.

5. If `newblock(deque)` fails, `deque_append_lock_held()` also consumes
   ownership on the failure path by calling `Py_DECREF(item)` before returning
   `-1`.

6. `deque_extend_impl()` treats the failure as if ownership was not consumed and
   decrefs `item` again at line 515.

7. The second decref sees an `int` whose refcount is already zero and the debug
   build aborts with `_Py_NegativeRefcount`.

`deque_extendleft_impl()` is useful comparison evidence.  It calls the matching
left helper, but on failure it only decrefs the iterator:

```c
while ((item = iternext(it)) != NULL) {
    if (deque_appendleft_lock_held(deque, item, maxlen) == -1) {
        Py_DECREF(it);
        return NULL;
    }
}
```

That matches the helper's ownership convention.  The right-extension path is
the inconsistent one.

## Relationship to `tactical-patches/deque.diff`

The patch is about preserving an already raised exception while `deque_clear()`
tries to allocate a fresh empty block.  That looks like a real fix for the
earlier "exception was cleared while memory is tight" debug abort.

The negative-refcount crash is different:

- the fatal source line is `deque_extend_impl()` line 515, not `deque_clear()`;
- a deterministic `pfalloc` failure of the next deque block allocation
  reproduces it without forcing the `deque_clear()` alternate method;
- the double-decref code exists in the unpatched source;
- the minimized reproducers fail while constructing/extending a deque from a
  huge `range`, before the later `d.index(...)` code matters.

My read is that the tactical patch changes which bug wins the race under memory
pressure.  Without it, cleanup under `MemoryError` can hit the exception-clearing
assert first.  With it, that earlier abort is avoided, so the existing
`deque.extend()` allocation-failure ownership bug becomes visible.

## Release-build impact

This is not just a harmless debug assert.  It is a double `Py_DECREF()` of a
new reference from an iterator after an allocation failure.  In debug builds,
CPython catches it as a negative refcount.  In release builds, the same path can
decref an already deallocated object, which is memory corruption territory even
if it does not always crash immediately.

## Likely fix direction

The local ownership convention already used by `extendleft()` is that
`deque_append*_lock_held()` consumes `item` on both success and allocation
failure.  The narrow fix is therefore to remove the extra decref in
`deque_extend_impl()`:

```diff
 while ((item = iternext(it)) != NULL) {
     if (deque_append_lock_held(deque, item, maxlen) == -1) {
-        Py_DECREF(item);
         Py_DECREF(it);
         return NULL;
     }
 }
```

The alternative would be to make `deque_append_lock_held()` not consume `item`
on failure, but that would require auditing and changing all callers that pass a
new reference.  The `extendleft()` implementation strongly suggests that the
helper-consuming-on-failure convention is the intended one.

## Regression test idea

The most direct regression test is an allocation-failure test for
`deque.extend()` when adding the first item that requires a new block.  In the
local fuzzing environment, `pfalloc` makes this tiny and deterministic:

```python
from collections import deque
import pfalloc

d = deque()
pfalloc.enable()
pfalloc.set_counter(1, 528)
try:
    try:
        d.extend(range(1000, 1100))
    except MemoryError:
        pass
finally:
    pfalloc.set_counter(0)
    pfalloc.disable()
```

For upstream CPython, the equivalent test would need to use whatever allocator
failure hook is acceptable in the test suite.  The behavioral expectation is
that `d.extend(...)` raises `MemoryError` cleanly, without a negative refcount
or other memory corruption.

# Crash report: low recursion limit makes failed `array` import corrupt GC ownership

### What happened?

This aborts a debug build:

```python
import sys

sys.setrecursionlimit(25)
import array
```

Observed stderr from the minimized `prs-5` reproducer:

```text
Python/gc.c:96: gc_decref: Assertion "gc_get_refs(g) > 0" failed: refcount is too small
object type name: type
object repr     : <class 'array.array'>
Fatal Python error: _PyObject_AssertFailed: _PyObject_AssertFailed
```

The fuzzer's minimized two-input script ran each input under the tracker
`try/except`. The two inputs were:

```python
import sys
sys.setrecursionlimit(5**2)
class X:
    def __repr__(self):
        return repr(self)
repr(X())
```

followed by:

```python
import array
a = array.array("B")
for n in (0, 1, 2, 255, 256):
    (a.__len__(memoryview)).extend([0] * n)
    a[:] = array.array("B")
```

The recursive `repr()` and the array operations are not required. The first
input's important effect is lowering the recursion limit to 25. The second
input's important effect is importing `array`.

### Expected behavior

Lowering the recursion limit should not leave a partially initialized extension
module in a refcount-invalid state.

If `import array` cannot complete under a very low, user-selected recursion
limit, it should fail with a normal Python exception such as `RecursionError`.
It should not abort during GC.

### Artifact shape

In the `prs-5` fuzzing project, this came from core artifact
`mayhem-moonlight-minimize-wellington`.

The project has 12 imported core artifacts with LLDB output. Eight match the
same GC/array signature:

```text
calypso-witness-ilse-surgery
cornwall-bogs-sarin-hereby
hosted-korsak-brute-succeeded
mayhem-moonlight-minimize-wellington
overthere-drill-playful-chakotay
southern-sardar-awakening-conjecture
venetian-corleone-unbridled-malaya
zenith-populated-labyrinth-christened
```

For `mayhem-moonlight-minimize-wellington`, LLDB stops in:

```text
_PyObject_AssertFailed(...)
gc_decref(...) at gc.c:94
visit_decref(...) at gc.c:452
descr_traverse(...) at descrobject.c:713
subtract_refs(...) at gc.c:497
deduce_unreachable(...) at gc.c:1177
gc_collect_main(...) at gc.c:1509
```

The failing object is always `<class 'array.array'>`.

### What is going on

The bug is an ownership mismatch in `array_modexec()`'s error path.

1. `array_modexec()` creates heap types and stores owned references in the
   module state:

   ```c
   CREATE_TYPE(m, state->ArrayType, &array_spec);
   CREATE_TYPE(m, state->ArrayIterType, &arrayiter_spec);
   ```

2. It then adds another reference to the module dict:

   ```c
   if (PyModule_AddObjectRef(m, "ArrayType",
                             (PyObject *)state->ArrayType) < 0) {
       return -1;
   }
   ```

3. Before the normal `PyModule_AddType()` call, it imports
   `collections.abc.MutableSequence` and registers `array.array`:

   ```c
   PyObject *mutablesequence = PyImport_ImportModuleAttrString(
           "collections.abc", "MutableSequence");
   if (!mutablesequence) {
       Py_DECREF((PyObject *)state->ArrayType);
       return -1;
   }
   PyObject *res = PyObject_CallMethod(mutablesequence, "register", "O",
                                       (PyObject *)state->ArrayType);
   Py_DECREF(mutablesequence);
   if (!res) {
       Py_DECREF((PyObject *)state->ArrayType);
       return -1;
   }
   ```

4. With `sys.setrecursionlimit(25)`, the import machinery can fail while
   loading the Python modules needed for `collections.abc`.

5. On that failure path, `array_modexec()` decrefs `state->ArrayType` but leaves
   `state->ArrayType` non-NULL.

6. The module state still behaves as if it owns that reference. Its traversal
   and clear functions both use the stale non-NULL slot:

   ```c
   Py_VISIT(state->ArrayType);
   ...
   Py_CLEAR(state->ArrayType);
   ```

7. During GC, that stale slot is reported as an internal reference even though
   the refcount was already decremented. GC subtracts one more internal
   reference from `array.array` than the real refcount supports, and the debug
   build trips:

   ```c
   _PyObject_ASSERT_WITH_MSG(FROM_GC(g),
                             gc_get_refs(g) > 0,
                             "refcount is too small");
   ```

The LLDB frame happens to show `descr_traverse()` visiting a descriptor's
`d_type`, but the descriptor is not the root cause. It is just where the GC
accounting finally notices that the `array.array` type has too few counted
references after the stale module-state edge was also traversed.

### Why the fuzzer input looked more complicated

The original tracked run had 225 inputs and the minimized reproducer kept two
of them: a low-recursion recursive `repr()` input and a later `array` input.

The recursive `repr()` is recoverable by itself. The later array operations are
also recoverable by themselves. The crash reduces further because the recursive
`repr()` only served to preserve:

```python
sys.setrecursionlimit(25)
```

and the array loop only served to force:

```python
import array
```

### Release-build impact

This is not just an over-strict debug assertion. The assertion is catching a
real reference ownership bug: `state->ArrayType` is decrefed while the module
state still traverses and later clears the same non-NULL pointer.

In a release build, the missing assertion would allow GC accounting to continue
with an extra non-owned edge. At minimum that can misclassify the partially
initialized `array.array` type during cyclic GC; later cleanup can also decref
the stale state slot again. I did not verify a release-build crash for this
specific reproducer, but the invariant violation is a real refcount/GC bug.

### Possible fix direction

The error paths after `state->ArrayType` is stored in module state should keep
module-state ownership consistent.

The simplest direction is to remove the two manual:

```c
Py_DECREF((PyObject *)state->ArrayType);
```

calls in the `mutablesequence` and `res` failure paths and let normal module
cleanup clear the state fields. Alternatively, those paths need to clear all
state slots whose references they release, but that is easier to get wrong and
duplicates the module clear logic.

The same pattern is worth checking in other multi-phase extension modules:
after a heap type is stored in module state, failure cleanup should not decref
that state-owned type without also nulling the state slot.

### Regression test

A subprocess test is probably the right shape so the test can assert "no fatal
abort" even if the exact Python-level outcome depends on how much recursion the
import path needs:

```python
code = "import sys; sys.setrecursionlimit(25); import array"
rc, out, err = assert_python_failure("-c", code)
self.assertNotIn(b"Fatal Python error", err)
self.assertNotIn(b"refcount is too small", err)
```

If the fixed build imports `array` successfully at that recursion limit, the
test can instead use `assert_python_ok("-c", code)`.

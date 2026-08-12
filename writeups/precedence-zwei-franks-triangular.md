# Crash report: `bytearray.__init__()` asserts after integer construction

## Summary

Calling `bytearray.__init__()` on a non-empty bytearray can leave the object in
a state that violates the new `ob_bytes_object` invariant.  In a debug build,
this aborts immediately:

```python
bytearray((1)).__init__()
```

Observed stderr:

```text
python3: Objects/bytearrayobject.c:937:
int bytearray___init___impl(...):
Assertion `self->ob_bytes_object == Py_GetConstantBorrowed(Py_CONSTANT_EMPTY_BYTES)' failed.
Aborted
```

The same signature appears in the `precedence-zwei-franks-triangular` core
artifact and in duplicate inputs such as `cancun-seatbelt-venetian-dice`,
`positioned-decreasing-admitted-misled`, `wrought-positioned-presley-overly`,
`bestow-headset-sowing-trickster`, and `thirty-tonight-contingency-brava`.

## What is going on

`bytearray((1))` creates a one-byte bytearray by going through the integer
constructor path.  That path eventually stores a one-byte `bytes` object in the
bytearray's new `ob_bytes_object` backing field.

The explicit `.__init__()` call then re-enters `bytearray___init___impl()` with
no arguments.  At the start of `bytearray___init___impl()`, non-empty previous
contents are cleared with:

```c
if (Py_SIZE(self) != 0) {
    if (PyByteArray_Resize((PyObject *)self, 0) < 0)
        return -1;
}
```

Immediately afterwards, the implementation expects resize-to-zero to have
restored the canonical empty bytes backing object:

```c
assert(self->ob_bytes_object == Py_GetConstantBorrowed(Py_CONSTANT_EMPTY_BYTES));
assert(self->ob_exports == 0);
```

That expectation is not true for a one-byte bytearray.  In
`bytearray_resize_lock_held()`, shrinking from size 1 to size 0 enters the
"current buffer is large enough" branch.  Because `alloc / 2` is also zero, the
major-downsize test is false:

```c
if (size + logical_offset <= alloc) {
    if (size < alloc / 2) {
        alloc = size;
    }
    else {
        Py_SET_SIZE(self, size);
        PyByteArray_AS_STRING(self)[size] = '\0';
        return 0;
    }
}
```

So `PyByteArray_Resize(self, 0)` returns after only setting `Py_SIZE(self)` to
zero and writing a terminator byte.  It never reaches:

```c
_PyBytes_Resize(&obj->ob_bytes_object, alloc);
```

If `_PyBytes_Resize()` were called with `newsize == 0`, it would replace the
backing bytes object with `bytes_get_empty()`.  The early return bypasses that
cleanup, leaving `ob_bytes_object` pointing at the previous one-byte allocation.

## Why this matters

This is reachable through public Python code and does not require subclassing,
private APIs, bad C extensions, or memory pressure.  The immediate observed
failure is debug-only because it is an `assert()`, but the assert documents a
real internal invariant that the new bytearray storage code relies on:
`bytearray.__init__()` expects a cleared bytearray to be backed by the canonical
empty bytes object before it starts processing the new initializer argument.

The release-build symptom for the zero-argument reproducer may be only an
over-retained backing allocation, because the object is logically empty after
`Py_SET_SIZE(self, 0)`.  The underlying bug is still that the resize helper's
fast path does not provide the empty-state normalization its caller requires.

## Relevant stack

```text
frame #5: bytearray___init___impl(...) at Objects/bytearrayobject.c:937
frame #6: bytearray___init__(...) at bytearrayobject.c.h:102
frame #7: wrap_init(...) at typeobject.c:10400
frame #8: wrapperdescr_raw_call(...) at descrobject.c:523
frame #9: wrapperdescr_call(...) at descrobject.c:570
```

## Possible fix direction

Make resizing a bytearray to zero always normalize `ob_bytes_object` to the
empty bytes singleton.  That could be done by special-casing
`requested_size == 0` before the minor-downsize fast path, or by adjusting the
fast path so size-zero shrinks do not return before `_PyBytes_Resize()` has a
chance to install `bytes_get_empty()`.

The regression test should exercise explicit reinitialization of a non-empty
bytearray, including the one-byte case:

```python
b = bytearray(1)
b.__init__()
self.assertEqual(b, bytearray())
```

# `numpy.take` crashes a debug CPython on an invalid integer `mode`

`numpy.take(..., mode=<invalid integer>)` leaves a `ValueError` set while
reporting successful argument conversion.  This aborts a debug CPython and
causes a `SystemError` rather than the documented `ValueError` in a release
build.

## Reproducer

```python
import numpy as np

np.take(np.arange(1), [0], mode=3)
```

`mode` accepts the integer values of `NPY_CLIP`, `NPY_WRAP`, and `NPY_RAISE`
(currently 0, 1, and 2), or the strings `"clip"`, `"wrap"`, and `"raise"`.
An invalid integer should raise `ValueError` immediately.  Instead, a debug
CPython aborts with:

```text
Fatal Python error: _Py_CheckSlotResult: Slot __len__ of type list succeeded with an exception set
ValueError: integer clipmode must be RAISE, WRAP, or CLIP from 'numpy._core.multiarray'
```

The CPython assertion is diagnostic: NumPy has already violated the C-API
contract by continuing to call Python APIs with an exception pending.  In a
release CPython the assertion is compiled out; when the extension later
returns a non-NULL result with that exception still pending, CPython converts
the failure into `SystemError` instead of exposing the intended `ValueError`.

## Root cause

`ndarray.take` parses `mode` with `PyArray_ClipmodeConverter` in
`numpy/_core/src/multiarray/methods.c`:

```c
if (npy_parse_arguments("take", args, len_args, kwnames,
        {"indices", NULL, &indices},
        {"|axis", &PyArray_AxisConverter, &dimension},
        {"|out", &PyArray_OutputConverter, &out},
        {"|mode", &PyArray_ClipmodeConverter, &mode}) < 0) {
    return NULL;
}
```

The converter correctly rejects an integer outside the `NPY_CLIP` through
`NPY_RAISE` range, but its error path only sets `ValueError`:

```c
else {
    PyErr_Format(PyExc_ValueError,
            "integer clipmode must be RAISE, WRAP, or CLIP "
            "from 'numpy._core.multiarray'");
}
return NPY_SUCCEED;
```

Thus the argument parser accepts `mode=3` and `array_take` calls
`PyArray_TakeFrom` with the exception pending.  `PyArray_TakeFrom` converts
the Python list of indices with `PyArray_FromAny`; its dtype/shape discovery
calls `PySequence_Size(indices)`.  The list length slot returns `2`, but the
pre-existing `ValueError` is still pending.  Debug CPython checks this slot
contract in `PyObject_Size` and aborts because a successful `__len__` call may
not leave an exception set.

The error branch must return `NPY_FAIL` after setting the `ValueError` (or
otherwise jump to a failure return).  This shared converter is also used by
`ndarray.put`, `ndarray.choose`, and `ravel_multi_index`; those callers need
regression coverage as well.  The existing `run_clipmode_converter` test
helper provides a direct unit-test boundary.

## Corroborating cores

All nine core files in this project have the same stop reason and relevant
stack:

```text
PySequence_Size(list)
PyArray_DiscoverDTypeAndShape_Recursive
PyArray_FromAny
PyArray_TakeFrom
array_take
```

Their linked inputs differ only in the invalid integer supplied as `mode`
(`-1`, `3`, `10`, `255`, `256`, and larger values).  Each records the same
pending `ValueError` and `_Py_CheckSlotResult` abort.  Representative stack:

<details>
<summary>Debug stack from the representative core</summary>

```
_Py_CheckSlotResult(obj=list, slot_name="__len__", success=1)
PySequence_Size(s=list) at Objects/abstract.c
PyArray_DiscoverDTypeAndShape_Recursive
PyArray_DiscoverDTypeAndShape
PyArray_FromAny_int
PyArray_FromAny
PyArray_TakeFrom
array_take
```

</details>

The affected NumPy build identifies itself as
`2.6.0.dev0+git20260713.9559a6b`.

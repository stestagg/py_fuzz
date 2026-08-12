# Crash report: `generator.throw(StopIteration)` trips a debug assert

### What happened?

This aborts a debug build:

```python
(_ for _ in ()).throw(StopIteration)
```

A caught form aborts before the exception can be handled:

```python
try:
    (_ for _ in ()).throw(StopIteration)
except StopIteration:
    pass
```

Observed stderr:

```text
python3: Objects/genobject.c:318: PySendResult gen_send_ex2(PyGenObject *, PyObject *, PyObject **, int): Assertion `!PyErr_ExceptionMatches(PyExc_StopIteration)' failed.
Aborted
```

### Expected behavior

`generator.throw()` raises the supplied exception inside the generator. If the generator does not catch that exception, the exception propagates to the caller.

For a newly-created generator, this `StopIteration` is the caller-supplied exception from `throw()`, not a `StopIteration` raised by generator body code. It should be possible for the caller to catch it.

### What is going on

The crash is an over-broad invariant in `gen_send_ex2()`.

1. `generator.throw(StopIteration)` enters `gen_throw()` / `_gen_throw()`.

2. `_gen_throw()` normalizes and installs the exception with `gen_set_exception()`.

3. `_gen_throw()` then calls `gen_throw_current_exception()`, which resumes the generator through:

   ```c
   gen_send_ex2(gen, Py_None, &result, 1)
   ```

   The final `1` is the `exc` flag: this is the path for an exception injected by `throw()`.

4. The generator is newly-created. The injected `StopIteration` is not caught by generator code, so `_PyEval_EvalFrame()` returns `NULL` with `StopIteration` still set.

5. That is a valid error return for `throw()`: return `NULL`, leave the Python exception set, and let it propagate to the caller.

6. `gen_send_ex2()` then reaches this block:

   ```c
   else {
       assert(!PyErr_ExceptionMatches(PyExc_StopIteration));
       assert(!PyAsyncGen_CheckExact(gen) ||
           !PyErr_ExceptionMatches(PyExc_StopAsyncIteration));
   }
   ```

   The first assertion assumes that any `NULL` return with `StopIteration` set must be an escaped generator-raised `StopIteration`. That is not true for this `throw()` path: the exception can be the caller-supplied exception.

### Relevant code

The failing assertion is in `Objects/genobject.c`:

```c
if (result) {
    assert(result == Py_None || !PyAsyncGen_CheckExact(gen));
    if (result == Py_None && !PyAsyncGen_CheckExact(gen) && !arg) {
        /* Return NULL if called by gen_iternext() */
        Py_CLEAR(result);
    }
}
else {
    assert(!PyErr_ExceptionMatches(PyExc_StopIteration));
    assert(!PyAsyncGen_CheckExact(gen) ||
        !PyErr_ExceptionMatches(PyExc_StopAsyncIteration));
}

*presult = result;
return result ? PYGEN_RETURN : PYGEN_ERROR;
```

The call chain that makes `StopIteration` legitimate here is also in `Objects/genobject.c`:

```c
static PyObject *
gen_throw_current_exception(PyGenObject *gen)
{
    ...
    if (gen_send_ex2(gen, Py_None, &result, 1) == PYGEN_RETURN) {
        return gen_set_stop_iteration(gen, result);
    }
    return result;
}
```

and:

```c
throw_here:
    ...
    if (gen_set_exception(typ, val, tb) < 0) {
        ...
        return NULL;
    }
    return gen_throw_current_exception(gen);
```

### Why this is not the normal PEP 479 case

The usual invariant is sound for normal generator execution: if generator body code raises `StopIteration`, the compiler-inserted cleanup handler converts it to `RuntimeError("generator raised StopIteration")`.

For example, throwing `StopIteration` into a generator already paused at a `yield` is handled by that machinery:

```python
def g():
    yield 1

it = g()
next(it)
it.throw(StopIteration)
```

That raises:

```text
RuntimeError: generator raised StopIteration
```

and does not hit the assertion.

The reproducer is different because the generator has not yet suspended at a `yield`. The `StopIteration` is the exception supplied by the caller to `throw()`, and it is allowed to propagate.

### Release-build impact

This appears to be a debug/assert-enabled crash for this specific path.

With `assert()` compiled out, `gen_send_ex2()` falls through to:

```c
*presult = result;  // result is NULL
return result ? PYGEN_RETURN : PYGEN_ERROR;
```

That returns `PYGEN_ERROR` with `StopIteration` still set, which is the normal C API convention for propagating an exception.

So for this reproducer, I do not see an invalid result being returned with an exception set, a leaked invalid object, or obvious release-build memory-safety impact. The non-debug behavior should simply be Python-level `StopIteration` propagation.

The caveat is that a different path reaching the same assertion could matter. If `StopIteration` reached this point because generator body code escaped the PEP 479 handler, that would be a separate release-build semantic bug. This reproducer does not show that path.

### Possible fix direction

The assertion should distinguish ordinary generator execution from throw-injected exceptions that are allowed to escape. The blanket:

```c
assert(!PyErr_ExceptionMatches(PyExc_StopIteration));
```

is too strong when `gen_send_ex2()` is called with `exc=1`.

The adjacent `StopAsyncIteration` assertion for async generators has the same shape and should probably be audited under the same rule.

### Regression test

```python
def test_throw_stopiteration_into_unstarted_generator(self):
    g = (_ for _ in ())
    with self.assertRaises(StopIteration):
        g.throw(StopIteration)
```

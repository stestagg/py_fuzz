# Crash report: deleting `sqlite3.Cursor.row_factory` leaves a NULL callable

### What happened?

This aborts a debug build:

```python
import sqlite3

cur = sqlite3.connect(":memory:").cursor()
del cur.row_factory
cur.execute("select 1").fetchone()
```

Observed debug-build stderr:

```text
python3: ./Include/internal/pycore_call.h:93: vectorcallfunc _PyVectorcall_FunctionInline(PyObject *): Assertion `callable != NULL' failed.
Aborted
```

The original fuzzer input had an unnecessary connection-level row factory, but
reduces to the same bug:

```python
import sqlite3

c = sqlite3.connect(":memory:")
c.row_factory = lambda *a: a
cur = c.cursor()
del cur.row_factory
cur.execute("select 1").fetchone()
```

### Expected behavior

`del cur.row_factory` should not leave the cursor in a crashable internal state.

The sqlite3 docs already say that deleting `Cursor.row_factory` is no longer
allowed. `Connection.row_factory` follows that rule today:

```python
>>> import sqlite3
>>> con = sqlite3.connect(":memory:")
>>> del con.row_factory
Traceback (most recent call last):
...
AttributeError: cannot delete row_factory attribute
```

`Cursor.row_factory` should probably raise the same `AttributeError`. Assigning
`None` should remain valid:

```python
cur.row_factory = None
cur.execute("select 1").fetchone()  # (1,)
```

### Artifact shape

In the `prs-7` fuzzing project, this is one signature repeated many times.

The project was a debug build (`py_debug: true`) with input tracking enabled.
`pfx summary` reported 17 saved crashes and 28 core dumps; the imported artifact
set currently contains 11 crash-input artifacts and 21 core artifacts.

All 11 crash artifacts have the same `input_clean.txt` hash:

```text
6068c21f4196cb2a4bcbe151d5e55457753547663a0e1c0ce6c52467c377509e
```

All 32 imported artifacts have the same `last_input.txt` hash, also
`6068c21f...`, and that input is the `del cur.row_factory` program above.

All 21 core artifacts match the same LLDB signature:

```text
_PyVectorcall_FunctionInline(callable=<unavailable>) at pycore_call.h:93
_PyObject_VectorcallTstate(...)
PyObject_Vectorcall(...)
pysqlite_cursor_iternext(...) at Modules/_sqlite/cursor.c:1182
pysqlite_cursor_fetchone(...)
```

The linked crash/core pairs and the unlinked `pid 76` cores differ in worker,
input count, and recorded harness phase (`module_cleanup` vs `gc_collect`), but
the stopped C stack and final input are the same. `pfx tracks reproducer --all`
generated history scripts whose final input is the same row-factory deletion;
the long input history is not required because the minimized standalone script
reproduces the abort.

### What is going on

The bug is an invalid internal state created by the cursor attribute descriptor.

1. Cursor initialization sets the C slot to `Py_None`:

   ```c
   Py_INCREF(Py_None);
   Py_XSETREF(self->row_factory, Py_None);
   ```

2. `Cursor.row_factory` is exposed as a writable `_Py_T_OBJECT` member:

   ```c
   static struct PyMemberDef cursor_members[] =
   {
       ...
       {"row_factory", _Py_T_OBJECT, offsetof(pysqlite_Cursor, row_factory), 0},
       ...
   };
   ```

3. `_Py_T_OBJECT` member assignment accepts deletion. In `PyMember_SetOne()`,
   `del cur.row_factory` arrives as `v == NULL` and stores `NULL` into the C
   slot:

   ```c
   case _Py_T_OBJECT:
   case Py_T_OBJECT_EX:
       oldv = *(PyObject **)addr;
       FT_ATOMIC_STORE_PTR_RELEASE(*(PyObject **)addr, Py_XNewRef(v));
       ...
   ```

   Reads then mask the problem: `_Py_T_OBJECT` returns `None` when the slot is
   `NULL`, so `cur.row_factory` appears to be `None` after deletion.

4. Fetching a row does not go through the member getter. It reads the C slot
   directly:

   ```c
   if (!Py_IsNone(self->row_factory)) {
       PyObject *factory = self->row_factory;
       PyObject *args[] = { op, row, };
       PyObject *new_row = PyObject_Vectorcall(factory, args, 2, NULL);
       Py_SETREF(row, new_row);
   }
   ```

5. `Py_IsNone(NULL)` is false, so the code treats the NULL slot as a custom row
   factory and calls `PyObject_Vectorcall(NULL, ...)`.

6. In a debug build, `_PyVectorcall_FunctionInline()` aborts on:

   ```c
   assert(callable != NULL);
   ```

### Why `Connection.row_factory` does not have the same bug

`Connection.row_factory` uses a get/set descriptor instead of a raw writable
member. Its setter rejects deletion:

```c
static int
connection_set_row_factory(PyObject *op, PyObject *value, void *closure)
{
    pysqlite_Connection *self = (pysqlite_Connection *)op;
    if (value == NULL) {
        PyErr_SetString(PyExc_AttributeError,
                        "cannot delete row_factory attribute");
        return -1;
    }
    Py_XSETREF(self->row_factory, Py_NewRef(value));
    return 0;
}
```

The regression tests currently cover `Connection.row_factory` and
`Connection.text_factory` deletion, but not `Cursor.row_factory` deletion. The
docs cover both connection and cursor `row_factory`, so the cursor
implementation and tests are the missing pieces.

### Release-build impact

This is not just a debug-only assertion.

With assertions compiled out, `_PyVectorcall_FunctionInline(NULL)` proceeds to
use `Py_TYPE(callable)`, which dereferences the NULL pointer. As a quick
corroborating check, the same minimized snippet under a normal `uv` runtime:

```text
Python 3.14.4 (main, Apr 14 2026, 14:46:33) [Clang 22.1.3]
```

exited with status 139 (`SIGSEGV`).

So the debug build reports the problem as an assertion failure, while release
builds can segfault.

### Possible fix direction

`Cursor.row_factory` should be changed from a raw writable `_Py_T_OBJECT` member
to a get/set descriptor that mirrors `Connection.row_factory`:

- getter returns a new reference to `self->row_factory`
- setter rejects `value == NULL` with `AttributeError`
- setter stores `Py_NewRef(value)` for normal assignment, including `None`

Adding a defensive NULL check in `pysqlite_cursor_iternext()` would avoid the
immediate crash, but it would leave `del cur.row_factory` contrary to the docs
and would preserve the hidden NULL state.

### Regression test

```python
def test_delete_cursor_row_factory(self):
    cur = self.con.cursor()
    with self.assertRaises(AttributeError):
        del cur.row_factory
```

It would also be useful to keep the assignment case explicit:

```python
def test_cursor_row_factory_none(self):
    cur = self.con.cursor()
    cur.row_factory = None
    self.assertEqual(cur.execute("select 1").fetchone(), (1,))
```

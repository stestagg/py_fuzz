# prs-9 core artifact analysis

## Short answer

I found one core artifact that does not look like a kernel stack-growth failure:

- `ingenuity-provenza-wallop-hydraulic`
  - linked crash artifact: `blindfold-unpaid-club-tenure`
  - worker: `w1`
  - pid: `7899`
  - tracked inputs run: `191`

The other 47 core artifacts in `prs-9` all classify as likely stack allocation
or stack-growth failures. They have fault addresses at or very near the stack
pointer, often in the unmapped hole just below the stack mapping, and/or very
deep recursive parser/evaluator/import backtraces.

## Non-stack issue: `_PyDict_NewKeysForClass()` uses `keys == NULL`

The outlier core stops at a near-null dereference, not a stack-near fault:

```text
_DK_ENTRIES(dk=<unavailable>) at pycore_dict.h:269
SIGSEGV: address not mapped to object (fault address=0x9)
```

The top frames are:

```text
frame #0  _DK_ENTRIES(dk=<unavailable>) at pycore_dict.h:269
frame #1  do_lookup(mp=0x0, dk=0x0, ...) at dictobject.c:1090
frame #2  unicodekeys_lookup_unicode(dk=0x0, ...) at dictobject.c:1183
frame #3  insert_split_key(keys=0x0, ...) at dictobject.c:1931
frame #4  _PyDict_NewKeysForClass(cls=0x0000aaaaabfffa70) at dictobject.c:7236
frame #5  type_ready_managed_dict(type=0x0000aaaaabfffa70) at typeobject.c:9473
frame #6  type_ready(...) at typeobject.c:9578
frame #7  PyType_Ready(...) at typeobject.c:9622
```

The source path matches the crash. In
`projects/prs-9/cpython/Objects/dictobject.c`, `_PyDict_NewKeysForClass()`
allows `new_keys_object()` to fail and clears the `MemoryError`:

```c
PyDictKeysObject *keys = new_keys_object(NEXT_LOG2_SHARED_KEYS_MAX_SIZE, 1);
if (keys == NULL) {
    PyErr_Clear();
}
else {
    ...
}
if (cls->ht_type.tp_dict) {
    PyObject *attrs = PyDict_GetItem(cls->ht_type.tp_dict, &_Py_ID(__static_attributes__));
    if (attrs != NULL && PyTuple_Check(attrs)) {
        ...
        if (insert_split_key(keys, key, hash) == DKIX_EMPTY) {
            break;
        }
    }
}
return keys;
```

If the class dict has a `__static_attributes__` tuple and the shared-keys
allocation fails, `keys` remains `NULL` but is still passed to
`insert_split_key()`. That reaches `unicodekeys_lookup_unicode(NULL, ...)`,
then `_DK_ENTRIES(NULL)`, and faults at `0x9`.

This is different from the stack-allocation artifacts: the fault address is a
small near-null address, and the stack classifier records a veto:

```text
-fault address 0x9 is near-null and far from the stack pointer - looks like a
 NULL/small-offset dereference, not a stack-growth fault
```

## Artifact shape

The linked crash input alone does not reproduce:

```text
blindfold-unpaid-club-tenure/lldb.txt:
process exited cleanly with code 1 - no crash detected
```

That fits the core state. The crash happened in a persistent process after 191
tracked inputs, with many live threads and exception printing/import machinery
active. The harness stderr contains thread exceptions followed by:

```text
Exception ignored in the internal traceback machinery:
```

The core backtrace shows the main thread importing `traceback` while other
threads are in `thread_excepthook_file()` / traceback printing paths. The last
input is just the final trigger from a longer history:

```python
import threading, time
c=threading.Condition()
def f():
    with c:
        c.wait()
for _ in range(4):
    threeeeeeeeeeeeeeading.Thread(target=f).start()
time.sleep(0.01)
with c:
    c.notify_all()
```

## Targeted repro shape

The huge persistent-thread history seems to be incidental pressure that gets
the process into a low-memory class-creation path while traceback is imported.
A targeted fault-injection reproducer should be enough for the underlying bug:

```python
import pfalloc

pfalloc.enable()
pfalloc.set_counter(1, 768)

class C:
    __static_attributes__ = ("x",)
```

I saved this as:

```text
projects/prs-9/scratch/reproducers/dict-keys-null-repro.py
```

I also saved a `_testcapi.set_nomemory()` version that searches for the failing
allocation index one allocation at a time:

```text
projects/prs-9/scratch/reproducers/dict-keys-null-testcapi-repro.py
```

That script uses `set_nomemory(start, start + 1)` so each loop iteration fails
only one allocation before removing the hooks and trying the next index. It
uses a method that assigns `self.x` so the compiler creates a non-empty
`__static_attributes__` tuple; explicitly writing `__static_attributes__ =
("x",)` in the class body is not sufficient, because this build replaces that
with an empty tuple.

The `768` byte filter is the expected allocation size for
`new_keys_object(NEXT_LOG2_SHARED_KEYS_MAX_SIZE, unicode=1)` in this non
free-threaded 64-bit build:

- `sizeof(PyDictKeysObject)` is 32 bytes.
- `DK_SIZE` is 64, so the index table is 64 bytes.
- `USABLE_FRACTION(64)` is 42.
- `sizeof(PyDictUnicodeEntry)` is 16 bytes.
- Total: `32 + 64 + 42 * 16 == 768`.

I could not run this repro in the project VM from this host because pfrun
reported:

```text
Virtualization is not available on this hardware.
```

Still, the core and source line up directly: `_PyDict_NewKeysForClass()` has a
missing `keys != NULL` guard before preloading `__static_attributes__`.

## Likely fix

Keep the existing "return NULL without an exception" behavior, but only preload
static attributes when `keys` was allocated:

```c
if (keys != NULL && cls->ht_type.tp_dict) {
    ...
}
```

or equivalently return immediately after clearing the allocation error.

`type_ready_managed_dict()` already handles `NULL` from
`_PyDict_NewKeysForClass()` by setting `PyErr_NoMemory()` and returning `-1`,
so the failure can be propagated cleanly instead of crashing.

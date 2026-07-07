## Writing up issues and root cause analysis

### Allowed Crashes

There are a number of cases where python is allowed to crash and not be considered a bug.  If you work out that
an issue is an example of one of these, stop investigating, and just output the one-line reason.
Known allowed crashes include:
 - Passing bad data to cPickle
 - Using ctypes to call a function or do any pointer-related operations (most crashes from ctypes that can't be attributed to a specific implementation fault are considered false positives)
 - Cases where the kernel cannot allocate a stack page due to memory pressure and thus triggering a stack overflow (in a way that the built-in stack guard cannot handle).
 - Increasing the recursion limit in a way that triggers a stack overflow or memory exhaustion (documented behaviour)
 - Throwing any signal or exiting with crash-related exit codes, calling abort etc..

Any of these cases (where confirmed) should stop all further investigation.


### Investigation detail

This fuzzing environment is very proprietary (open-source but unqiue) and specialized.
There are many tools to aid with debugging and root cause analysis (read ANALYSIS.md)
but very few people know about this environment, or what it does.

This means that once a bug is found, we use our custom tooling to thoroughly identify the root cause, typically this is:
1. Exactly which functions and lines of code are causing the bug, including the relecvant code interactions or any required state.
2. The exact conditions that trigger the bug, including any required state or inputs.
3. Typically when we get here, there is an indication of what one possible fix might be, this is a good sign that the analysis is complete.
4. If the analysis is 'run this code, then this other code' then you get this type of error, then this is **not** enough information about what is happening. even if the code examples have been minimized and seem obvious, we need the actual lines/clauses spelled out.
5. (**This can be hard to do but is very important**) Sometimes a bug surfaces as a debug assert, or other contrived/artifical error.  At this stage, we should very carefully, and sometimes creatively, consider if the root cause can be also triggered, or used from non-debug/artifical builds/codepaths.  An example of this (although minor, as both options are pure-python) would be the following reproducer for an issue:

```
import sqlite3
con = sqlite3.Connection.__new__(sqlite3.Connection)
print(con.row_factory)
```

This is fine as-is, but calling __new__ on a class is very uncommon.  With a bit of thought, we came up with the following example as well:

```
import sqlite3
class MyCon(sqlite3.Connection):
  def __init__(self, *a, **kw):
    if self.row_factory:
      pass
    super().__init__()
c = MyCon(':memory:')
```

Now, this code is something that is far more likely to be a real user scenario, with a real user impact (and no debug or non-public build options).  Getting to that next level of relevance in a bug report is very important where possible.

### Writing up

Once the investigation is complete, it's important to lift this information entirely out of the fuzzing environment, tooling and terminology, and frame the bug in isolation as a standalone problem that any experienced core cpython developer should be able to understand and verify.

Descriptions of bugs should be written in terse, concise, clear language that outlines the observed behaviour.  Don't dumb-down or skate over detail, but avoid any mentions of anything about the local reproduction environment or the fuzzing structure/terminology here.

An ideal report has:

1. a one-line summary of the root cause bug and it's real-world (where possible) observable impact.  Ideally one sentence up to 79 characters if possible.
2. A minimized, generic reproducer (for memory issues, _testcapi usage is fine if no alternative is possible), with a description of the observed behaviour (and the expected behaviour if it is not obvious to a core dev).
3. A detailed description of the code-level sequence of calls / interactions that lead up to this bug, start at the best boundary that allows some context to the issue without including unnecessary detail (i.e. )
An example of a detailed description is:
```
Early during the class build process, `__static_attributes__` is correctly populated with the list of detected static attributes.

Then later during PyType_Ready(): `type_ready_managed_dict` is called to make the shared key cache.
This calls `_PyDict_NewKeysForClass` and this line:
[ the line ]

Tries to allocate a keys object, and if that allocation fails, just clears the `MemoryError` and continues, skipping out some keys setup steps.

However, later on in that `_PyDict_NewKeysForClass` it fetches the `__static_attributes__` tuple and tries to populate them into the keys object:
[ the snippet ]

And `insert_split_key` reasonably assumes that `keys` is a valid Keys object, resulting in a relative pointer from NULL dereferenec.
```
4. A section with any relevant debugging stack frames, and/or lldb output or anything else.
Longer sections sould be wrapped in:
<details>
<summary>[summary of the content]</summary>

```
<the content>
```

</details>
```

Note the white space around the ``` is important!.
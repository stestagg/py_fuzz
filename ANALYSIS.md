# Crash Analysis Tooling

This document describes the local `./pfx` / `./pyfuzz` tooling that is relevant
when a fuzzing run leaves behind crash files, core files, and difficult
reproduction questions.

`./pfx` is a symlink to `./pyfuzz`. When invoked as `pfx`, the CLI loads the
project name from `.pyfuzz_project`; otherwise the same commands can be run as
`./pyfuzz --project NAME ...`. The examples below use `./pfx`.

## Artifact Model

The analysis tooling is centered on artifact directories:

```sh
projects/<project>/artifacts/<artifact-hash>/
```

Artifact hashes are stable, human-readable directory names. They are derived
from raw fuzzing outputs and are the identifiers accepted by most analysis
subcommands.

A crash artifact represents an AFL crash input copied out of:

```sh
projects/<project>/outputs/<worker>/crashes/
```

It can contain:

- `meta.json`: type, timestamp, worker, and source AFL filename
- `input.txt`: the copied crash input
- `lldb.txt`: LLDB output, if analysis has been run
- other named analysis outputs produced by `analyze script`

A core artifact represents a core file found under:

```sh
projects/<project>/cores/
```

It can contain:

- `meta.json`: type, pid, timestamp, and optional crash linkage
- `core`: a symlink to the raw core file
- `lldb.txt`: LLDB output, if analysis has been run
- other named analysis outputs produced by `analyze script`

The distinction matters because a crash artifact answers "what input was saved?"
while a core artifact answers "what state did the process actually stop in?"
Hard-to-reproduce failures often need both views.

## `analyze sync`

```sh
./pfx analyze sync
```

Creates artifact directories for raw crash inputs and core files that have not
yet been imported. The command scans the project `outputs/` and `cores/`
directories, writes `meta.json`, and either copies crash input bytes to
`input.txt` or symlinks a core file as `core`.

Relevant output:

- New directories under `projects/<project>/artifacts/`
- Crash metadata with `worker`, `source_filename`, and `timestamp`
- Core metadata with parsed `pid` and `timestamp` when the core name matches
  `core.<pid>.<timestamp>`

Example command shapes:

```sh
./pfx analyze sync
./pfx analyze query type:crash
./pfx analyze query type:core
```

Relevance to reproduction: this is the bridge from raw fuzzer residue to the
stable artifact names consumed by the rest of the tooling.

## `analyze link-core`

```sh
./pfx analyze link-core
```

Associates core artifacts with nearby crash artifacts. A core is linked to the
closest crash whose timestamp is within one second. The core receives a
`linked_crash` field in `meta.json`; the crash receives a `linked_core` field.

Relevant output:

- `linked_crash` in core artifact metadata
- `linked_core` in crash artifact metadata

Example command shapes:

```sh
./pfx analyze link-core
./pfx analyze query type:core
./pfx analyze query type:crash
```

Relevance to reproduction: a single AFL input may not recreate the failure in a
fresh process, while the linked core can still preserve the state of the
persistent worker that crashed.

## `analyze lldb`

```sh
./pfx analyze lldb <artifact-hash>
./pfx analyze lldb <artifact-hash> --output <path>
./pfx analyze lldb <artifact-hash> --interactive
./pfx analyze lldb <path/to/script.py> --output <path>
./pfx analyze lldb <path/to/script.py> --interactive
```

Runs LLDB-backed analysis inside the project's LLDB VM environment. The first
positional argument is interpreted as an artifact hash when it contains no `/`,
or as a local Python script path when it does.

**Hash mode** loads a saved core or replays a crash input against the project
fuzz target, exactly as before. Output defaults to:

```sh
projects/<project>/artifacts/<artifact-hash>/lldb.txt
```

**Script mode** copies the script into `projects/<project>/scratch/lldb/` (if
not already inside `scratch/`), then feeds it to the fuzz target as a crash
input in LLDB. The `--output` option is required in non-interactive script mode.

In both modes, `--output PATH` overrides the VM output path. `PATH` is relative
to `/pfm/` inside the VM — for example `scratch/lldb/out.txt` writes to
`projects/<project>/scratch/lldb/out.txt` on the host.

The default LLDB command set is:

- `thread list`
- `bt all`
- `register read`

The project's fuzz memory limit is applied to crash and script inputs unless
ASAN disables that limit.

Relevant output:

- Stop reason and thread list
- Full backtraces
- Register state
- For crash/script inputs, explicit clean-exit or timeout messages when the
  input does not recreate a crash in that LLDB launch

Example command shapes:

```sh
./pfx analyze lldb <crash-hash>
./pfx analyze lldb <core-hash>
./pfx analyze lldb <crash-hash> --interactive
./pfx analyze lldb path/to/repro.py --output scratch/lldb/repro.txt
./pfx analyze lldb projects/<project>/scratch/reproducers/repro-1.py --output scratch/lldb/repro-1.txt
./pfx analyze lldb path/to/repro.py --interactive
./pfx analyze query file:lldb.txt re:lldb.txt:"process exited cleanly"
./pfx analyze query file:lldb.txt re:lldb.txt:"SIGSEGV|SIGBUS|Py_FatalError"
```

Relevance to reproduction: it separates inputs that reproduce as standalone
crashes from artifacts that only make sense through the original core or a
longer input history. Script mode lets an arbitrary reproducer be run in the
same fuzz environment without first registering it as an artifact.

## `analyze query CLAUSE...`

```sh
./pfx analyze query <clause> [<clause> ...]
```

Filters artifact directories and prints matching artifact hashes. Clauses are
combined with logical AND.

Available clauses:

- `type:<crash|core>`: match artifact type
- `file:<filename>`: match artifacts containing a named file
- `meta:<key>=<value>`: match an exact metadata value
- `re:<filename>:<pattern>`: match a regex inside an artifact file
- `no:<clause>`: invert another clause

Relevant output:

- Artifact hashes matching the requested structural, metadata, or text filters

Example command shapes:

```sh
./pfx analyze query type:crash no:file:lldb.txt
./pfx analyze query type:core file:lldb.txt
./pfx analyze query type:crash meta:worker=w3
./pfx analyze query file:lldb.txt re:lldb.txt:"_PyObject_Free|Py_DECREF"
./pfx analyze query type:crash file:lldb.txt no:re:lldb.txt:"process exited cleanly"
```

Relevance to reproduction: it provides coarse classification over a large set
of artifacts without opening each directory by hand. Regex queries over LLDB
outputs are especially useful for grouping likely duplicate signatures.

## `analyze script OUT_NAME BATCH_FILE ARTIFACTS...`

```sh
./pfx analyze script <out-name> <lldb-command-file> <artifact-hash>...
```

Runs a custom LLDB command file against one or more artifacts. For each
artifact, it copies the command file to:

```sh
projects/<project>/artifacts/<artifact-hash>/<out-name>.cmds
```

and writes command output to:

```sh
projects/<project>/artifacts/<artifact-hash>/<out-name>.txt
```

The checked-in `lldb-commands.txt` is an example batch aimed at inspecting
register-derived `PyObject` state.

Relevant output:

- A preserved copy of the LLDB command batch used for that artifact
- A named LLDB output file that can be queried later with `analyze query`

Example command shapes:

```sh
./pfx analyze script objcheck lldb-commands.txt <hash-a> <hash-b> <hash-c>
./pfx analyze query file:objcheck.txt re:objcheck.txt:"memory region|ob_refcnt|ob_type"
```

Relevance to reproduction: it makes ad hoc LLDB checks repeatable across a set
of artifacts, so a suspicious object layout, register value, or memory region
can be compared across related crashes and cores.

## `track-script`

```sh
./pfx track-script <worker-id> <pid-timestamp> <out-path>
./pfx track-script --all=<base-name>
```

Builds a Python replay script from recorded per-iteration inputs. This command
is outside the `analyze` group, but it is directly relevant to crash
reproduction when `track_inputs` was enabled for the fuzzing project.

The command reads:

```sh
projects/<project>/input_tracks/<worker-id>/<pid-timestamp>/
```

and emits a Python script containing:

- One `exec(compile(...))` block per recorded input
- `FUZZ_MARKER` comments before input, reset, and GC sections
- Periodic `sys.modules` cleanup mirroring the harness cadence
- Periodic `gc.collect()` calls mirroring the harness cadence

With `--all=<base-name>`, it scans core artifacts with `lldb.txt`, extracts the
stopped PID from LLDB output, finds matching input tracks, and writes scripts to:

```sh
projects/<project>/scratch/reproducers/
```

Relevant output:

- Reproducer scripts such as `projects/<project>/scratch/reproducers/repro-1.py`
- Marked sections that can later be minimized or inspected

Example command shapes:

```sh
./pfx track-script --all=repro
./pfx track-script w4 1332.1778668698 projects/<project>/scratch/reproducers/repro-1332.py
```

Relevance to reproduction: it captures persistent-process history rather than
only the final AFL input. That matters when the crash depends on imports,
module cache state, mutated builtins, GC timing, or earlier inputs.

## `run-cmd` and `shell`

```sh
./pfx run-cmd --image lldb <cmd> [args...]
./pfx shell --image lldb
./pfx shell --image lldb -c '<cmd>'
```

Runs commands inside one of the project environments. For analysis work, the
`lldb` image mounts the built Python, helpers, inputs, logs, cores, artifacts,
config, and input tracks in the same `/pfm/...` layout used by the analysis
commands.

Relevant environment paths:

- `/pfm/py`: built Python tree
- `/pfm/tools`: fuzz targets and helper binaries
- `/pfm/helpers`: helper scripts
- `/pfm/config`: project configuration
- `/pfm/scratch`: general-purpose read-write workspace (subdirs: `lldb/`, `reproducers/`, `dist/`, `bisect/`)
- `/pfm/artifacts`: analysis artifacts
- `/pfm/cores`: raw cores
- `/pfm/input_tracks`: tracked per-iteration inputs, when present

Example command shapes:

```sh
./pfx run-cmd --image lldb /pfm/py/bin/python3 /pfm/scratch/reproducers/repro-1.py
./pfx run-cmd --image lldb /pfm/py/bin/python3 /pfm/helpers/minimize_crash.py /pfm/scratch/reproducers/repro-1.py
./pfx shell --image lldb
```

Relevance to reproduction: it exposes the same mounted project environment as
the canned analysis commands while leaving the invoked command open-ended.

## `run-dist SCRIPT`

```sh
./pfx run-dist <script.py>
./pfx run-dist <script.py> --ref <cpython-ref>
./pfx run-dist <script.py> --debug
./pfx run-dist <script.py> --env KEY=VALUE
./pfx run-dist <script.py> --configure-args '<extra configure args>'
./pfx run-dist <script.py> --interactive
```

Builds or reuses a more standard CPython build, then runs a supplied Python
script against it. The command copies the script into:

```sh
projects/<project>/dist_script/
```

and uses cached CPython installs under:

```sh
cache/dist-builds/
```

The build is keyed by the resolved CPython commit, whether `--debug` is set,
and the configure arguments. `--ref` selects the CPython ref to build, defaulting
to `main`. `--debug` adds `--with-pydebug`. `--env KEY=VALUE` passes script
environment variables through to the run. `--interactive` prepares or reuses the
build, prints the Python command it would run, and opens a shell with useful
environment variables such as `PYTHON`, `DIST_BUILD_ROOT`, `DIST_SCRIPT`, and
`DIST_COMMAND`.

Relevant output:

- A copied script under `projects/<project>/scratch/dist/`
- A cached CPython install under `cache/dist-builds/`
- The resolved CPython commit and build-cache path
- The script's exit status when run under the built Python

Example command shapes:

```sh
./pfx run-dist projects/<project>/scratch/reproducers/repro-1.py
./pfx run-dist projects/<project>/scratch/reproducers/repro-1.py --ref v3.14.0a7
./pfx run-dist projects/<project>/scratch/reproducers/repro-1.py --debug
./pfx run-dist projects/<project>/scratch/reproducers/repro-1.py --env PYTHONMALLOC=debug
./pfx run-dist projects/<project>/scratch/reproducers/repro-1.py --interactive
```

Relevance to reproduction: this is the verification tool for reproducers. It
checks whether a script that was derived from fuzzing artifacts still fails
against a more conventional CPython build, rather than only inside the fuzzing
harness, LLDB image, or persistent AFL environment.

## Allocation Fault Injection (`pfalloc`)

The `pf-allocator.diff` tactical patch adds a `pfalloc` built-in module that
wraps all three CPython allocator domains (`RAW`, `MEM`, `OBJ`) with a
countdown-based fault injector. When installed in a patched build, it lets you
make the Nth allocation of a given size fail on demand.

### API

```python
import pfalloc

pfalloc.enable()               # install the hook (idempotent)
pfalloc.set_counter(n, size=0) # arm: Nth allocation of `size` bytes returns NULL
                               # size=0 matches any allocation size
pfalloc.disable()              # remove the hook (fails if another hook layered on top)
```

The counter decrements on every `malloc`, `calloc`, or growing `realloc` whose
size matches the filter. The allocation that brings the counter to zero is the
one that fails. After firing, `alloc_size` is reset to 0 so subsequent
allocations pass through normally. Setting `n=0` disables fault injection
without removing the hook.

### Typical usage pattern

```python
import pfalloc

pfalloc.enable()
pfalloc.set_counter(3)          # third allocation of any size will fail
result = some_operation()       # exercise the code path under test
pfalloc.set_counter(0)          # disarm before disable
pfalloc.disable()
```

To fault-inject inside `run-dist` or the LLDB shell, place the
`import pfalloc` / `enable` / `set_counter` calls at the top of the reproducer
script, immediately before the code under test.

### Debugger breakpoint

The C implementation contains a dedicated `Py_NO_INLINE` function
`PyMem_PfFailed()` that is called exactly once per injected failure. Set an
LLDB breakpoint on it to stop at the injection site:

```
(lldb) breakpoint set -n PyMem_PfFailed
```

This makes it straightforward to inspect the call stack at the moment of
injection, confirm which allocator domain fired, and correlate the failure with
Python object state.

### Size filter

`alloc_size` allows targeting a specific allocation size. This is useful when
a code path of interest always allocates a fixed-size struct and you want to
skip over unrelated heap traffic:

```python
pfalloc.set_counter(1, 56)   # fail the very first 56-byte allocation
```

When the fault fires, `alloc_size` is reset to 0 automatically so subsequent
allocations of that size are not also blocked.

### Combining with `run-dist`

```sh
./pfx run-dist projects/<project>/scratch/reproducers/alloc-repro.py
```

where `alloc-repro.py` imports and arms `pfalloc` at the top. `run-dist`
builds a standard CPython from the patched tree, so the same reproducer that
exercises allocation-failure paths inside the fuzzing image can be checked
against a conventional release build.

Relevance to reproduction: allocation failures expose error-handling paths that
ordinary fuzzing rarely reaches. When a crash is suspected to involve a missing
`NULL` check or a partially constructed object after a failed allocation,
`pfalloc` lets you reproduce that failure at an exact, controllable point rather
than relying on system memory pressure or fuzzer luck.

## `bisect SCRIPT`

```sh
./pfx bisect <script.py>
./pfx bisect <script.py> --ccache
./pfx bisect <script.py> --configure-args '<extra configure args>'
```

Copies a Python reproducer script into `projects/<project>/scratch/bisect/` and
starts the bisect environment. `--ccache` enables compiler caching for the run.
`--configure-args` passes additional arguments through to CPython's `./configure`.

Relevant output:

- A copy of the reproducer under `projects/<project>/scratch/bisect/`
- An interactive bisect environment for checking the reproducer across builds

Example command shape:

```sh
./pfx bisect projects/<project>/scratch/reproducers/repro-1.py --ccache
```

Relevance to reproduction: it connects a standalone reproducer to the CPython
history or build-configuration space being investigated.

## `summary`

```sh
./pfx summary
```

Aggregates current AFL worker statistics for the project.

Relevant output fields include:

- worker count
- crash count
- core dump count
- execution count and execution rate
- saved hangs and timeouts
- corpus and edge counters

Example command shape:

```sh
./pfx summary
```

Relevance to reproduction: it provides run-scale context for the artifacts, such
as whether a crash is isolated, part of a broad signature storm, or accompanied
by many cores or timeouts.

## `show-config`

```sh
./pfx show-config
```

Displays the current project configuration, with non-default values highlighted.

Settings especially relevant to crash reproduction include:

- `asan`
- `fuzz_mem_limit`
- `fuzz_timeout_ms`
- `fuzz_peg`
- `track_inputs`
- `fuzz_env`
- `py_debug`
- `py_configure_extra_args`

Example command shape:

```sh
./pfx show-config
```

Relevance to reproduction: it records the environment assumptions behind the
artifacts: sanitizer mode, memory limits, timeouts, target selection, custom
environment variables, input tracking, and build flags.

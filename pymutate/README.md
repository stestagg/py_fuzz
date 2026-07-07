# pymutate

An AST-aware custom mutator for AFL++ that mutates **Python source** instead of
raw bytes. It parses each input with [ruff]'s error-tolerant Python parser and
performs targeted, syntax-aware edits keyed off AST node ranges — so mutations
land on real language constructs rather than corrupting bytes the parser will
just reject.

Currently a **standalone dev/test workspace**. It is not yet wired into the
`py_fuzz` build/run pipeline (see *Integration* below).

## Layout

```
pymutate/
  core/   pymutate-core  — AFL-independent mutation logic + dev CLI + tests
  afl/    pymutate-afl   — thin cdylib implementing the AFL++ custom_mutator ABI
```

`core` has no dependency on AFL, so the mutation logic can be developed and
tested with a plain CLI and `cargo test`. `afl` is a small FFI shim that AFL
loads at runtime.

## How it works

`pymutate_core::mutate(input: &[u8], seed: u32, max_size: usize) -> Option<Vec<u8>>`:

1. Decode `input` as UTF-8 (non-UTF-8 → `None`, let AFL havoc handle it).
2. Parse with `ruff_python_parser::parse_unchecked` — always yields a (possibly
   partial) AST even for malformed input, so we mutate whatever parsed.
3. **Pick one** sub-mutator at random (seeded); it produces **one** [`Edit`].
4. Splice that single edit into the byte buffer (respecting `max_size`).

Small, single-edit, attributable mutations play well with AFL's coverage
feedback. Determinism is keyed entirely on `seed`.

### Sub-mutators

A `SubMutator` proposes at most one `Edit` per call. Add a strategy by
implementing the trait and registering it in `core/src/mutators/mod.rs::registry`.

- **`name_subst`** — picks one `Name` node and replaces it with a candidate from
  the compile-embedded `core/src/names.dict` (builtins, singletons, dunders,
  exceptions…). Edit that file to broaden coverage; no logic recompile needed.

## Develop / test

Requires a stable Rust toolchain (see `rust-toolchain.toml`).

```bash
# Eyeball mutations for a seed corpus file across several seeds
cargo run -p pymutate-core --bin mutate -- --count 5 ../testcases/decimal/1.py

# One specific seed, or pipe source in via stdin
cargo run -p pymutate-core --bin mutate -- --seed 7 ../testcases/sqlite/1.py
echo 'print(value)' | cargo run -p pymutate-core --bin mutate

# Full test suite: sweeps every testcases/**/*.py plus malformed inputs and
# checks no-panic / max_size / UTF-8 / determinism invariants
cargo test -p pymutate-core

# Build the AFL cdylib and confirm it exports the C ABI
cargo build -p pymutate-afl --release
nm -gU target/release/libpymutate_afl.dylib | grep afl_custom   # macOS
```

## Dependencies

- `ruff_python_parser` / `ruff_python_ast` / `ruff_text_size` — pinned to a single
  git revision (not published to crates.io) in the workspace `Cargo.toml`.
- `custom_mutator` — AFL++'s high-level Rust wrapper, from the AFLplusplus repo
  `stable` branch. The high-level wrapper needs no AFL headers, so `afl` builds
  without a local AFL++ checkout.

## Integration (follow-up, not done yet)

To wire this into `py_fuzz` fuzzing runs:

- Install Rust in the build image (`pfrun/images/base/build/run.sh`) and add a
  build step (à la `build_helpers.sh`) that emits `libpymutate_afl.so` into
  `/pfm/tools/`.
- Set `AFL_CUSTOM_MUTATOR_LIBRARY` (and optionally *not* `AFL_CUSTOM_MUTATOR_ONLY`
  so havoc still runs) in `pfrun/images/afl/env.txt`.
- Add a `custom_mutator` field to the `Project` dataclass
  (`src/pyfuzz/project.py`) so it is persisted and templated.
- Ensure the `.so` is mounted into the AFL image (`src/pyfuzz/env.py` mount list).

[ruff]: https://github.com/astral-sh/ruff

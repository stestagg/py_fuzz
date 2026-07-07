//! AFL++ `custom_mutator` FFI shim.
//!
//! This is a thin adapter: it implements the AFL++ [`CustomMutator`] trait and
//! delegates all real work to [`pymutate_core::mutate`]. Building it produces the
//! cdylib that AFL++ loads via `AFL_CUSTOM_MUTATOR_LIBRARY=libpymutate_afl.so`.
//!
//! Kept intentionally minimal — the mutation strategy lives in `pymutate-core`
//! so it can be developed and tested without AFL.

use custom_mutator::{export_mutator, CustomMutator};

/// How many times AFL calls [`PyMutator::fuzz`] per queue entry, per cycle.
///
/// This *must* be set explicitly: the `custom_mutator` wrapper's `export_mutator!`
/// unconditionally exports `afl_custom_fuzz_count`, so AFL never falls back to its
/// own perf-score-scaled stage count — and the trait default is `1`. Leaving it at
/// 1 means a single AST mutation per queue entry per cycle, which (with ~10 random
/// single-edit sub-mutators over many candidate sites) barely samples the space.
/// Override at runtime with `PYMUTATE_FUZZ_COUNT` — no `.so` rebuild needed.
const DEFAULT_FUZZ_COUNT: u32 = 64;

struct PyMutator {
    /// Base seed handed to us by AFL at init.
    seed: u32,
    /// Per-call counter so repeated `fuzz` calls on the same input differ.
    counter: u32,
    /// How many `fuzz` calls to request per queue entry (see [`DEFAULT_FUZZ_COUNT`]).
    fuzz_count: u32,
    /// Owns the mutated bytes we hand back to AFL (borrowed by the return value).
    output: Vec<u8>,
}

impl CustomMutator for PyMutator {
    type Error = ();

    fn init(seed: u32) -> Result<Self, Self::Error> {
        // `PYMUTATE_FUZZ_COUNT` lets us retune the per-entry call budget without
        // recompiling the cdylib; fall back to the compiled-in default otherwise.
        let fuzz_count = std::env::var("PYMUTATE_FUZZ_COUNT")
            .ok()
            .and_then(|v| v.trim().parse::<u32>().ok())
            .filter(|&n| n > 0)
            .unwrap_or(DEFAULT_FUZZ_COUNT);

        // AFL calls this once when it loads the library, so it's the spot for a
        // one-time banner listing which sub-mutators are compiled in.
        eprintln!(
            "[pymutate] fuzz_count={fuzz_count}; enabled sub-mutators: {}",
            pymutate_core::mutators::registry_names().join(", ")
        );
        Ok(PyMutator {
            seed,
            counter: 0,
            fuzz_count,
            output: Vec::new(),
        })
    }

    /// Number of `fuzz` calls AFL makes for each selected queue entry. See
    /// [`DEFAULT_FUZZ_COUNT`] for why overriding this is mandatory, not optional.
    fn fuzz_count(&mut self, _buffer: &[u8]) -> Result<u32, Self::Error> {
        Ok(self.fuzz_count)
    }

    fn fuzz<'b, 's: 'b>(
        &'s mut self,
        buffer: &'b mut [u8],
        _add_buff: Option<&[u8]>,
        max_size: usize,
    ) -> Result<Option<&'b [u8]>, Self::Error> {
        self.counter = self.counter.wrapping_add(1);
        // Mix the counter into the seed (multiply by a large odd constant) so
        // successive calls explore different mutations deterministically.
        let call_seed = self.seed ^ self.counter.wrapping_mul(0x9E37_79B9);

        match pymutate_core::mutate(buffer, call_seed, max_size) {
            Some(bytes) => {
                self.output = bytes;
                Ok(Some(self.output.as_slice()))
            }
            // None => let AFL fall back to its own havoc for this iteration.
            None => Ok(None),
        }
    }
}

export_mutator!(PyMutator);

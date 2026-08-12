//! AFL++ `custom_mutator` FFI shim.
//!
//! This is a thin adapter: it implements the AFL++ [`CustomMutator`] trait and
//! delegates all real work to [`pymutate_core::mutate`]. Building it produces the
//! cdylib that AFL++ loads via `AFL_CUSTOM_MUTATOR_LIBRARY=libpymutate_afl.so`.
//!
//! Kept intentionally minimal — the mutation strategy lives in `pymutate-core`
//! so it can be developed and tested without AFL.

use std::collections::hash_map::DefaultHasher;
use std::collections::{BTreeSet, HashSet};
use std::hash::{Hash, Hasher};

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

/// How many extra seeds to try when a mutation duplicates one already returned
/// for the current queue entry (or when the mutator declines outright).
///
/// Each sub-mutator makes one small edit at a randomly chosen site, so short
/// inputs have a small reachable output set and repeats are common: on a 62-byte
/// seed a single 64-call stage produced only 48 distinct buffers. Every repeat
/// costs a full target execution for coverage AFL has already seen, so it is far
/// cheaper to re-roll the seed (a parse) than to hand the duplicate back.
const MAX_RETRIES: u32 = 8;

/// Cap on remembered output hashes, so a queue entry that stays selected across
/// many stages can't grow the set without bound. Comfortably above the usual
/// per-entry call budget, hence effectively "the whole stage" in practice.
const MAX_SEEN: usize = 4096;

/// Upper bound on how many single edits one `fuzz` call may stack.
///
/// The count is drawn per call from a halving distribution, so most outputs are
/// still a lone edit; the tail is what lets a two-line seed reach combined
/// structure at all. Override with `PYMUTATE_MAX_EDITS` (1 = the old
/// strictly-one-edit behaviour); no `.so` rebuild needed.
const DEFAULT_MAX_EDITS: usize = 4;

struct PyMutator {
    /// Base seed handed to us by AFL at init.
    seed: u32,
    /// Per-call counter so repeated `fuzz` calls on the same input differ.
    counter: u32,
    /// How many `fuzz` calls to request per queue entry (see [`DEFAULT_FUZZ_COUNT`]).
    fuzz_count: u32,
    /// Ceiling on edits stacked per call (see [`DEFAULT_MAX_EDITS`]).
    max_edits: usize,
    /// Identifier candidates loaded from the configured files at AFL startup.
    extra_names: Vec<String>,
    /// Hash of the input the [`PyMutator::seen`] set belongs to; a different
    /// input means a new queue entry, so the set is cleared.
    seen_input: u64,
    /// Hashes of the outputs already returned for `seen_input`.
    seen: HashSet<u64>,
    /// Owns the mutated bytes we hand back to AFL (borrowed by the return value).
    output: Vec<u8>,
}

/// Stable-within-a-process hash of a buffer, used only for duplicate detection —
/// a collision costs at most one skipped mutation.
fn hash_bytes(bytes: &[u8]) -> u64 {
    let mut hasher = DefaultHasher::new();
    bytes.hash(&mut hasher);
    hasher.finish()
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
        let max_edits = std::env::var("PYMUTATE_MAX_EDITS")
            .ok()
            .and_then(|v| v.trim().parse::<usize>().ok())
            .filter(|&n| n > 0)
            .unwrap_or(DEFAULT_MAX_EDITS);
        let extra_names = std::env::var("PYMUTATE_NAME_FILES")
            .map(|paths| load_name_files(&paths))
            .unwrap_or_default();

        // AFL calls this once when it loads the library, so it's the spot for a
        // one-time banner listing which sub-mutators are compiled in.
        eprintln!(
            "[pymutate] fuzz_count={fuzz_count}; max_edits={max_edits}; extra_names={}; \
             enabled sub-mutators: {}",
            extra_names.len(),
            pymutate_core::mutators::registry_names().join(", ")
        );
        Ok(PyMutator {
            seed,
            counter: 0,
            fuzz_count,
            max_edits,
            extra_names,
            seen_input: 0,
            seen: HashSet::new(),
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
        // A different input means AFL moved to another queue entry, so the
        // outputs we remember no longer apply.
        let input_hash = hash_bytes(buffer);
        if input_hash != self.seen_input {
            self.seen_input = input_hash;
            self.seen.clear();
        } else if self.seen.len() >= MAX_SEEN {
            self.seen.clear();
        }

        for _ in 0..MAX_RETRIES {
            self.counter = self.counter.wrapping_add(1);
            // Mix the counter into the seed (multiply by a large odd constant) so
            // successive calls explore different mutations deterministically.
            let call_seed = self.seed ^ self.counter.wrapping_mul(0x9E37_79B9);

            let Some(bytes) = pymutate_core::mutate_stacked(
                buffer,
                call_seed,
                max_size,
                &self.extra_names,
                self.max_edits,
            ) else {
                // Whether a sub-mutator finds a usable site depends on the seed
                // (site choice, replacement length vs. `max_size`), so a decline
                // is worth re-rolling — but for genuinely unmutable input every
                // retry declines too, and we give up after the loop.
                continue;
            };

            // Only hand AFL an execution it hasn't already spent on this entry.
            if self.seen.insert(hash_bytes(&bytes)) {
                self.output = bytes;
                return Ok(Some(self.output.as_slice()));
            }
        }

        // Out of retries: nothing new to offer. AFL skips this iteration of the
        // custom-mutator stage (a zero-length return), leaving its own stages to
        // work the entry.
        Ok(None)
    }
}

/// Read colon-separated name files. Missing/unreadable optional files are
/// ignored so projects can add a config file only when they need one.
fn load_name_files(paths: &str) -> Vec<String> {
    let mut names = BTreeSet::new();
    for path in paths.split(':').filter(|path| !path.is_empty()) {
        if let Ok(contents) = std::fs::read_to_string(path) {
            add_names(&mut names, &contents);
        }
    }
    names.into_iter().collect()
}

fn add_names(names: &mut BTreeSet<String>, contents: &str) {
    names.extend(
        contents
            .lines()
            .map(str::trim)
            .filter(|name| !name.is_empty())
            .map(str::to_owned),
    );
}

#[cfg(test)]
mod tests {
    use std::collections::{BTreeSet, HashSet};

    use custom_mutator::CustomMutator;

    use super::{add_names, PyMutator};

    /// A mutator wired up without going through `init` (which reads the process
    /// environment), so tests control the seed and start from a clean state.
    fn mutator() -> PyMutator {
        PyMutator {
            seed: 0xC0FF_EE00,
            counter: 0,
            fuzz_count: 64,
            max_edits: super::DEFAULT_MAX_EDITS,
            extra_names: Vec::new(),
            seen_input: 0,
            seen: HashSet::new(),
            output: Vec::new(),
        }
    }

    /// Run a full stage of `fuzz` calls over one buffer, collecting the outputs.
    fn stage(mutator: &mut PyMutator, src: &[u8], calls: usize) -> Vec<Vec<u8>> {
        let mut buffer = src.to_vec();
        (0..calls)
            .filter_map(|_| {
                mutator
                    .fuzz(&mut buffer, None, 1 << 20)
                    .expect("fuzz is infallible")
                    .map(<[u8]>::to_vec)
            })
            .collect()
    }

    #[test]
    fn outputs_within_a_stage_are_never_repeated() {
        // The point of the dedup set: every buffer handed to AFL costs a target
        // execution, so no two may be equal (nor equal to the input).
        let src = b"x = 1\ny = x\n";
        let outputs = stage(&mut mutator(), src, 64);
        assert!(!outputs.is_empty(), "expected some mutations");
        let unique: HashSet<&Vec<u8>> = outputs.iter().collect();
        assert_eq!(unique.len(), outputs.len(), "stage returned a duplicate");
        assert!(!outputs.iter().any(|out| out == src), "returned the input");
    }

    #[test]
    fn a_new_queue_entry_clears_the_dedup_set() {
        // The set is keyed on the input, so switching entries (and coming back)
        // must not suppress mutations of the second entry.
        let mut mutator = mutator();
        let first = stage(&mut mutator, b"x = 1\ny = x\n", 8);
        let second = stage(&mut mutator, b"z = 2\nw = z\n", 8);
        assert_eq!(second.len(), 8, "second entry was starved by stale hashes");
        assert!(!first.is_empty());
    }

    #[test]
    fn unmutable_input_declines_instead_of_looping_forever() {
        // Empty input has no syntax to edit: every retry declines, and `fuzz`
        // must return `None` rather than spin.
        let outputs = stage(&mut mutator(), b"", 8);
        assert!(outputs.is_empty(), "expected no mutation of empty input");
    }

    #[test]
    fn name_file_lines_are_stripped_and_deduplicated() {
        let mut names = BTreeSet::new();
        add_names(&mut names, " ndarray \n\nDataFrame\nndarray\n");
        assert_eq!(
            names.into_iter().collect::<Vec<_>>(),
            ["DataFrame", "ndarray"]
        );
    }
}

export_mutator!(PyMutator);

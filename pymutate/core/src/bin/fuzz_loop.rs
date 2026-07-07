//! Standalone brute-force repro harness for `pymutate_core`, independent of
//! AFL and the `custom_mutator` FFI shim entirely.
//!
//! Mimics how AFL actually exercises the mutator: each successful mutation is
//! fed back in as a new corpus entry (so inputs get progressively weirder
//! across iterations, not just single mutations of pristine seeds), which is
//! the pattern needed to reproduce a bug that only showed up after ~100k AFL
//! execs.
//!
//! Run under AddressSanitizer (needs nightly) so a heap overflow is caught at
//! the write, not lazily by glibc much later:
//!
//!   RUSTFLAGS="-Zsanitizer=address" \
//!     rustup run nightly cargo run -Zbuild-std --target <host-triple> \
//!     -p pymutate-core --release --bin fuzz_loop -- ../../testcases
//!
//! Before every `mutate()` call the (buffer, seed) about to be used is
//! written to `last_attempt.py` / `last_attempt.seed` in the cwd, so a crash
//! can be reproduced exactly afterwards with:
//!
//!   cargo run -p pymutate-core --bin mutate -- \
//!     --seed "$(cat last_attempt.seed)" last_attempt.py

use rand::Rng as _;
use std::path::Path;

const MAX_SIZE: usize = 1024 * 1024; // matches AFL's MAX_FILE
const MAX_CORPUS: usize = 4096;
const PROGRESS_EVERY: u64 = 1000;

fn main() {
    let mut args = std::env::args().skip(1);
    let corpus_dir = args.next().unwrap_or_else(|| "../testcases".to_string());
    let iterations: u64 = args
        .next()
        .map(|s| s.parse().expect("iterations must be a number"))
        .unwrap_or(u64::MAX);

    let mut corpus = load_corpus(Path::new(&corpus_dir));
    assert!(
        !corpus.is_empty(),
        "no .py seed files found under {corpus_dir}"
    );
    eprintln!("loaded {} seed files from {corpus_dir}", corpus.len());

    let mut rng = rand::thread_rng();
    let mut i: u64 = 0;

    while i < iterations {
        i += 1;

        let idx = rng.gen_range(0..corpus.len());
        let input = corpus[idx].clone();
        let seed: u32 = rng.gen();

        write_repro(&input, seed);

        if let Some(out) = pymutate_core::mutate(&input, seed, MAX_SIZE) {
            if corpus.len() < MAX_CORPUS {
                corpus.push(out);
            } else {
                let evict = rng.gen_range(0..corpus.len());
                corpus[evict] = out;
            }
        }

        if i % PROGRESS_EVERY == 0 {
            eprintln!("iter {i} (corpus size {})", corpus.len());
        }
    }

    eprintln!("completed {i} iterations without crashing");
}

/// Overwrite the same two files every iteration (cheap: no directory growth)
/// so whichever input was in flight when a crash happens is on disk.
fn write_repro(input: &[u8], seed: u32) {
    let _ = std::fs::write("last_attempt.py", input);
    let _ = std::fs::write("last_attempt.seed", seed.to_string());
}

fn load_corpus(dir: &Path) -> Vec<Vec<u8>> {
    let mut out = Vec::new();
    visit(dir, &mut out);
    out
}

fn visit(dir: &Path, out: &mut Vec<Vec<u8>>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_dir() {
            visit(&path, out);
        } else if path.extension().is_some_and(|e| e == "py") {
            if let Ok(bytes) = std::fs::read(&path) {
                if bytes.len() <= MAX_SIZE && std::str::from_utf8(&bytes).is_ok() {
                    out.push(bytes);
                }
            }
        }
    }
}

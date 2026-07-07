//! `pymutate-core` — AST-aware Python source mutation, independent of AFL++.
//!
//! The single entry point is [`mutate`]. It is deliberately AFL-agnostic (bytes
//! in, bytes out, plus a seed) so it can be exercised from a plain CLI and unit
//! tests; the AFL++ `custom_mutator` FFI shim lives in the sibling `afl` crate.

pub mod mutators;
mod rng;

use ruff_python_parser::{parse_unchecked, Mode, ParseOptions};

use crate::mutators::{AstCtx, Edit, SubMutator};
use crate::rng::Rng;

/// Mutate `input` (Python source bytes) into a new buffer, or return `None` to
/// signal "no syntax-aware mutation was made" (the AFL++ shim then falls back to
/// havoc for this iteration).
///
/// Contract:
/// - `seed` makes the result deterministic and reproducible.
/// - The output never exceeds `max_size` bytes.
/// - Exactly one sub-mutator runs, producing exactly one edit.
///
/// `None` is returned when the input is not valid UTF-8, when no sub-mutator
/// finds anything to change, or when the single edit would overflow `max_size`.
pub fn mutate(input: &[u8], seed: u32, max_size: usize) -> Option<Vec<u8>> {
    mutate_inner(input, seed, max_size, mutators::registry())
}

/// Like [`mutate`], but restricted to the sub-mutators whose [`SubMutator::name`]
/// appears in `only`. This lets tests (and the dev CLI's `--only` flag) exercise
/// one strategy in isolation instead of teasing apart the combined output.
///
/// An empty `only` selects nothing and therefore always returns `None`. Note the
/// result is *not* expected to match [`mutate`] for the same seed: the driver's
/// random choice depends on how many sub-mutators are in play.
pub fn mutate_with(input: &[u8], seed: u32, max_size: usize, only: &[&str]) -> Option<Vec<u8>> {
    let selected: Vec<Box<dyn SubMutator>> = mutators::registry()
        .into_iter()
        .filter(|m| only.contains(&m.name()))
        .collect();
    mutate_inner(input, seed, max_size, selected)
}

/// Shared driver: pick one of `submutators` at random and apply its single edit.
fn mutate_inner(
    input: &[u8],
    seed: u32,
    max_size: usize,
    submutators: Vec<Box<dyn SubMutator>>,
) -> Option<Vec<u8>> {
    // Non-UTF-8 input isn't Python source we can reason about; let havoc have it.
    let source = std::str::from_utf8(input).ok()?;

    // `parse_unchecked` is error-tolerant: it always returns a (possibly partial)
    // tree plus a list of errors, which is exactly what we want for malformed
    // fuzz inputs — we mutate whatever nodes did parse.
    let parsed = parse_unchecked(source, ParseOptions::from(Mode::Module));
    let ctx = AstCtx {
        source,
        module: parsed.syntax(),
    };

    let mut rng = Rng::from_seed(seed);

    // Deterministically pick one sub-mutator at random. If it has nothing to do
    // for this input, fall through to the remaining ones in the shuffled order.
    let mut order: Vec<usize> = (0..submutators.len()).collect();
    rng.shuffle(&mut order);
    for idx in order {
        if let Some(edit) = submutators[idx].mutate(&ctx, &mut rng) {
            return apply_edit(source, &edit, max_size);
        }
    }
    None
}

/// Splice a single edit's replacement into the source at its byte range.
fn apply_edit(source: &str, edit: &Edit, max_size: usize) -> Option<Vec<u8>> {
    let start = usize::from(edit.range.start());
    let end = usize::from(edit.range.end());
    let src = source.as_bytes();

    let new_len = src.len() - (end - start) + edit.replacement.len();
    if new_len > max_size {
        return None;
    }

    let mut out = Vec::with_capacity(new_len);
    out.extend_from_slice(&src[..start]);
    out.extend_from_slice(edit.replacement.as_bytes());
    out.extend_from_slice(&src[end..]);
    Some(out)
}

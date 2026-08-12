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
/// signal "no syntax-aware mutation was made" (AFL++ then skips that iteration
/// of the custom-mutator stage).
///
/// Contract:
/// - `seed` makes the result deterministic and reproducible.
/// - The output never exceeds `max_size` bytes.
/// - Exactly one sub-mutator runs, producing exactly one edit.
///
/// Input that is not valid UTF-8 is not rejected outright: the valid prefix is
/// mutated and the remaining bytes are re-attached untouched (see
/// [`split_valid_utf8`]).
///
/// `None` is returned only when no sub-mutator produces an edit that fits in
/// `max_size` — e.g. an empty input, or one whose valid UTF-8 prefix holds no
/// mutable syntax.
pub fn mutate(input: &[u8], seed: u32, max_size: usize) -> Option<Vec<u8>> {
    let mut rng = Rng::from_seed(seed);
    mutate_inner(input, &mut rng, max_size, &mutators::registry())
}

/// Like [`mutate`], with additional identifier candidates supplied by the host.
/// The extras are used by every mutator that draws from `names.dict`.
pub fn mutate_with_extra_names(
    input: &[u8],
    seed: u32,
    max_size: usize,
    extra_names: &[String],
) -> Option<Vec<u8>> {
    let mut rng = Rng::from_seed(seed);
    mutate_inner(
        input,
        &mut rng,
        max_size,
        &mutators::registry_with_extra_names(extra_names),
    )
}

/// Like [`mutate_with_extra_names`], but *stacks* between 1 and `max_edits`
/// independent single-edit mutations, re-parsing between each one.
///
/// One edit per call keeps outputs attributable, but it also caps the reachable
/// output set at roughly (sites x candidates) — for a two-line seed that is a
/// few hundred buffers, so a fuzzing stage exhausts it and then just repeats
/// itself. Stacking multiplies that set per extra edit, which is the only way a
/// tiny input ever reaches deep or combined structure.
///
/// The count is drawn from a halving distribution ([`Rng::geometric`]), so most
/// outputs are still a single edit and the deeper combinations are sampled
/// rather than forced. `max_edits` of 0 or 1 is exactly the single-edit driver.
pub fn mutate_stacked(
    input: &[u8],
    seed: u32,
    max_size: usize,
    extra_names: &[String],
    max_edits: usize,
) -> Option<Vec<u8>> {
    let submutators = mutators::registry_with_extra_names(extra_names);
    let mut rng = Rng::from_seed(seed);
    let edits = rng.geometric(max_edits.max(1));

    let mut current: Option<Vec<u8>> = None;
    for _ in 0..edits {
        // Each edit re-reads the previous output: byte offsets shift as soon as
        // one edit lands, so the next sub-mutator needs a fresh parse to aim at.
        let source = current.as_deref().unwrap_or(input);
        match mutate_inner(source, &mut rng, max_size, &submutators) {
            Some(next) => current = Some(next),
            // Nothing more to do (or nothing that fits): keep what we have.
            None => break,
        }
    }
    current
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
    let mut rng = Rng::from_seed(seed);
    mutate_inner(input, &mut rng, max_size, &selected)
}

/// Shared driver: pick one of `submutators` at random and apply its single edit.
fn mutate_inner(
    input: &[u8],
    rng: &mut Rng,
    max_size: usize,
    submutators: &[Box<dyn SubMutator>],
) -> Option<Vec<u8>> {
    // Non-UTF-8 bytes aren't Python source we can reason about, but declining the
    // whole buffer would permanently exclude every queue entry havoc has mangled.
    // Mutate the valid prefix instead and carry the rest through verbatim.
    let (source, tail) = split_valid_utf8(input);

    // `parse_unchecked` is error-tolerant: it always returns a (possibly partial)
    // tree plus a list of errors, which is exactly what we want for malformed
    // fuzz inputs — we mutate whatever nodes did parse.
    let parsed = parse_unchecked(source, ParseOptions::from(Mode::Module));
    let ctx = AstCtx {
        source,
        module: parsed.syntax(),
    };

    // Weight each sub-mutator by how much it can actually do here instead of
    // picking uniformly: uniform choice spends as much budget on a mutator with
    // one possible output as on one with thousands, and the small ones then
    // supply nearly all the repeats. A mutator with no sites at all now scores
    // zero and is skipped outright rather than picked and declining.
    //
    // The weight is logarithmic in the space, not proportional to it. Straight
    // proportional weighting is *worse* than uniform in practice: it hands ~97%
    // of the budget to the dictionary-backed mutators and starves the structural
    // ones (`line_dup`, `self_rebind`, `del_insert`, `splat_spray`), which are
    // the only source of genuinely new program shapes. Logarithmic keeps a
    // 1000x-larger space at a ~10x larger share, so nothing is starved.
    let mut weights: Vec<usize> = submutators
        .iter()
        .map(|m| selection_weight(m.edit_space(&ctx)))
        .collect();

    // Draw without replacement: a sub-mutator that declines anyway, or whose
    // edit would overflow `max_size`, must not sink the whole call — the ones we
    // haven't tried may have a smaller edit (a name substitution vs. a whole
    // duplicated block, say).
    while let Some(idx) = rng.weighted_index(&weights) {
        if let Some(edit) = submutators[idx].mutate(&ctx, rng) {
            if let Some(out) = apply_edit(source, &edit, tail, max_size) {
                return Some(out);
            }
        }
        weights[idx] = 0;
    }
    None
}

/// Turn an edit-space estimate into a selection weight.
///
/// Logarithmic on purpose — see the rationale in [`mutate_inner`].
fn selection_weight(space: usize) -> usize {
    match space {
        0 => 0,
        space => 1 + space.ilog2() as usize,
    }
}

/// Per-sub-mutator `(name, edit_space, weight)` for `input`, in registry order:
/// exactly what the driver's weighted choice is based on.
///
/// Exposed for the dev CLI's `--weights`, which is how you check that a seed
/// isn't handing its whole budget to one strategy.
pub fn weight_report(input: &[u8], extra_names: &[String]) -> Vec<(&'static str, usize, usize)> {
    let (source, _) = split_valid_utf8(input);
    let parsed = parse_unchecked(source, ParseOptions::from(Mode::Module));
    let ctx = AstCtx {
        source,
        module: parsed.syntax(),
    };
    mutators::registry_with_extra_names(extra_names)
        .iter()
        .map(|m| {
            let space = m.edit_space(&ctx);
            (m.name(), space, selection_weight(space))
        })
        .collect()
}

/// Split `input` into its longest valid UTF-8 prefix and the trailing bytes that
/// prefix could not decode.
///
/// AFL's own havoc stage regularly turns part of a queue entry into arbitrary
/// bytes. Treating that as "not mutable" would retire the entry from AST-aware
/// mutation for the rest of the campaign, so we mutate the decodable head and
/// splice the raw tail back on afterwards.
fn split_valid_utf8(input: &[u8]) -> (&str, &[u8]) {
    match std::str::from_utf8(input) {
        Ok(source) => (source, &[]),
        Err(err) => {
            let (head, tail) = input.split_at(err.valid_up_to());
            // `valid_up_to` is by definition the length of a valid prefix, so
            // this re-validation always succeeds; fall back to "nothing usable"
            // rather than `unwrap` if that ever stops holding.
            (std::str::from_utf8(head).unwrap_or(""), tail)
        }
    }
}

/// Splice a single edit's replacement into the source at its byte range, then
/// re-attach the undecodable `tail` (empty for well-formed UTF-8 input).
fn apply_edit(source: &str, edit: &Edit, tail: &[u8], max_size: usize) -> Option<Vec<u8>> {
    let start = usize::from(edit.range.start());
    let end = usize::from(edit.range.end());
    let src = source.as_bytes();

    let new_len = src.len() - (end - start) + edit.replacement.len() + tail.len();
    if new_len > max_size {
        return None;
    }

    let mut out = Vec::with_capacity(new_len);
    out.extend_from_slice(&src[..start]);
    out.extend_from_slice(edit.replacement.as_bytes());
    out.extend_from_slice(&src[end..]);
    out.extend_from_slice(tail);
    Some(out)
}

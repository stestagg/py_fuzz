//! `TrickyData` — replace one literal with a curated "tricky" value of the
//! *same* kind, sourced from the `trickydata-macros` crate.
//!
//! `trickydata-macros` bakes in const arrays of adversarial values per Rust
//! type (numeric boundaries, subnormals, `NaN`/`inf`, homoglyph/bidi/combining
//! Unicode strings, …) — exactly the kind of edge cases worth throwing at the
//! interpreter that `bignum` and `type_swap` hand-curate for their own narrower
//! purposes. This mutator is the constant-replacement sibling of `bignum`
//! (same "pick one literal, replace it in place" shape) but draws its pool from
//! that crate instead of a fixed list.
//!
//! Coverage is int / float / str only:
//!   - Widening is lossless, so requesting the *widest* integer types
//!     (`u128`, `i128`) already yields the union of every narrower type's
//!     boundary values (`u8::MAX`, `i16::MIN`, …) — no need to also request
//!     `u8`..`u64` / `i8`..`i64` separately.
//!   - Likewise `f64` alone covers the `f32` pool (`f32→f64` widening).
//!   - `bool` is requested by nothing here: the crate's bool pool is empty
//!     (there is no "tricky" bool beyond `True`/`False`, which `type_swap`
//!     already covers).
//!   - `Vec<u8>` (bytes) is deliberately skipped — correctly escaping
//!     arbitrary raw byte payloads into a Python bytes literal is a
//!     meaningfully different (and separately worth doing) chunk of work.
//!
//! Every numeric replacement is wrapped in parentheses, same as `bignum` and
//! for the same reason: a leading `-` (these pools skew heavily negative /
//! extreme) must never re-associate with a preceding operator.

use ruff_python_ast::visitor::{walk_expr, Visitor};
use ruff_python_ast::{Expr, LiteralExpressionRef, Number};
use ruff_text_size::{Ranged, TextRange};

use crate::mutators::{walk_module, AstCtx, Edit, SubMutator};
use crate::rng::Rng;

static U128_POOL: &[u128] = trickydata_macros::static_values!(u128);
static I128_POOL: &[i128] = trickydata_macros::static_values!(i128);
static F64_POOL: &[f64] = trickydata_macros::static_values!(f64);
static STR_POOL: &[&str] = trickydata_macros::static_values!(String);

/// The literal kinds this mutator knows how to replace.
#[derive(Clone, Copy, PartialEq, Eq)]
enum SiteKind {
    Int,
    Float,
    Str,
}

#[derive(Clone, Copy)]
struct Site {
    range: TextRange,
    kind: SiteKind,
}

/// Preorder visitor that records every int / float / string literal (skipping
/// bytes, bool, complex, `None`, `...`, and containers).
struct SiteCollector {
    sites: Vec<Site>,
}

impl<'a> Visitor<'a> for SiteCollector {
    fn visit_expr(&mut self, expr: &'a Expr) {
        if let Some(lit) = expr.as_literal_expr() {
            let kind = match lit {
                LiteralExpressionRef::NumberLiteral(n) => match n.value {
                    Number::Int(_) => Some(SiteKind::Int),
                    Number::Float(_) => Some(SiteKind::Float),
                    Number::Complex { .. } => None,
                },
                LiteralExpressionRef::StringLiteral(_) => Some(SiteKind::Str),
                _ => None,
            };
            if let Some(kind) = kind {
                self.sites.push(Site {
                    range: expr.range(),
                    kind,
                });
            }
        }
        walk_expr(self, expr);
    }
}

/// Format an `f64` from the pool as a Python float expression. `NaN` and the
/// infinities have no Python float-literal spelling, so those go through
/// `float(...)`; everything else uses exponential form (always has a `.`-or-`e`
/// so it can never be mistaken for an int literal).
fn format_float(v: f64) -> String {
    if v.is_nan() {
        "float('nan')".to_string()
    } else if v.is_infinite() {
        if v.is_sign_positive() {
            "float('inf')".to_string()
        } else {
            "float('-inf')".to_string()
        }
    } else {
        format!("{v:e}")
    }
}

/// Escape an arbitrary Rust `&str` (already valid Unicode — `trickydata`'s
/// `String` pool can't contain lone surrogates) into a single-quoted Python
/// string literal: backslash/quote/control characters are escaped, everything
/// else is emitted raw since Python source is UTF-8.
fn python_str_literal(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('\'');
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '\'' => out.push_str("\\'"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out.push('\'');
    out
}

pub struct TrickyData {
    /// Plain decimal ints (both pools combined; no parens yet — added at the
    /// edit site so the same value could in principle be reused unwrapped).
    ints: Vec<String>,
    /// Python float expressions (no parens yet).
    floats: Vec<String>,
    /// Fully-quoted Python string literals.
    strs: Vec<String>,
}

impl TrickyData {
    pub fn new() -> Self {
        let ints = U128_POOL
            .iter()
            .map(|v| v.to_string())
            .chain(I128_POOL.iter().map(|v| v.to_string()))
            .collect();
        let floats = F64_POOL.iter().map(|v| format_float(*v)).collect();
        let strs = STR_POOL.iter().map(|s| python_str_literal(s)).collect();
        Self { ints, floats, strs }
    }
}

impl Default for TrickyData {
    fn default() -> Self {
        Self::new()
    }
}

impl SubMutator for TrickyData {
    fn name(&self) -> &'static str {
        "trickydata"
    }

    fn mutate(&self, ctx: &AstCtx, rng: &mut Rng) -> Option<Edit> {
        let mut collector = SiteCollector { sites: Vec::new() };
        walk_module(&mut collector, ctx.module);

        let site = *rng.choose(&collector.sites)?;
        let replacement = match site.kind {
            SiteKind::Int => format!("({})", rng.choose(&self.ints)?),
            SiteKind::Float => format!("({})", rng.choose(&self.floats)?),
            SiteKind::Str => rng.choose(&self.strs)?.clone(),
        };

        Some(Edit {
            range: site.range,
            kind: "trickydata",
            replacement,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Run only `trickydata` over `src` for one seed, returning the mutated
    /// output.
    fn run(src: &str, seed: u32) -> Option<String> {
        let out = crate::mutate_with(src.as_bytes(), seed, 1 << 20, &["trickydata"])?;
        Some(String::from_utf8(out).unwrap())
    }

    #[test]
    fn pools_are_nonempty() {
        assert!(!U128_POOL.is_empty());
        assert!(!I128_POOL.is_empty());
        assert!(!F64_POOL.is_empty());
        assert!(!STR_POOL.is_empty());
    }

    #[test]
    fn replaces_an_int_and_stays_parseable() {
        for seed in 0..64u32 {
            if let Some(out) = run("x = 5\n", seed) {
                assert!(out.starts_with("x = ("), "seed {seed}: {out:?}");
                let parsed = ruff_python_parser::parse_module(&out);
                assert!(parsed.is_ok(), "seed {seed}: unparseable: {out:?}");
            }
        }
    }

    #[test]
    fn replaces_a_float_and_stays_parseable() {
        let mut checked = false;
        for seed in 0..64u32 {
            if let Some(out) = run("y = 1.5\n", seed) {
                assert!(out.starts_with("y = ("), "seed {seed}: {out:?}");
                let parsed = ruff_python_parser::parse_module(&out);
                assert!(parsed.is_ok(), "seed {seed}: unparseable: {out:?}");
                checked = true;
            }
        }
        assert!(checked, "expected the float to be mutated for some seed");
    }

    #[test]
    fn nan_and_inf_use_float_call_form() {
        let saw_special = (0..256u32).any(|seed| {
            run("y = 1.5\n", seed).is_some_and(|s| s.contains("float('nan')") || s.contains("float('inf')") || s.contains("float('-inf')"))
        });
        assert!(saw_special, "expected NaN/inf to appear across seeds");
    }

    #[test]
    fn replaces_a_string_with_a_quoted_literal_and_stays_parseable() {
        let mut checked = false;
        for seed in 0..64u32 {
            if let Some(out) = run("s = 'hi'\n", seed) {
                let replaced = out.trim_start_matches("s = ").trim();
                assert!(replaced.starts_with('\''), "seed {seed}: {out:?}");
                let parsed = ruff_python_parser::parse_module(&out);
                assert!(parsed.is_ok(), "seed {seed}: unparseable: {out:?}");
                checked = true;
            }
        }
        assert!(checked, "expected the string to be mutated for some seed");
    }

    #[test]
    fn bytes_and_bool_are_left_alone() {
        // Neither literal kind is in our pool, so with only `trickydata`
        // selected there's nothing to do and every seed returns None.
        for seed in 0..32u32 {
            assert!(run("b = b'x'\n", seed).is_none(), "seed {seed}");
            assert!(run("t = True\n", seed).is_none(), "seed {seed}");
        }
    }

    #[test]
    fn no_literals_yields_nothing() {
        assert!(run("x = y\n", 0).is_none());
    }
}

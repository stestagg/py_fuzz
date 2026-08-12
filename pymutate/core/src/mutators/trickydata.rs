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
//! The `String` `values()` pool only yields *valid* text, so it silently drops
//! the corpus's deliberately-invalid encoding cases (lone surrogates, CESU-8,
//! overlong/truncated sequences). Those are some of the most interesting inputs
//! for a Python interpreter, so we additionally pull every **invalid** would-be
//! string carrying any of the `unicode` / `utf8` / `invalid-utf8` tags via
//! `static_examples!` and reconstruct each as a Python `str` literal: the raw
//! bytes are decoded WTF-8-style (valid + surrogate sequences recovered, e.g.
//! `ED A0 80` → `\ud800`, matching CPython's `surrogatepass`), with any byte
//! that still can't decode surrogate-escaped (`b` → `\udc00+b`, matching
//! `surrogateescape`). The result is always emitted with ASCII-only `\x`/`\u`/`\U`
//! escapes, so the mutated source stays valid UTF-8 while the parsed `str` still
//! contains the tricky (often un-encodable) code points.
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

/// Every input carrying the given tag as a would-be `String`, invalid ones
/// included (`value: None`, raw bytes preserved). Tags are AND-ed within a
/// single `static_examples!` call, so the three tags we want unioned need three
/// arrays; `TrickyData::new` filters to the invalid entries and dedups by name.
type StrExamples = &'static [trickydata::StaticExample<&'static str>];
static UNICODE_EXAMPLES: StrExamples =
    trickydata_macros::static_examples!(String, tags = ["unicode"]);
static UTF8_EXAMPLES: StrExamples = trickydata_macros::static_examples!(String, tags = ["utf8"]);
static INVALID_UTF8_EXAMPLES: StrExamples =
    trickydata_macros::static_examples!(String, tags = ["invalid-utf8"]);

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

/// Every int / float / string literal in the module, in source order.
fn literal_sites(ctx: &AstCtx) -> Vec<Site> {
    let mut collector = SiteCollector { sites: Vec::new() };
    walk_module(&mut collector, ctx.module);
    collector.sites
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

/// Python's `surrogateescape` convention: a byte `b` that can't be decoded is
/// smuggled into a `str` as the low-surrogate code point `U+DC00 + b`.
fn surrogate_escape(b: u8) -> u32 {
    0xDC00 + b as u32
}

/// Decode a slice of *invalid* UTF-8 into code points (`u32`, since the result
/// may include surrogates `0xD800..=0xDFFF`). Valid sequences — extended to
/// accept the 3-byte surrogate forms, i.e. WTF-8 / CPython `surrogatepass` —
/// yield their code point; any byte that can't begin or complete a legal
/// sequence is surrogate-escaped and we resync one byte forward. Overlong,
/// out-of-range (`> U+10FFFF`), and truncated sequences all fall through to the
/// per-byte escape, so the return value is never lossy and never panics.
fn decode_wtf8(raw: &[u8]) -> Vec<u32> {
    let mut out = Vec::new();
    let mut i = 0;
    while i < raw.len() {
        let b0 = raw[i];
        if b0 < 0x80 {
            out.push(b0 as u32);
            i += 1;
            continue;
        }
        // (sequence length, allowed range for the first continuation byte). The
        // b1 range folds in the overlong guard (E0/F0) and the max-code-point
        // guard (F4); surrogates are intentionally *not* excluded from the ED
        // range. Continuation bytes 2..len are always 0x80..=0xBF.
        let (len, min_b1, max_b1) = match b0 {
            0xC2..=0xDF => (2, 0x80, 0xBF),
            0xE0 => (3, 0xA0, 0xBF),
            0xED => (3, 0x80, 0xBF),
            0xE1..=0xEC | 0xEE..=0xEF => (3, 0x80, 0xBF),
            0xF0 => (4, 0x90, 0xBF),
            0xF4 => (4, 0x80, 0x8F),
            0xF1..=0xF3 => (4, 0x80, 0xBF),
            _ => {
                out.push(surrogate_escape(b0));
                i += 1;
                continue;
            }
        };
        let seq_ok = i + len <= raw.len()
            && (min_b1..=max_b1).contains(&raw[i + 1])
            && raw[i + 2..i + len]
                .iter()
                .all(|b| (0x80..=0xBF).contains(b));
        if !seq_ok {
            out.push(surrogate_escape(b0));
            i += 1;
            continue;
        }
        let cp = raw[i..i + len]
            .iter()
            .enumerate()
            .fold(0u32, |acc, (k, &b)| {
                let bits = if k == 0 {
                    b as u32 & (0x7F >> len)
                } else {
                    b as u32 & 0x3F
                };
                (acc << 6) | bits
            });
        out.push(cp);
        i += len;
    }
    out
}

/// Decode a slice of *invalid* UTF-16-LE into code points: valid surrogate pairs
/// combine into a supplementary scalar, a lone surrogate is kept as its own code
/// point, and a dangling trailing byte is surrogate-escaped.
fn decode_wtf16le(raw: &[u8]) -> Vec<u32> {
    let mut out = Vec::new();
    let mut i = 0;
    while i + 1 < raw.len() {
        let unit = raw[i] as u32 | (raw[i + 1] as u32) << 8;
        i += 2;
        if (0xD800..=0xDBFF).contains(&unit) && i + 1 < raw.len() {
            let low = raw[i] as u32 | (raw[i + 1] as u32) << 8;
            if (0xDC00..=0xDFFF).contains(&low) {
                out.push(0x10000 + ((unit - 0xD800) << 10) + (low - 0xDC00));
                i += 2;
                continue;
            }
        }
        out.push(unit);
    }
    if i < raw.len() {
        out.push(surrogate_escape(raw[i]));
    }
    out
}

/// Render decoded code points (possibly including surrogates) as a single-quoted
/// Python `str` literal using only ASCII: printable ASCII is emitted raw, and
/// everything else — controls, non-ASCII scalars, and surrogates — goes through
/// `\xXX` / `\uXXXX` / `\UXXXXXXXX`. Keeping the output ASCII-only means the
/// mutated source stays valid UTF-8 even when the `str` it denotes is not.
fn python_str_literal_from_code_points(cps: &[u32]) -> String {
    let mut out = String::with_capacity(cps.len() + 2);
    out.push('\'');
    for &cp in cps {
        match cp {
            0x5C => out.push_str("\\\\"),
            0x27 => out.push_str("\\'"),
            0x0A => out.push_str("\\n"),
            0x0D => out.push_str("\\r"),
            0x09 => out.push_str("\\t"),
            0x20..=0x7E => out.push(cp as u8 as char),
            0..=0xFF => out.push_str(&format!("\\x{cp:02x}")),
            0x100..=0xFFFF => out.push_str(&format!("\\u{cp:04x}")),
            _ => out.push_str(&format!("\\U{cp:08x}")),
        }
    }
    out.push('\'');
    out
}

/// Reconstruct one deliberately-invalid corpus example as a Python `str` literal,
/// decoding its raw bytes according to the encoding it *would* have been.
fn invalid_example_literal(ex: &trickydata::StaticExample<&str>) -> String {
    let enc = ex.invalid_as.unwrap_or(ex.decode_as);
    let cps = match enc {
        trickydata::DecodeAs::Utf16Le => decode_wtf16le(ex.raw),
        _ => decode_wtf8(ex.raw),
    };
    python_str_literal_from_code_points(&cps)
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

        // Valid strings from the `values()` pool, plus the invalid would-be
        // strings (lone surrogates, CESU-8, overlong/truncated bytes) that
        // `values()` drops — reconstructed as escaped literals. The three tag
        // arrays overlap heavily, so dedup by input name.
        let mut strs: Vec<String> = STR_POOL.iter().map(|s| python_str_literal(s)).collect();
        let mut seen: std::collections::HashSet<&str> = std::collections::HashSet::new();
        for ex in UNICODE_EXAMPLES
            .iter()
            .chain(UTF8_EXAMPLES)
            .chain(INVALID_UTF8_EXAMPLES)
        {
            if ex.value.is_some() || !seen.insert(ex.name) {
                continue;
            }
            strs.push(invalid_example_literal(ex));
        }

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

    fn edit_space(&self, ctx: &AstCtx) -> usize {
        // A site draws only from the pool matching its literal kind.
        literal_sites(ctx)
            .iter()
            .map(|site| match site.kind {
                SiteKind::Int => self.ints.len(),
                SiteKind::Float => self.floats.len(),
                SiteKind::Str => self.strs.len(),
            })
            .sum()
    }

    fn mutate(&self, ctx: &AstCtx, rng: &mut Rng) -> Option<Edit> {
        let site = *rng.choose(&literal_sites(ctx))?;
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
            run("y = 1.5\n", seed).is_some_and(|s| {
                s.contains("float('nan')")
                    || s.contains("float('inf')")
                    || s.contains("float('-inf')")
            })
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

    #[test]
    fn wtf8_recovers_lone_high_surrogate() {
        // The user-facing example: `ED A0 80` is the naive-UTF-8 spelling of a
        // lone high surrogate and must come back as the U+D800 code point.
        assert_eq!(decode_wtf8(&[0xED, 0xA0, 0x80]), vec![0xD800]);
        assert_eq!(
            python_str_literal_from_code_points(&decode_wtf8(&[0xED, 0xA0, 0x80])),
            "'\\ud800'"
        );
    }

    #[test]
    fn wtf8_recovers_cesu8_surrogate_pair() {
        // CESU-8 for U+1F600 decodes to the two separate surrogate code points.
        assert_eq!(
            decode_wtf8(&[0xED, 0xA0, 0xBD, 0xED, 0xB8, 0x80]),
            vec![0xD83D, 0xDE00]
        );
    }

    #[test]
    fn wtf8_surrogate_escapes_undecodable_bytes() {
        // Above U+10FFFF, overlong, stray continuation — none decode, each byte
        // is surrogate-escaped rather than dropped.
        assert_eq!(
            decode_wtf8(&[0xF4, 0x90, 0x80, 0x80]),
            vec![0xDCF4, 0xDC90, 0xDC80, 0xDC80]
        );
        assert_eq!(decode_wtf8(&[0xC0, 0x80]), vec![0xDCC0, 0xDC80]);
        // Valid ASCII survives around a stray continuation byte.
        assert_eq!(decode_wtf8(&[0x41, 0x80, 0x42]), vec![0x41, 0xDC80, 0x42]);
    }

    #[test]
    fn wtf16le_combines_pairs_and_keeps_lone_surrogates() {
        // 'A' then a lone high surrogate (utf16-le `00 d8`).
        assert_eq!(
            decode_wtf16le(&[0x41, 0x00, 0x00, 0xD8]),
            vec![0x41, 0xD800]
        );
        // A well-formed pair (utf16-le for U+1F600) combines.
        assert_eq!(decode_wtf16le(&[0x3D, 0xD8, 0x00, 0xDE]), vec![0x1F600]);
    }

    #[test]
    fn invalid_literals_reach_the_mutated_output() {
        // Drive the whole mutate path over many seeds; at least one must splice a
        // reconstructed surrogate escape into a string site and stay parseable.
        // `python_str_literal` emits valid strings' non-ASCII chars raw, so a
        // `\u` escape in the output can only have come from a reconstructed
        // invalid example.
        let landed =
            (0..2048u32).any(|seed| run("s = 'hi'\n", seed).is_some_and(|out| out.contains("\\u")));
        assert!(
            landed,
            "expected a reconstructed surrogate literal to be spliced in"
        );
    }

    #[test]
    fn invalid_utf8_examples_are_in_the_pool_and_parseable() {
        let td = TrickyData::new();
        // The lone-surrogate literal made it into the string pool.
        assert!(
            td.strs.iter().any(|s| s == "'\\ud800'"),
            "expected the lone-surrogate literal in the pool"
        );
        // Every literal we emit — valid and reconstructed-invalid alike — is a
        // parseable Python string expression.
        for s in &td.strs {
            let src = format!("x = {s}\n");
            assert!(
                ruff_python_parser::parse_module(&src).is_ok(),
                "unparseable literal: {s:?}"
            );
        }
    }
}

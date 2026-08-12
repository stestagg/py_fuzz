//! `BigNum` — replace one numeric constant with a giant one.
//!
//! Numbers control allocation sizes, buffer lengths, loop counts and index math,
//! so blowing a modest constant up to something near (or far past) the ~2 GB
//! address space we fuzz under is a cheap way to probe overflow / OOM / slow
//! paths in the interpreter and any C libraries it reaches.
//!
//! Strategy (one edit per call):
//!   1. Walk the (possibly partial) AST and collect every integer and float
//!      literal, recording each one's byte range and whether it is a float.
//!      (Complex literals are left alone — "make it big" is less meaningful.)
//!   2. Pick one at random.
//!   3. Replace it with a randomly generated giant of the *same* numeric family:
//!        - **float** → a magnitude near the credible max exponent for f32 or
//!          f64 (Python floats are always f64, but C code reached through the
//!          interpreter may narrow to f32, so both regimes are worth hitting),
//!          negated roughly half the time.
//!        - **int** → one of two flavours, chosen at random: an *unevaluated*
//!          massive exponentiation like `1000**10000000` (tiny in source, but
//!          computing it eats memory/CPU far beyond the address space), or a
//!          concrete value sitting near the 2^31 / 2^32 / 2^63 boundaries that
//!          matter for allocation counts and index arithmetic. Occasionally
//!          negated.
//!
//! Every replacement is wrapped in parentheses so it stays a single atom in any
//! expression position (`a ** 5` → `a ** (-2147483648)`), and so a leading `-`
//! never re-associates with a preceding operator.

use ruff_python_ast::visitor::{walk_expr, Visitor};
use ruff_python_ast::{Expr, LiteralExpressionRef, Number};
use ruff_text_size::{Ranged, TextRange};

use crate::mutators::{walk_module, AstCtx, Edit, SubMutator};
use crate::rng::Rng;

/// Float magnitudes near the credible max (and min-normal) exponent for f32 and
/// f64. Deliberately weighted toward the largest values — the point is overflow.
static BIG_FLOATS: &[&str] = &[
    "3.4028235e38",           // ~f32::MAX
    "3.4028234663852886e38",  // f32::MAX (exact)
    "1e38",                   // near the f32 exponent ceiling
    "1.7976931348623157e308", // f64::MAX
    "8.98846567431158e307",   // ~f64::MAX / 2
    "1e308",                  // near the f64 exponent ceiling
];

/// "Truly massive" integers left *unevaluated* as inline exponentiations /
/// shifts: the source stays a few bytes, but evaluating them allocates or
/// computes an integer dwarfing the fuzzed 2 GB address space (or burns CPU
/// trying).
static HUGE_INT_EXPRS: &[&str] = &[
    "1000**10000000",
    "2**1000000000",
    "10**100000000",
    "9**99999999",
    "1<<10000000",
];

/// Concrete integers near / past the address-space boundaries that matter for
/// allocation sizes, buffer lengths and index math.
static BOUNDARY_INTS: &[&str] = &[
    "2147483647",           // 2**31 - 1  (INT_MAX)
    "2147483648",           // 2**31
    "4294967295",           // 2**32 - 1  (UINT_MAX)
    "4294967296",           // 2**32
    "9223372036854775807",  // 2**63 - 1  (LLONG_MAX)
    "18446744073709551615", // 2**64 - 1  (ULLONG_MAX)
    "2000000000",           // ~2 GB
    "0x7fffffff",           // INT_MAX in hex
];

/// A collected numeric literal: its byte range and whether it is a float (so we
/// replace it with a big float rather than a big int).
#[derive(Clone, Copy)]
struct NumberSite {
    range: TextRange,
    is_float: bool,
}

/// Preorder visitor that records every int / float literal (skipping complex).
struct NumberCollector {
    numbers: Vec<NumberSite>,
}

impl<'a> Visitor<'a> for NumberCollector {
    fn visit_expr(&mut self, expr: &'a Expr) {
        if let Some(LiteralExpressionRef::NumberLiteral(n)) = expr.as_literal_expr() {
            match n.value {
                Number::Int(_) => self.numbers.push(NumberSite {
                    range: expr.range(),
                    is_float: false,
                }),
                Number::Float(_) => self.numbers.push(NumberSite {
                    range: expr.range(),
                    is_float: true,
                }),
                // Making a complex constant "big" is not meaningful — leave it.
                Number::Complex { .. } => {}
            }
        }
        walk_expr(self, expr);
    }
}

/// Every int / float literal in the module, in source order.
fn number_sites(ctx: &AstCtx) -> Vec<NumberSite> {
    let mut collector = NumberCollector {
        numbers: Vec::new(),
    };
    walk_module(&mut collector, ctx.module);
    collector.numbers
}

pub struct BigNum;

impl BigNum {
    pub fn new() -> Self {
        BigNum
    }

    /// A parenthesized giant float, negated ~half the time.
    fn big_float(rng: &mut Rng) -> Option<String> {
        let mag = *rng.choose(BIG_FLOATS)?;
        let sign = if rng.index(2)? == 0 { "-" } else { "" };
        Some(format!("({sign}{mag})"))
    }

    /// A parenthesized giant int: an unevaluated massive power or a concrete
    /// boundary value, occasionally negated.
    fn big_int(rng: &mut Rng) -> Option<String> {
        // Flavour: 0 → unevaluated massive power, 1 → concrete boundary value.
        let base = if rng.index(2)? == 0 {
            *rng.choose(HUGE_INT_EXPRS)?
        } else {
            *rng.choose(BOUNDARY_INTS)?
        };
        // Negative roughly a third of the time. Wrap the base again so the `-`
        // binds the whole thing (`-(1000**10000000)`, not `(-1000)**...`).
        if rng.index(3)? == 0 {
            Some(format!("(-({base}))"))
        } else {
            Some(format!("({base})"))
        }
    }
}

impl Default for BigNum {
    fn default() -> Self {
        Self::new()
    }
}

impl SubMutator for BigNum {
    fn name(&self) -> &'static str {
        "bignum"
    }

    fn edit_space(&self, ctx: &AstCtx) -> usize {
        // Per site: one pool entry, optionally negated (see `big_float`/`big_int`).
        number_sites(ctx)
            .iter()
            .map(|site| {
                if site.is_float {
                    BIG_FLOATS.len() * 2
                } else {
                    (HUGE_INT_EXPRS.len() + BOUNDARY_INTS.len()) * 2
                }
            })
            .sum()
    }

    fn mutate(&self, ctx: &AstCtx, rng: &mut Rng) -> Option<Edit> {
        let site = *rng.choose(&number_sites(ctx))?;
        let replacement = if site.is_float {
            Self::big_float(rng)?
        } else {
            Self::big_int(rng)?
        };

        Some(Edit {
            range: site.range,
            kind: "bignum",
            replacement,
        })
    }
}

#[cfg(test)]
mod tests {
    /// Run only `bignum` over `src` for one seed, returning the mutated output.
    fn run(src: &str, seed: u32) -> Option<String> {
        let out = crate::mutate_with(src.as_bytes(), seed, 1 << 20, &["bignum"])?;
        Some(String::from_utf8(out).unwrap())
    }

    #[test]
    fn enlarges_an_integer_and_stays_parseable() {
        // Across seeds we should see a mix of massive-power and boundary ints,
        // and every result must re-parse without a syntax error.
        let mut saw_power = false;
        let mut saw_boundary = false;
        for seed in 0..128u32 {
            if let Some(out) = run("x = 5\n", seed) {
                assert!(out.starts_with("x = ("), "seed {seed}: {out:?}");
                if out.contains("**") || out.contains("<<") {
                    saw_power = true;
                }
                if out.contains("2147483647") || out.contains("2000000000") {
                    saw_boundary = true;
                }
                let parsed = ruff_python_parser::parse_module(&out);
                assert!(parsed.is_ok(), "seed {seed}: unparseable: {out:?}");
            }
        }
        assert!(saw_power, "expected some massive-power replacements");
        assert!(saw_boundary, "expected some boundary-value replacements");
    }

    #[test]
    fn floats_become_big_floats_not_ints() {
        // A float literal must be replaced by a float-shaped constant (has a `.`
        // or an exponent), never by an integer expression.
        let mut checked = false;
        for seed in 0..64u32 {
            if let Some(out) = run("y = 1.5\n", seed) {
                let replaced = out.trim_start_matches("y = ").trim();
                assert!(
                    replaced.contains('e') || replaced.contains('.'),
                    "seed {seed}: float not replaced by a float: {out:?}"
                );
                assert!(
                    !replaced.contains("**"),
                    "seed {seed}: got an int expr: {out:?}"
                );
                checked = true;
            }
        }
        assert!(checked, "expected the float to be mutated for some seed");
    }

    #[test]
    fn negative_floats_appear_for_some_seed() {
        let negated = (0..64u32).any(|seed| {
            run("y = 1.5\n", seed).is_some_and(|s| s.contains("(-") && s.contains('e'))
        });
        assert!(
            negated,
            "expected a negative float replacement across seeds"
        );
    }

    #[test]
    fn negative_ints_appear_for_some_seed() {
        let negated =
            (0..128u32).any(|seed| run("x = 5\n", seed).is_some_and(|s| s.contains("(-")));
        assert!(negated, "expected a negative int replacement across seeds");
    }

    #[test]
    fn no_numbers_yields_nothing() {
        assert!(run("x = y\n", 0).is_none());
    }
}

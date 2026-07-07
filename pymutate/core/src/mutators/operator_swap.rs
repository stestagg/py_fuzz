//! `OperatorSwap` — replace one operator token with a different operator of the
//! *same arity*.
//!
//! Strategy (one edit per call):
//!   1. Walk the (possibly partial) AST and collect every operator token site:
//!      binary operators (`BinOp`: `+ - * @ / // % ** << >> & | ^`), comparison
//!      operators (`Compare`: `< > <= >= == != is "is not" in "not in"`, one per
//!      link of a chained comparison), boolean operators (`BoolOp`: `and`/`or`,
//!      one per gap of an n-ary chain), and unary operators (`UnaryOp`:
//!      `+ - ~ not`). For each we locate the *token's* exact byte range.
//!   2. Pick one at random.
//!   3. Replace it with a candidate drawn from the compile-embedded
//!      `operators.dict` of the **same family** (infix vs. prefix), skipping any
//!      candidate equal to the token already there so the output actually changes.
//!
//! Why families? Swaps must preserve arity *and* operand validity, so we group
//! operators by the class of operand they accept:
//!   - `infix`  — arithmetic / bitwise / shift / comparison. All bind tighter
//!     than `not`, so their operands are always comparison-level-or-tighter and
//!     any of these tokens accepts any other's operands. Cross-swaps here never
//!     produce a syntax error; at worst they *regroup* (`x | y < z` → `x | y + z`
//!     reparses as `x | (y + z)`), which is itself useful new structure.
//!   - `bool`   — `and` / `or`. These sit *below* `not`/comparison in precedence,
//!     so their operands can be things like `not d` or `a < b` that are invalid
//!     as operands of a tighter operator. Swapping `a and not d` to `a < not d`
//!     is a `SyntaxError`, so `and`/`or` only ever swap with each other.
//!   - `prefix` — unary `+ - ~ not` (arity 1); interchangeable with each other
//!     but never with a binary token.
//!   - `assign` — the statement-level assignment operators `= += -= *= @= /= //=
//!     %= **= <<= >>= &= |= ^=`. A plain `a = 1` can become `a **= 1` and vice
//!     versa, exercising the in-place (`__ipow__`, …) vs. plain-store paths and
//!     the load-of-an-unbound-name path. Only single, *simple* targets (Name /
//!     Attribute / Subscript) qualify: `a, b = …` and `a = b = …` have no
//!     augmented form, so those `=` tokens are left alone.
//!
//! Each family still jumps between wildly different C slots (`nb_add`,
//! `nb_matrix_multiply`, `tp_richcompare`, short-circuit `__bool__`,
//! `nb_inplace_add`, …).
//!
//! Ruff does not give operator *tokens* their own range (only the whole
//! expression and its operands are ranged), so we locate each token by searching
//! for its canonical spelling in the gap between the surrounding operands. A site
//! whose token can't be located (e.g. an unusual `is    not` spelling, or an
//! operator hidden by a comment continuation) is simply dropped from the pool.

use ruff_python_ast::visitor::{walk_expr, walk_stmt, Visitor};
use ruff_python_ast::{Expr, Stmt};
use ruff_text_size::{Ranged, TextRange, TextSize};

use crate::mutators::{walk_module, AstCtx, Edit, SubMutator};
use crate::rng::Rng;

/// Curated candidate operators, baked into the binary. See the file header of
/// `operators.dict` for the `<family>: <token>` line format.
static OPERATORS_DICT: &str = include_str!("../operators.dict");

/// Arity class of an operator — used to guarantee a swap stays parseable.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Family {
    /// Binary infix: arithmetic, bitwise, shift, comparison.
    Infix,
    /// Boolean infix: `and` / `or` (isolated — their operands can be lower
    /// precedence than any other binary operator accepts).
    Bool,
    /// Unary prefix: `+ - ~ not`.
    Prefix,
    /// Statement assignment operators: `= += -= … ^=`.
    Assign,
}

impl Family {
    /// Parse a dict-file family keyword, or `None` if unrecognised.
    fn from_tag(tag: &str) -> Option<Family> {
        Some(match tag {
            "infix" => Family::Infix,
            "bool" => Family::Bool,
            "prefix" => Family::Prefix,
            "assign" => Family::Assign,
            _ => return None,
        })
    }
}

/// Is `expr` a target that a *single* augmented assignment accepts? Tuple / list
/// / starred targets have no augmented form, so we must not offer `=` -> `+=` for
/// them.
fn is_simple_target(expr: &Expr) -> bool {
    matches!(
        expr,
        Expr::Name(_) | Expr::Attribute(_) | Expr::Subscript(_)
    )
}

/// A replacement operator token plus the family it belongs to.
struct Candidate {
    family: Family,
    token: &'static str,
}

/// Parse one dict line into a [`Candidate`], or `None` if it is not a recognised
/// `<family>: <token>` line. Untagged lines are dropped: an operator with an
/// unknown arity can't be swapped in safely.
fn parse_line(line: &'static str) -> Option<Candidate> {
    let (head, rest) = line.split_once(':')?;
    let family = Family::from_tag(head.trim())?;
    Some(Candidate {
        family,
        token: rest.trim(),
    })
}

/// Parse `operators.dict` into candidates (skip blanks, `#` comments, and any
/// untagged lines).
fn load_candidates() -> Vec<Candidate> {
    OPERATORS_DICT
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
        .filter_map(parse_line)
        .collect()
}

/// A located operator token in the source.
struct OpSite {
    /// Byte range of just the operator token (not its operands).
    range: TextRange,
    family: Family,
}

/// Find `needle` within `source[lo..hi]` and return its absolute byte range, or
/// `None` if the slice is out of bounds or the token isn't present.
fn locate(source: &str, lo: usize, hi: usize, needle: &str) -> Option<TextRange> {
    let hay = source.get(lo..hi)?;
    let rel = hay.find(needle)?;
    let start = TextSize::try_from(lo + rel).ok()?;
    let end = TextSize::try_from(lo + rel + needle.len()).ok()?;
    Some(TextRange::new(start, end))
}

/// Preorder visitor that records every locatable operator token site.
struct OpCollector<'a> {
    source: &'a str,
    sites: Vec<OpSite>,
}

impl<'a> OpCollector<'a> {
    /// Record a site for `needle` found between byte offsets `lo` and `hi`.
    fn push(&mut self, lo: usize, hi: usize, needle: &str, family: Family) {
        if let Some(range) = locate(self.source, lo, hi, needle) {
            self.sites.push(OpSite { range, family });
        }
    }
}

impl<'a> Visitor<'a> for OpCollector<'a> {
    fn visit_stmt(&mut self, stmt: &'a Stmt) {
        match stmt {
            Stmt::Assign(s) => {
                // Only a single, simple target has an augmented form: `a, b = …`
                // and `a = b = …` can't become `+=`, so leave their `=` alone.
                if let [target] = s.targets.as_slice() {
                    if is_simple_target(target) {
                        let lo = usize::from(target.range().end());
                        let hi = usize::from(s.value.range().start());
                        self.push(lo, hi, "=", Family::Assign);
                    }
                }
            }
            Stmt::AugAssign(s) => {
                // The target is already a simple assignable, so swapping to `=` or
                // any other augmented op is always valid. The source token is the
                // operator plus `=` (`+` -> `+=`, `**` -> `**=`).
                let lo = usize::from(s.target.range().end());
                let hi = usize::from(s.value.range().start());
                self.push(lo, hi, &format!("{}=", s.op.as_str()), Family::Assign);
            }
            _ => {}
        }
        walk_stmt(self, stmt);
    }

    fn visit_expr(&mut self, expr: &'a Expr) {
        match expr {
            Expr::BinOp(e) => {
                // The operator lives between the two operands.
                let lo = usize::from(e.left.range().end());
                let hi = usize::from(e.right.range().start());
                self.push(lo, hi, e.op.as_str(), Family::Infix);
            }
            Expr::UnaryOp(e) => {
                // The operator is a prefix before the operand.
                let lo = usize::from(e.range().start());
                let hi = usize::from(e.operand.range().start());
                self.push(lo, hi, e.op.as_str(), Family::Prefix);
            }
            Expr::BoolOp(e) => {
                // One `and`/`or` token per gap between consecutive values.
                for pair in e.values.windows(2) {
                    let lo = usize::from(pair[0].range().end());
                    let hi = usize::from(pair[1].range().start());
                    self.push(lo, hi, e.op.as_str(), Family::Bool);
                }
            }
            Expr::Compare(e) => {
                // One op token per link of the (possibly chained) comparison.
                let mut lo = usize::from(e.left.range().end());
                for (op, comparator) in e.ops.iter().zip(e.comparators.iter()) {
                    let hi = usize::from(comparator.range().start());
                    self.push(lo, hi, op.as_str(), Family::Infix);
                    lo = usize::from(comparator.range().end());
                }
            }
            _ => {}
        }
        walk_expr(self, expr);
    }
}

pub struct OperatorSwap {
    candidates: Vec<Candidate>,
}

impl OperatorSwap {
    pub fn new() -> Self {
        Self {
            candidates: load_candidates(),
        }
    }
}

impl Default for OperatorSwap {
    fn default() -> Self {
        Self::new()
    }
}

impl SubMutator for OperatorSwap {
    fn name(&self) -> &'static str {
        "operator_swap"
    }

    fn mutate(&self, ctx: &AstCtx, rng: &mut Rng) -> Option<Edit> {
        let mut collector = OpCollector {
            source: ctx.source,
            sites: Vec::new(),
        };
        walk_module(&mut collector, ctx.module);

        // Pick one operator site and note its current token + family.
        let site = rng.choose(&collector.sites)?;
        let start = usize::from(site.range.start());
        let end = usize::from(site.range.end());
        let current = &ctx.source[start..end];

        // Scan the candidate pool from a random offset for one of the same family
        // whose spelling differs. Seeded, and never a same-token no-op.
        let n = self.candidates.len();
        let start_idx = rng.index(n)?;
        for k in 0..n {
            let cand = &self.candidates[(start_idx + k) % n];
            if cand.family == site.family && cand.token != current {
                return Some(Edit {
                    range: site.range,
                    kind: "operator_swap",
                    replacement: cand.token.to_string(),
                });
            }
        }
        // No differing same-family candidate (degenerate dict) — no-op.
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_line_recognises_families() {
        let c = parse_line("infix: +").unwrap();
        assert_eq!(c.family, Family::Infix);
        assert_eq!(c.token, "+");

        let c = parse_line("prefix: not").unwrap();
        assert_eq!(c.family, Family::Prefix);
        assert_eq!(c.token, "not");
    }

    #[test]
    fn parse_line_keeps_multiword_tokens_whole() {
        let c = parse_line("infix: is not").unwrap();
        assert_eq!(c.family, Family::Infix);
        assert_eq!(c.token, "is not");
    }

    #[test]
    fn parse_line_drops_untagged_lines() {
        assert!(parse_line("+").is_none());
        assert!(parse_line("wat: +").is_none());
    }

    #[test]
    fn dict_has_all_families() {
        let cands = load_candidates();
        assert!(cands.iter().any(|c| c.family == Family::Infix));
        assert!(cands.iter().any(|c| c.family == Family::Bool));
        assert!(cands.iter().any(|c| c.family == Family::Prefix));
        assert!(cands.iter().any(|c| c.family == Family::Assign));
    }

    #[test]
    fn locate_finds_token_in_gap() {
        let src = "aa < bb";
        // gap between operands is bytes 2..5 ("  <  " region: " < ").
        let r = locate(src, 2, 5, "<").unwrap();
        assert_eq!(&src[usize::from(r.start())..usize::from(r.end())], "<");
    }
}

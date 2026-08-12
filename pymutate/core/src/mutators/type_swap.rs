//! `TypeSwap` — replace one literal node with a literal of a *different* type.
//!
//! Strategy (one edit per call):
//!   1. Walk the (possibly partial) AST and collect every literal expression
//!      (`None`, `True`/`False`, `...`, numbers, strings, bytes), recording each
//!      one's byte range and its [`LitKind`].
//!   2. Pick one at random.
//!   3. Replace it with a candidate snippet drawn from the compile-embedded
//!      `literals.dict` whose kind differs from the picked literal's — so we
//!      never swap a string for another string. Untagged candidates (kind
//!      unknown) are treated as always-different and so are always eligible.
//!
//! The candidate pool is deliberately broader than the set of AST literal kinds:
//! it also carries `list`/`tuple`/`dict`/`set` and arbitrary untagged
//! expressions, so a scalar literal can become a container (and exercise very
//! different C paths) even though those are not themselves literal nodes.

use ruff_python_ast::visitor::{walk_expr, Visitor};
use ruff_python_ast::{Expr, LiteralExpressionRef, Number};
use ruff_text_size::{Ranged, TextRange};

use crate::mutators::{walk_module, AstCtx, Edit, SubMutator};
use crate::rng::Rng;

/// Curated candidate literals, baked into the binary. See the file header for
/// the (optional) `<tag>: ` line format.
static LITERALS_DICT: &str = include_str!("../literals.dict");

/// The type category of a literal — used to guarantee a swap changes the type.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum LitKind {
    None,
    Bool,
    Int,
    Float,
    Complex,
    Str,
    Bytes,
    Ellipsis,
    List,
    Tuple,
    Dict,
    Set,
}

impl LitKind {
    /// Parse a dict-file tag keyword into a kind, or `None` if unrecognised.
    fn from_tag(tag: &str) -> Option<LitKind> {
        Some(match tag {
            "none" => LitKind::None,
            "bool" => LitKind::Bool,
            "int" => LitKind::Int,
            "float" => LitKind::Float,
            "complex" => LitKind::Complex,
            "str" => LitKind::Str,
            "bytes" => LitKind::Bytes,
            "ellipsis" => LitKind::Ellipsis,
            "list" => LitKind::List,
            "tuple" => LitKind::Tuple,
            "dict" => LitKind::Dict,
            "set" => LitKind::Set,
            _ => return None,
        })
    }
}

/// Classify a literal AST node into its [`LitKind`].
fn kind_of(lit: LiteralExpressionRef) -> LitKind {
    match lit {
        LiteralExpressionRef::NoneLiteral(_) => LitKind::None,
        LiteralExpressionRef::BooleanLiteral(_) => LitKind::Bool,
        LiteralExpressionRef::EllipsisLiteral(_) => LitKind::Ellipsis,
        LiteralExpressionRef::StringLiteral(_) => LitKind::Str,
        LiteralExpressionRef::BytesLiteral(_) => LitKind::Bytes,
        LiteralExpressionRef::NumberLiteral(n) => match n.value {
            Number::Int(_) => LitKind::Int,
            Number::Float(_) => LitKind::Float,
            Number::Complex { .. } => LitKind::Complex,
        },
    }
}

/// A replacement snippet plus the kind it represents (`None` = untagged, i.e.
/// treated as different from every literal kind).
struct Candidate {
    kind: Option<LitKind>,
    snippet: &'static str,
}

/// Parse one dict line into a [`Candidate`]. A line is *tagged* only when the
/// text before its first `:` is a recognised kind keyword (`int`, `str`, …);
/// otherwise the whole line is the (untagged) snippet — which is what keeps
/// colon-bearing snippets like `{1: 2}` intact.
fn parse_line(line: &'static str) -> Candidate {
    if let Some((head, rest)) = line.split_once(':') {
        if let Some(kind) = LitKind::from_tag(head.trim()) {
            return Candidate {
                kind: Some(kind),
                snippet: rest.trim(),
            };
        }
    }
    Candidate {
        kind: None,
        snippet: line.trim(),
    }
}

/// Parse `literals.dict` into candidates (skip blanks and `#` comments).
fn load_candidates() -> Vec<Candidate> {
    LITERALS_DICT
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
        .map(parse_line)
        .collect()
}

/// Preorder visitor that records the byte range and kind of every literal.
struct LiteralCollector {
    literals: Vec<(TextRange, LitKind)>,
}

impl<'a> Visitor<'a> for LiteralCollector {
    fn visit_expr(&mut self, expr: &'a Expr) {
        if let Some(lit) = expr.as_literal_expr() {
            self.literals.push((expr.range(), kind_of(lit)));
        }
        walk_expr(self, expr);
    }
}

/// Every literal in the module paired with its kind, in source order.
fn literal_sites(ctx: &AstCtx) -> Vec<(TextRange, LitKind)> {
    let mut collector = LiteralCollector {
        literals: Vec::new(),
    };
    walk_module(&mut collector, ctx.module);
    collector.literals
}

pub struct TypeSwap {
    candidates: Vec<Candidate>,
}

impl TypeSwap {
    pub fn new() -> Self {
        Self {
            candidates: load_candidates(),
        }
    }
}

impl Default for TypeSwap {
    fn default() -> Self {
        Self::new()
    }
}

impl SubMutator for TypeSwap {
    fn name(&self) -> &'static str {
        "type_swap"
    }

    fn edit_space(&self, ctx: &AstCtx) -> usize {
        // Only candidates of a *different* kind are legal swaps, so count per site.
        literal_sites(ctx)
            .iter()
            .map(|(_, src_kind)| {
                self.candidates
                    .iter()
                    .filter(|cand| cand.kind != Some(*src_kind))
                    .count()
            })
            .sum()
    }

    fn mutate(&self, ctx: &AstCtx, rng: &mut Rng) -> Option<Edit> {
        // Pick one literal node and note its kind.
        let (range, src_kind) = *rng.choose(&literal_sites(ctx))?;

        // Scan the candidate pool from a random offset for one whose kind
        // differs (untagged candidates count as different). Seeded, and never a
        // same-kind swap.
        let n = self.candidates.len();
        let start_idx = rng.index(n)?;
        for k in 0..n {
            let cand = &self.candidates[(start_idx + k) % n];
            if cand.kind != Some(src_kind) {
                return Some(Edit {
                    range,
                    kind: "type_swap",
                    replacement: cand.snippet.to_string(),
                });
            }
        }
        // Every candidate is the same kind as the source (degenerate dict).
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_line_recognises_tags() {
        let c = parse_line("int: 42");
        assert_eq!(c.kind, Some(LitKind::Int));
        assert_eq!(c.snippet, "42");
    }

    #[test]
    fn parse_line_leaves_untagged_snippets_whole() {
        // `{1` is not a tag keyword, so the colon inside the dict is preserved.
        let c = parse_line("{1: 2}");
        assert_eq!(c.kind, None);
        assert_eq!(c.snippet, "{1: 2}");
    }

    #[test]
    fn dict_is_nonempty_and_has_untagged_entries() {
        let cands = load_candidates();
        assert!(!cands.is_empty());
        // At least one untagged entry keeps every literal kind swappable even
        // when the dict happens to hold only same-kind tagged candidates.
        assert!(cands.iter().any(|c| c.kind.is_none()));
    }
}

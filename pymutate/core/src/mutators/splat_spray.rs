//! `SplatSpray` — sprinkle `*` / `**` unpacking operators into places they mostly
//! don't belong.
//!
//! Unlike the other sub-mutators, this one is *deliberately* happy to produce
//! syntactically invalid Python: the whole point is to jam splats into unusual
//! positions and see what the parser / compiler / AST-builder do with them
//! (starred assignment targets, starred subscripts, `**` inside a list display,
//! bare `*name`, …). Many outputs are `SyntaxError`s — cheap to reject — but the
//! ones that *do* parse tend to hit rarely-exercised grammar productions.
//!
//! Two flavours of edit, one chosen per call (deterministically, from a single
//! flat candidate space so the pick is uniform across everything on offer):
//!
//!   1. **Prefix injection** — insert `*` or `**` immediately before some node.
//!      We collect the start offset of every statement, expression, and bare
//!      identifier (via the source-order visitor, same as `name_subst`, so we
//!      reach attribute names, keyword-arg names, parameters, …). Any of those
//!      becomes an anchor: `x.attr` → `x.*attr`, `return y` → `*return y`,
//!      `f(a)` → `**f(a)`, and so on.
//!
//!   2. **Container append** — for a non-empty `[...]` list or `(...)`/bare tuple,
//!      append a fresh splatted element after the last one: `[1, 2]` → `[1, 2,
//!      *[1]]` (valid — iterable unpacking in a display) or `[1, 2, **{'x': 1}]`
//!      (invalid — `**` isn't allowed in a list/tuple display, which is exactly
//!      the sort of "shouldn't be here" we want).
//!
//! Every edit is a pure insertion (a zero-width range), so nothing existing is
//! disturbed — the change is maximally attributable.

use ruff_python_ast::visitor::source_order::{
    walk_expr, walk_module, walk_stmt, SourceOrderVisitor,
};
use ruff_python_ast::{Expr, Identifier, Stmt};
use ruff_text_size::{Ranged, TextRange, TextSize};

use crate::mutators::{AstCtx, Edit, SubMutator};
use crate::rng::Rng;

/// The two prefix splats, indexed by the low bit of the candidate index.
const PREFIXES: [&str; 2] = ["*", "**"];
/// The two container-append snippets (leading `, ` so they follow the last elt).
const APPENDS: [&str; 2] = [", *[1]", ", **{'x': 1}"];

/// Source-order visitor collecting every prefix anchor (node start offsets) and
/// every container-append anchor (offset just past a list/tuple's last element).
struct SplatCollector {
    /// Byte offsets at which a `*`/`**` could be inserted.
    prefixes: Vec<TextSize>,
    /// Byte offsets (just after a list/tuple's final element) at which a new
    /// splatted element could be appended.
    appends: Vec<TextSize>,
}

impl<'a> SourceOrderVisitor<'a> for SplatCollector {
    fn visit_stmt(&mut self, stmt: &'a Stmt) {
        self.prefixes.push(stmt.range().start());
        walk_stmt(self, stmt);
    }

    fn visit_expr(&mut self, expr: &'a Expr) {
        self.prefixes.push(expr.range().start());
        match expr {
            Expr::List(e) => {
                if let Some(last) = e.elts.last() {
                    self.appends.push(last.range().end());
                }
            }
            Expr::Tuple(e) => {
                if let Some(last) = e.elts.last() {
                    self.appends.push(last.range().end());
                }
            }
            _ => {}
        }
        walk_expr(self, expr);
    }

    fn visit_identifier(&mut self, identifier: &'a Identifier) {
        self.prefixes.push(identifier.range().start());
    }
}

pub struct SplatSpray;

impl SplatSpray {
    pub fn new() -> Self {
        SplatSpray
    }
}

impl Default for SplatSpray {
    fn default() -> Self {
        Self::new()
    }
}

impl SubMutator for SplatSpray {
    fn name(&self) -> &'static str {
        "splat_spray"
    }

    fn mutate(&self, ctx: &AstCtx, rng: &mut Rng) -> Option<Edit> {
        let mut collector = SplatCollector {
            prefixes: Vec::new(),
            appends: Vec::new(),
        };
        walk_module(&mut collector, ctx.module);

        // Distinct prefix anchors only, so positions where several nodes start at
        // the same offset (e.g. a statement and its first expression) aren't
        // over-weighted.
        collector.prefixes.sort_unstable();
        collector.prefixes.dedup();

        // One flat candidate space: each prefix anchor offers `*` and `**`; each
        // container anchor offers `*[1]` and `**{'x': 1}`. Pick one uniformly.
        let n_prefix = collector.prefixes.len() * PREFIXES.len();
        let n_append = collector.appends.len() * APPENDS.len();
        let idx = rng.index(n_prefix + n_append)?;

        if idx < n_prefix {
            let offset = collector.prefixes[idx / PREFIXES.len()];
            let replacement = PREFIXES[idx % PREFIXES.len()].to_string();
            Some(Edit {
                range: TextRange::empty(offset),
                kind: "splat_spray",
                replacement,
            })
        } else {
            let j = idx - n_prefix;
            let offset = collector.appends[j / APPENDS.len()];
            let replacement = APPENDS[j % APPENDS.len()].to_string();
            Some(Edit {
                range: TextRange::empty(offset),
                kind: "splat_spray",
                replacement,
            })
        }
    }
}

#[cfg(test)]
mod tests {
    /// Run only this sub-mutator over `src` for one seed (via the real driver,
    /// restricted to `splat_spray`), returning the mutated output as a string.
    fn run(src: &str, seed: u32) -> Option<String> {
        let out = crate::mutate_with(src.as_bytes(), seed, 1 << 20, &["splat_spray"])?;
        Some(String::from_utf8(out).unwrap())
    }

    #[test]
    fn always_inserts_a_star() {
        // Every edit adds a `*` (prefix or append), never removes anything.
        for seed in 0..64u32 {
            if let Some(out) = run("[a, b]\n", seed) {
                assert!(out.contains('*'), "seed {seed}: no splat inserted: {out:?}");
                assert!(out.len() > "[a, b]\n".len(), "seed {seed}: not an insertion");
            }
        }
    }

    #[test]
    fn appends_a_splatted_element_for_some_seed() {
        let appended = (0..128u32).any(|seed| {
            run("[a, b]\n", seed).is_some_and(|s| s.contains("*[1]") || s.contains("**{'x': 1}"))
        });
        assert!(appended, "expected a container-append edit across seeds");
    }

    #[test]
    fn empty_input_yields_nothing() {
        assert!(run("", 0).is_none());
    }
}

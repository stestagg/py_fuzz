//! `AttrWrap` — wrap one name reference in an attribute access (and maybe a
//! call).
//!
//! Strategy (one edit per call):
//!   1. Walk the AST and collect every `Name` **expression** (a variable/function
//!      *reference* — not the bare `Identifier` positions that `name_subst` also
//!      grabs, since attaching `.attr` to a `def` name or keyword-arg name would
//!      just be a syntax error).
//!   2. Pick one at random.
//!   3. Replace it with one of two shapes, drawing the attribute from the
//!      compile-embedded `names.dict`:
//!        - `(name.attr)`   — attribute access
//!        - `(name.attr())` — attribute access *and* call
//!
//! The point is to route otherwise-inert names through `__getattribute__` /
//! descriptor / `__call__` machinery: `x` becomes `(x.__class__)` or
//! `(x.__reduce__())`, etc. The whole thing is parenthesized so it stays a single
//! atom in any expression position and never re-associates with a neighbour.

use ruff_python_ast::visitor::{walk_expr, Visitor};
use ruff_python_ast::Expr;
use ruff_text_size::{Ranged, TextRange};

use crate::mutators::{load_name_dict, walk_module, AstCtx, Edit, SubMutator};
use crate::rng::Rng;

/// The two wrap shapes, indexed by the low bit of the candidate index. `{name}`
/// / `{attr}` are filled in per pick.
const SHAPES: [&str; 2] = ["({name}.{attr})", "({name}.{attr}())"];

/// Preorder visitor recording the byte range of every `Name` reference.
struct NameRefCollector {
    ranges: Vec<TextRange>,
}

impl<'a> Visitor<'a> for NameRefCollector {
    fn visit_expr(&mut self, expr: &'a Expr) {
        if let Expr::Name(name) = expr {
            self.ranges.push(name.range());
        }
        walk_expr(self, expr);
    }
}

pub struct AttrWrap {
    attrs: Vec<&'static str>,
}

impl AttrWrap {
    pub fn new() -> Self {
        // The dict carries the keyword constants `None`/`True`/`False` (fine as a
        // whole-name substitution, but `x.True` is a syntax error) — drop them so
        // every wrap yields a legal attribute name.
        let attrs = load_name_dict()
            .into_iter()
            .filter(|c| !matches!(*c, "None" | "True" | "False"))
            .collect();
        Self { attrs }
    }
}

impl Default for AttrWrap {
    fn default() -> Self {
        Self::new()
    }
}

impl SubMutator for AttrWrap {
    fn name(&self) -> &'static str {
        "attr_wrap"
    }

    fn mutate(&self, ctx: &AstCtx, rng: &mut Rng) -> Option<Edit> {
        let mut collector = NameRefCollector { ranges: Vec::new() };
        walk_module(&mut collector, ctx.module);

        // One flat candidate space: each Name offers both wrap shapes. Pick one.
        let n = collector.ranges.len() * SHAPES.len();
        let idx = rng.index(n)?;
        let range = collector.ranges[idx / SHAPES.len()];
        let shape = SHAPES[idx % SHAPES.len()];

        let name = &ctx.source[usize::from(range.start())..usize::from(range.end())];
        let attr = *rng.choose(&self.attrs)?;
        let replacement = shape.replace("{name}", name).replace("{attr}", attr);

        Some(Edit {
            range,
            kind: "attr_wrap",
            replacement,
        })
    }
}

#[cfg(test)]
mod tests {
    /// Run only `attr_wrap` over `src` for one seed, returning the mutated output.
    fn run(src: &str, seed: u32) -> Option<String> {
        let out = crate::mutate_with(src.as_bytes(), seed, 1 << 20, &["attr_wrap"])?;
        Some(String::from_utf8(out).unwrap())
    }

    #[test]
    fn wraps_a_name_and_stays_parseable() {
        // `x` should become `(x.<attr>)` or `(x.<attr>())`; both must re-parse.
        let mut saw_attr = false;
        let mut saw_call = false;
        for seed in 0..128u32 {
            if let Some(out) = run("value\n", seed) {
                assert!(out.starts_with("(value."), "seed {seed}: {out:?}");
                if out.contains("())") {
                    saw_call = true;
                } else {
                    saw_attr = true;
                }
                assert!(
                    ruff_python_parser::parse_module(&out).is_ok(),
                    "seed {seed}: unparseable: {out:?}"
                );
            }
        }
        assert!(saw_attr, "expected a plain-attribute wrap across seeds");
        assert!(saw_call, "expected an attribute-call wrap across seeds");
    }

    #[test]
    fn no_names_yields_nothing() {
        // A lone literal has no Name references to wrap.
        assert!(run("42\n", 0).is_none());
    }
}

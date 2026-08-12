//! `SelfRebind` — insert a `name = name` self-assignment.
//!
//! Rebinding a name to itself is a no-op semantically but churns the reference
//! count and re-runs the store/load machinery — cheap ref-count / lifetime bait,
//! and it also creates a redundant binding the compiler must still handle.
//!
//! Strategy (one edit per call):
//!   1. Walk the AST collecting every statement range and every `Name` reference.
//!   2. For a chosen name, find its **smallest enclosing statement** (its leaf
//!      statement) and splice `"{name} = {name}"` as a sibling right after it, at
//!      that statement's indentation. Anchoring to the leaf keeps the rebind in
//!      the same block — and therefore the same scope — as the name it copies.
//!
//! Names whose leaf statement isn't line-leading (inline bodies like
//! `if c: use(x)`) are skipped — see [`crate::mutators::line_indent`].

use ruff_python_ast::visitor::{walk_expr, walk_stmt, Visitor};
use ruff_python_ast::{Expr, Stmt};
use ruff_text_size::{Ranged, TextRange};

use crate::mutators::{line_indent, walk_module, AstCtx, Edit, SubMutator};
use crate::rng::Rng;

/// Preorder visitor collecting statement ranges and `Name` reference ranges.
struct Collector {
    stmts: Vec<TextRange>,
    names: Vec<TextRange>,
}

impl<'a> Visitor<'a> for Collector {
    fn visit_stmt(&mut self, stmt: &'a Stmt) {
        self.stmts.push(stmt.range());
        walk_stmt(self, stmt);
    }

    fn visit_expr(&mut self, expr: &'a Expr) {
        if let Expr::Name(name) = expr {
            self.names.push(name.range());
        }
        walk_expr(self, expr);
    }
}

/// Every rebind candidate: a name, its smallest enclosing line-leading
/// statement (where the rebind is anchored), and that statement's indent.
fn rebind_sites<'a>(ctx: &AstCtx<'a>) -> Vec<(TextRange, TextRange, &'a str)> {
    let mut c = Collector {
        stmts: Vec::new(),
        names: Vec::new(),
    };
    walk_module(&mut c, ctx.module);

    c.names
        .iter()
        .filter_map(|&nr| {
            let stmt = c
                .stmts
                .iter()
                .filter(|s| s.start() <= nr.start() && nr.end() <= s.end())
                .min_by_key(|s| s.len())?;
            let indent = line_indent(ctx.source, usize::from(stmt.start()))?;
            Some((nr, *stmt, indent))
        })
        .collect()
}

pub struct SelfRebind;

impl SelfRebind {
    pub fn new() -> Self {
        SelfRebind
    }
}

impl Default for SelfRebind {
    fn default() -> Self {
        Self::new()
    }
}

impl SubMutator for SelfRebind {
    fn name(&self) -> &'static str {
        "self_rebind"
    }

    fn edit_space(&self, ctx: &AstCtx) -> usize {
        // The rebind text is fixed by the name, so each site yields one edit.
        rebind_sites(ctx).len()
    }

    fn mutate(&self, ctx: &AstCtx, rng: &mut Rng) -> Option<Edit> {
        let &(nr, stmt, indent) = rng.choose(&rebind_sites(ctx))?;
        let name = &ctx.source[usize::from(nr.start())..usize::from(nr.end())];

        Some(Edit {
            range: TextRange::empty(stmt.end()),
            kind: "self_rebind",
            replacement: format!("\n{indent}{name} = {name}"),
        })
    }
}

#[cfg(test)]
mod tests {
    /// Run only `self_rebind` over `src` for one seed, returning the output.
    fn run(src: &str, seed: u32) -> Option<String> {
        let out = crate::mutate_with(src.as_bytes(), seed, 1 << 20, &["self_rebind"])?;
        Some(String::from_utf8(out).unwrap())
    }

    #[test]
    fn inserts_a_self_rebind_and_stays_parseable() {
        // `x = compute()` has names `x` and `compute`; a rebind of either is valid.
        let mut hit = false;
        for seed in 0..64u32 {
            if let Some(out) = run("x = compute()\n", seed) {
                assert!(
                    out.contains("x = x") || out.contains("compute = compute"),
                    "seed {seed}: no self-rebind: {out:?}"
                );
                assert!(
                    ruff_python_parser::parse_module(&out).is_ok(),
                    "seed {seed}: unparseable: {out:?}"
                );
                hit = true;
            }
        }
        assert!(hit, "expected self_rebind to fire");
    }

    #[test]
    fn indents_the_rebind_inside_a_function() {
        let hit = (0..64u32).any(|seed| {
            run("def f():\n    y = g()\n", seed).is_some_and(|s| s.contains("    y = y"))
        });
        assert!(hit, "expected an indented `y = y` inside the function");
    }

    #[test]
    fn no_names_yields_nothing() {
        assert!(run("42\n", 0).is_none());
    }
}

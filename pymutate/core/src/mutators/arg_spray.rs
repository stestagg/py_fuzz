//! `ArgSpray` — append one extra positional argument to a call.
//!
//! `splat_spray` appends to list/tuple *displays*; calls are left untouched, yet
//! argument parsing (and the C-level fast paths for 0-/1-/N-arg calls) is a rich
//! target. This mutator adds a fresh trailing arg to a call site:
//!   - `f()`   → `f(len)`      (0-arg fast path → general path)
//!   - `f(a)`  → `f(a, len)`
//!   - `g(a=1)`→ `g(a=1, len)` (a positional arg after a keyword — a SyntaxError,
//!     which is exactly the sort of edge we want to probe)
//!
//! Strategy (one edit per call): walk the AST for `Call` nodes, pick one, and
//! insert an identifier drawn from the compile-embedded `names.dict` just before
//! the closing `)`. It is a pure insertion (zero-width range).

use ruff_python_ast::visitor::{walk_expr, Visitor};
use ruff_python_ast::Expr;
use ruff_text_size::{Ranged, TextSize};

use crate::mutators::{load_name_dict, walk_module, AstCtx, Edit, SubMutator};
use crate::rng::Rng;

/// A collected call site: the byte offset just before its closing `)` and
/// whether it already has any arguments (so we know to prepend a `, `).
#[derive(Clone, Copy)]
struct CallSite {
    /// Offset of the closing `)` — the new argument is inserted here.
    insert_at: TextSize,
    has_args: bool,
}

/// Preorder visitor recording every call site.
struct CallCollector {
    calls: Vec<CallSite>,
}

impl<'a> Visitor<'a> for CallCollector {
    fn visit_expr(&mut self, expr: &'a Expr) {
        if let Expr::Call(call) = expr {
            // `arguments.range()` spans the parenthesized `(...)`, so its end is
            // one past the `)`; step back one byte to land just inside it.
            let close = call.arguments.range().end() - TextSize::from(1);
            self.calls.push(CallSite {
                insert_at: close,
                has_args: !call.arguments.is_empty(),
            });
        }
        walk_expr(self, expr);
    }
}

/// Every call site in the module, in source order.
fn call_sites(ctx: &AstCtx) -> Vec<CallSite> {
    let mut collector = CallCollector { calls: Vec::new() };
    walk_module(&mut collector, ctx.module);
    collector.calls
}

pub struct ArgSpray {
    args: Vec<String>,
}

impl ArgSpray {
    pub fn new() -> Self {
        Self::with_extra_names(&[])
    }

    pub fn with_extra_names(extra_names: &[String]) -> Self {
        Self {
            args: load_name_dict(extra_names),
        }
    }
}

impl Default for ArgSpray {
    fn default() -> Self {
        Self::new()
    }
}

impl SubMutator for ArgSpray {
    fn name(&self) -> &'static str {
        "arg_spray"
    }

    fn edit_space(&self, ctx: &AstCtx) -> usize {
        call_sites(ctx).len() * self.args.len()
    }

    fn mutate(&self, ctx: &AstCtx, rng: &mut Rng) -> Option<Edit> {
        let site = *rng.choose(&call_sites(ctx))?;
        let arg = rng.choose(&self.args)?;
        let replacement = if site.has_args {
            format!(", {arg}")
        } else {
            arg.clone()
        };

        Some(Edit {
            range: ruff_text_size::TextRange::empty(site.insert_at),
            kind: "arg_spray",
            replacement,
        })
    }
}

#[cfg(test)]
mod tests {
    /// Run only `arg_spray` over `src` for one seed, returning the mutated output.
    fn run(src: &str, seed: u32) -> Option<String> {
        let out = crate::mutate_with(src.as_bytes(), seed, 1 << 20, &["arg_spray"])?;
        Some(String::from_utf8(out).unwrap())
    }

    #[test]
    fn adds_a_trailing_arg_to_a_call_with_args() {
        // `f(a)` → `f(a, <name>)`, still parseable.
        for seed in 0..32u32 {
            if let Some(out) = run("f(a)\n", seed) {
                assert!(out.starts_with("f(a, "), "seed {seed}: {out:?}");
                assert!(out.len() > "f(a)\n".len(), "seed {seed}: not an insertion");
                assert!(
                    ruff_python_parser::parse_module(&out).is_ok(),
                    "seed {seed}: unparseable: {out:?}"
                );
            }
        }
    }

    #[test]
    fn adds_the_first_arg_to_an_empty_call() {
        // `f()` → `f(<name>)` — no leading comma.
        for seed in 0..32u32 {
            if let Some(out) = run("f()\n", seed) {
                assert!(
                    out.starts_with("f(") && !out.starts_with("f(,"),
                    "seed {seed}: {out:?}"
                );
                assert!(out.len() > "f()\n".len(), "seed {seed}: not an insertion");
                assert!(
                    ruff_python_parser::parse_module(&out).is_ok(),
                    "seed {seed}: unparseable: {out:?}"
                );
            }
        }
    }

    #[test]
    fn no_calls_yields_nothing() {
        assert!(run("x = 1\n", 0).is_none());
    }
}

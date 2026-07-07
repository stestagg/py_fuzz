//! `NameSubstitution` — replace one identifier with a candidate identifier.
//!
//! Strategy (one edit per call):
//!   1. Walk the (possibly partial) AST and collect every identifier position:
//!      both `Name` expressions (variable/function references) and standalone
//!      [`Identifier`] nodes (attribute names in `x.attr`, keyword-argument
//!      names, `def`/`class` names, `import` aliases, parameter names, …).
//!   2. Pick one at random.
//!   3. Replace it with a candidate drawn from the compile-embedded `names.dict`,
//!      skipping any candidate equal to the identifier already there so the
//!      output actually changes.
//!
//! We use ruff's *source-order* visitor here (not the plain `Visitor`) because
//! only it descends into `Identifier` nodes; the plain visitor stops at
//! expressions and never yields e.g. the `attr` of an attribute access.

use ruff_python_ast::visitor::source_order::{walk_expr, walk_module, SourceOrderVisitor};
use ruff_python_ast::{Expr, Identifier};
use ruff_text_size::TextRange;

use crate::mutators::{load_name_dict, AstCtx, Edit, SubMutator};
use crate::rng::Rng;

/// Source-order visitor recording the byte range of every identifier position:
/// `Name` expressions plus bare [`Identifier`] nodes (attributes, keyword args,
/// def/class names, aliases, parameters, …).
struct NameCollector {
    ranges: Vec<TextRange>,
}

impl<'a> SourceOrderVisitor<'a> for NameCollector {
    fn visit_expr(&mut self, expr: &'a Expr) {
        if let Expr::Name(name) = expr {
            self.ranges.push(name.range);
        }
        walk_expr(self, expr);
    }

    fn visit_identifier(&mut self, identifier: &'a Identifier) {
        self.ranges.push(identifier.range);
    }
}

pub struct NameSubstitution {
    candidates: Vec<&'static str>,
}

impl NameSubstitution {
    pub fn new() -> Self {
        Self {
            candidates: load_name_dict(),
        }
    }
}

impl Default for NameSubstitution {
    fn default() -> Self {
        Self::new()
    }
}

impl SubMutator for NameSubstitution {
    fn name(&self) -> &'static str {
        "name_subst"
    }

    fn mutate(&self, ctx: &AstCtx, rng: &mut Rng) -> Option<Edit> {
        let mut collector = NameCollector { ranges: Vec::new() };
        walk_module(&mut collector, ctx.module);

        // Pick one Name node.
        let range = *rng.choose(&collector.ranges)?;
        let start = usize::from(range.start());
        let end = usize::from(range.end());
        let current = &ctx.source[start..end];

        // Pick a candidate different from what's already there. Start at a random
        // offset and scan so the choice is seeded but we never emit a no-op.
        let n = self.candidates.len();
        let start_idx = rng.index(n)?;
        for k in 0..n {
            let cand = self.candidates[(start_idx + k) % n];
            if cand != current {
                return Some(Edit {
                    range,
                    kind: "name_subst",
                    replacement: cand.to_string(),
                });
            }
        }
        // Every candidate equals the current text (degenerate dict) — no-op.
        None
    }
}

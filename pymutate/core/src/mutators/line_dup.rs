//! `LineDup` — repeat a line, or a whole statement.
//!
//! Duplicating code cheaply creates redefinition, double-`return`, repeated
//! side-effects and dead code — inputs that stress the compiler's and
//! interpreter's handling of rebinding and repeated execution.
//!
//! Two flavours, one flat candidate space, one chosen per call:
//!
//!   1. **Raw line** (pure text, no AST): pick a non-blank physical line and
//!      insert a verbatim copy right after it. Simple and robust; it may bisect a
//!      multi-line statement (a triple-quoted string, a bracketed continuation),
//!      which is fine — a `SyntaxError` is cheap to reject.
//!
//!   2. **Whole statement** (AST): pick a statement and insert a copy of its full
//!      source as a sibling at the same indentation. For a compound statement
//!      (`def`/`for`/`if`) that duplicates the entire block, header and body.
//!
//! Both are pure insertions (zero-width range).

use ruff_python_ast::visitor::{walk_stmt, Visitor};
use ruff_python_ast::Stmt;
use ruff_text_size::{Ranged, TextRange, TextSize};

use crate::mutators::{line_indent, walk_module, AstCtx, Edit, SubMutator};
use crate::rng::Rng;

/// Byte ranges `[start, end)` of every non-blank physical line, `end` including
/// the trailing `\n` when present.
fn nonblank_lines(source: &str) -> Vec<(usize, usize)> {
    let bytes = source.as_bytes();
    let mut lines = Vec::new();
    let mut start = 0usize;
    for (i, &b) in bytes.iter().enumerate() {
        if b == b'\n' {
            if !source[start..i].trim().is_empty() {
                lines.push((start, i + 1)); // include the newline
            }
            start = i + 1;
        }
    }
    // Trailing line without a newline.
    if start < bytes.len() && !source[start..].trim().is_empty() {
        lines.push((start, bytes.len()));
    }
    lines
}

/// Preorder visitor recording every statement range (incl. nested ones).
struct StmtCollector {
    ranges: Vec<TextRange>,
}

impl<'a> Visitor<'a> for StmtCollector {
    fn visit_stmt(&mut self, stmt: &'a Stmt) {
        self.ranges.push(stmt.range());
        walk_stmt(self, stmt);
    }
}

/// Statement sites usable for duplication, each paired with its indent so the
/// copy stays aligned. Statements that don't lead their line are dropped — see
/// [`line_indent`].
fn stmt_sites<'a>(ctx: &AstCtx<'a>) -> Vec<(TextRange, &'a str)> {
    let mut collector = StmtCollector { ranges: Vec::new() };
    walk_module(&mut collector, ctx.module);
    collector
        .ranges
        .iter()
        .filter_map(|r| line_indent(ctx.source, usize::from(r.start())).map(|ind| (*r, ind)))
        .collect()
}

pub struct LineDup;

impl LineDup {
    pub fn new() -> Self {
        LineDup
    }
}

impl Default for LineDup {
    fn default() -> Self {
        Self::new()
    }
}

impl SubMutator for LineDup {
    fn name(&self) -> &'static str {
        "line_dup"
    }

    fn edit_space(&self, ctx: &AstCtx) -> usize {
        // The copy is the source text itself, so each site yields one edit.
        nonblank_lines(ctx.source).len() + stmt_sites(ctx).len()
    }

    fn mutate(&self, ctx: &AstCtx, rng: &mut Rng) -> Option<Edit> {
        let src = ctx.source;
        let lines = nonblank_lines(src);
        let stmts = stmt_sites(ctx);

        // One flat candidate space across both flavours; pick uniformly.
        let idx = rng.index(lines.len() + stmts.len())?;
        if idx < lines.len() {
            // Raw-line flavour.
            let (start, end) = lines[idx];
            let has_newline = src.as_bytes().get(end - 1) == Some(&b'\n');
            let replacement = if has_newline {
                src[start..end].to_string()
            } else {
                // Last line without a newline: add one before the copy.
                format!("\n{}", &src[start..end])
            };
            Some(Edit {
                range: TextRange::empty(TextSize::from(end as u32)),
                kind: "line_dup",
                replacement,
            })
        } else {
            // Statement flavour.
            let (range, indent) = stmts[idx - lines.len()];
            let text = &src[usize::from(range.start())..usize::from(range.end())];
            Some(Edit {
                range: TextRange::empty(range.end()),
                kind: "line_dup",
                replacement: format!("\n{indent}{text}"),
            })
        }
    }
}

#[cfg(test)]
mod tests {
    /// Run only `line_dup` over `src` for one seed, returning the output.
    fn run(src: &str, seed: u32) -> Option<String> {
        let out = crate::mutate_with(src.as_bytes(), seed, 1 << 20, &["line_dup"])?;
        Some(String::from_utf8(out).unwrap())
    }

    #[test]
    fn always_grows_the_input() {
        for seed in 0..32u32 {
            if let Some(out) = run("x = 1\n", seed) {
                assert!(
                    out.len() > "x = 1\n".len(),
                    "seed {seed}: not additive: {out:?}"
                );
            }
        }
    }

    #[test]
    fn duplicates_a_compound_statement() {
        // The statement flavour copies the whole `def` block; across seeds the
        // header should appear twice at least once.
        let hit = (0..64u32).any(|seed| {
            run("def f():\n    pass\n", seed).is_some_and(|s| s.matches("def f():").count() >= 2)
        });
        assert!(hit, "expected the whole def to be duplicated across seeds");
    }

    #[test]
    fn blank_only_input_yields_nothing() {
        // No non-blank lines and no statements to duplicate.
        assert!(run("   \n\n", 0).is_none());
    }
}

//! `DelInsert` — drop a `del <target>` next to an assignment.
//!
//! Deleting a name right after (or before) it is bound churns reference counts
//! and turns later uses into `NameError` / use-after-`del` paths — cheap bait for
//! ref-count and lifetime bugs in the interpreter.
//!
//! Strategy (one edit per call):
//!   1. Walk the AST for assignment statements — `Assign` (each of its targets),
//!      `AnnAssign`, `AugAssign` — recording the statement's range and the
//!      target's range.
//!   2. Pick one, and one of two placements, and splice in a sibling `del`
//!      statement at the assignment's indentation:
//!        - **after:**  `x = 1`  →  `x = 1` / `del x`
//!        - **before:** `x = 1`  →  `del x` / `x = 1`
//!
//! The deleted operand is just the target's source text, so tuple (`del a, b`),
//! attribute (`del x.a`) and subscript (`del x[0]`) targets all come out valid
//! for free. Sites whose statement isn't the first thing on its line (inline
//! bodies like `if c: x = 1`) are skipped — see [`crate::mutators::line_indent`].

use ruff_python_ast::visitor::{walk_stmt, Visitor};
use ruff_python_ast::Stmt;
use ruff_text_size::{Ranged, TextRange};

use crate::mutators::{line_indent, walk_module, AstCtx, Edit, SubMutator};
use crate::rng::Rng;

/// The two placements, indexed by the low bit of the candidate index.
#[derive(Clone, Copy)]
enum Placement {
    Before,
    After,
}
const PLACEMENTS: [Placement; 2] = [Placement::Before, Placement::After];

/// A collected assignment site: the statement's range and its target's range.
#[derive(Clone, Copy)]
struct AssignSite {
    stmt: TextRange,
    target: TextRange,
}

/// Preorder visitor recording every assignment target (incl. nested ones).
struct AssignCollector {
    sites: Vec<AssignSite>,
}

impl<'a> Visitor<'a> for AssignCollector {
    fn visit_stmt(&mut self, stmt: &'a Stmt) {
        match stmt {
            Stmt::Assign(a) => {
                for t in &a.targets {
                    self.sites.push(AssignSite {
                        stmt: stmt.range(),
                        target: t.range(),
                    });
                }
            }
            Stmt::AnnAssign(a) => self.sites.push(AssignSite {
                stmt: stmt.range(),
                target: a.target.range(),
            }),
            Stmt::AugAssign(a) => self.sites.push(AssignSite {
                stmt: stmt.range(),
                target: a.target.range(),
            }),
            _ => {}
        }
        walk_stmt(self, stmt);
    }
}

pub struct DelInsert;

impl DelInsert {
    pub fn new() -> Self {
        DelInsert
    }
}

impl Default for DelInsert {
    fn default() -> Self {
        Self::new()
    }
}

impl SubMutator for DelInsert {
    fn name(&self) -> &'static str {
        "del_insert"
    }

    fn mutate(&self, ctx: &AstCtx, rng: &mut Rng) -> Option<Edit> {
        let mut collector = AssignCollector { sites: Vec::new() };
        walk_module(&mut collector, ctx.module);

        // Keep only sites whose statement leads its line, pairing each with its
        // indent so the spliced `del` lines up. (See `line_indent`.)
        let usable: Vec<(AssignSite, &str)> = collector
            .sites
            .iter()
            .filter_map(|s| {
                line_indent(ctx.source, usize::from(s.stmt.start())).map(|ind| (*s, ind))
            })
            .collect();

        // One flat candidate space: each usable site offers before/after.
        let idx = rng.index(usable.len() * PLACEMENTS.len())?;
        let (site, indent) = usable[idx / PLACEMENTS.len()];
        let placement = PLACEMENTS[idx % PLACEMENTS.len()];

        let target =
            &ctx.source[usize::from(site.target.start())..usize::from(site.target.end())];

        let (range, replacement) = match placement {
            Placement::After => (
                TextRange::empty(site.stmt.end()),
                format!("\n{indent}del {target}"),
            ),
            Placement::Before => (
                TextRange::empty(site.stmt.start()),
                format!("del {target}\n{indent}"),
            ),
        };

        Some(Edit {
            range,
            kind: "del_insert",
            replacement,
        })
    }
}

#[cfg(test)]
mod tests {
    /// Run only `del_insert` over `src` for one seed, returning the output.
    fn run(src: &str, seed: u32) -> Option<String> {
        let out = crate::mutate_with(src.as_bytes(), seed, 1 << 20, &["del_insert"])?;
        Some(String::from_utf8(out).unwrap())
    }

    #[test]
    fn inserts_a_del_and_stays_parseable() {
        let mut saw_before = false;
        let mut saw_after = false;
        for seed in 0..64u32 {
            if let Some(out) = run("x = 1\n", seed) {
                assert!(out.contains("del x"), "seed {seed}: no del: {out:?}");
                if out.starts_with("del x") {
                    saw_before = true;
                } else {
                    saw_after = true;
                }
                assert!(
                    ruff_python_parser::parse_module(&out).is_ok(),
                    "seed {seed}: unparseable: {out:?}"
                );
            }
        }
        assert!(saw_before, "expected a before-placement across seeds");
        assert!(saw_after, "expected an after-placement across seeds");
    }

    #[test]
    fn tuple_target_deletes_the_whole_target() {
        // `a, b = 1, 2` → the del uses the target's source text: `del a, b`.
        let hit = (0..64u32).any(|seed| run("a, b = 1, 2\n", seed).is_some_and(|s| s.contains("del a, b")));
        assert!(hit, "expected `del a, b` for a tuple target");
    }

    #[test]
    fn keeps_indentation_of_nested_assignment() {
        // A `del` spliced next to an indented assignment must carry the indent.
        let hit = (0..64u32).any(|seed| {
            run("def f():\n    y = 1\n", seed).is_some_and(|s| s.contains("    del y"))
        });
        assert!(hit, "expected the spliced del to be indented to match");
    }

    #[test]
    fn no_assignments_yields_nothing() {
        assert!(run("print(x)\n", 0).is_none());
    }
}
